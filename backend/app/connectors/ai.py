"""AI provider connector -- moves the Anthropic API key out of the
ANTHROPIC_API_KEY environment variable and into the connector registry
(2026-09-19), so it's encrypted at rest via crypto.py, write-only over
the API, and configured through Administration exactly like Liongard and
SMTP already are.

**Why this is a product decision, not tidiness (Jarrod's framing,
recorded here so a future reader doesn't mistake this for a refactor):**
WinGRC is AGPL software other MSPs deploy. `.env.example` documented
`ANTHROPIC_API_KEY=` directly, which taught every reader of the reference
deployment "put your API keys in a plaintext file next to the code" --
the opposite of every other third-party credential in this product, all
of which already go through crypto.py's Fernet-encrypted,
IntegrationConnection-backed pattern. The AI key was the sole exception,
and it carries a billing exposure on top of the usual data one.

**Closes an explicitly-deferred item, not new scope.** docs/roadmap.md's
document-ingestion Done entry already named exactly what this would take
("an explicit-key AnthropicProvider constructor instead of the SDK's
implicit env read, a new paid test-completion action, and a decision
about whether Settings.ai_provider keeps existing or is replaced by 'is
there an integration_connection row for key=ai'") and deferred it as a
separate follow-up. This is that follow-up.

**Environment-variable fallback: dropped, not kept (2026-09-19,
decided here).** The real argument for keeping one -- a secret-manager or
air-gapped deployment injecting at the env layer -- doesn't actually need
it: Liongard and SMTP already require entering their credentials through
Administration with no env-var alternative, and an operator with a secret
manager can inject at the database layer (seed IntegrationConnection
directly, or script the same PUT this screen uses) exactly as easily.
Keeping a parallel env-var path would mean a precedence question (which
wins?), a UI that could show one value while a stale env var silently
overrides it, and a second way to misconfigure this that Liongard/SMTP
don't have. Dropped for the same reason those two were never given one:
one path, no ambiguity.

**Only "anthropic" is offered as a Provider choice.** config.py's own
comment (before this change) already named azure_openai/local as planned
but unimplemented -- get_ai_provider()'s registry has only ever
constructed AnthropicProvider. Rendering options that would 500 on
selection is worse than not offering them; help_text says the rest are
planned.

**Per-request resolution, no cache.** ai/__init__.py:get_ai_provider()
decrypts and reconstructs the provider on every call -- document
ingestion (the only caller) is a rare, human-initiated, already-slow
action (upload documents, wait several seconds for a completion); the
extra DB round-trip + Fernet decrypt is immeasurable next to that. Not
cached, and deliberately so: a cache needs an invalidation story (does
changing the credential in Administration take effect immediately, or
only after some TTL/restart?), and there's no real need driving one yet.
Revisit only if a second, higher-frequency caller appears -- don't build
a cache pre-emptively without a concrete invalidation requirement to
design against.

**run_completion() is the one place that actually calls the Anthropic
SDK** -- used by both this module's own test_connection (a minimal, cheap
real completion, mirroring connectors/smtp.py's "test_connection and the
real send both go through one function" discipline) and
ai/anthropic_.py:AnthropicProvider.complete() (the real ingestion path),
so the two can never report a given failure differently. Error
categorization (auth/rate-limit/network/model-not-found) is built from
the real anthropic SDK's exception hierarchy
(anthropic._exceptions, confirmed by reading the installed package's
source rather than guessed from memory or the published docs) -- never a
generic "failed", matching connectors/liongard.py's LiongardAPIError and
connectors/smtp.py's SmtpConnectionError precedent.

The `anthropic` import is lazy (inside run_completion(), not this
module's top level) so this module -- and therefore the whole connector
registry, which connectors/__init__.py:_build_registry() imports
unconditionally regardless of which connectors are actually configured --
stays importable on a deployment that hasn't installed the `[ai]` extras
at all. ai/anthropic_.py's own pre-existing "anthropic package not
installed" friendly error moved here for the same reason: this is now
the one place that actually does `import anthropic`.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ConfigField, ConfigFieldOption, ConnectorSpec, ConnectorTestResult

_DEFAULT_MODEL = "claude-sonnet-4-6"
# azure_openai / local: planned (see CLAUDE.md's BYO-AI note, config.py's
# former comment), not implemented -- offering them here would 500 on
# selection. Extend this set only alongside real construction logic in
# ai/__init__.py:get_ai_provider(), never on its own.
_SUPPORTED_PROVIDERS = frozenset({"anthropic"})
_TEST_MAX_TOKENS = 10


class AIConfigError(Exception):
    """The stored config/credential itself is unusable (unrecognized
    provider, no API key) -- nothing was even attempted. Mirrors
    connectors/smtp.py's SmtpConfigError."""


class AIConnectionError(Exception):
    """A specific, human-readable explanation of why a call to the AI
    provider failed -- auth, rate limit, network, or model unavailable --
    never a generic "failed". Matches connectors/smtp.py's
    SmtpConnectionError / connectors/liongard.py's LiongardAPIError."""


@dataclass(frozen=True)
class AISettings:
    provider: str
    model: str
    api_key: str


def parse_settings(config: dict, credential: dict) -> AISettings:
    """Validates and normalizes stored config/credential into a typed
    settings object -- called by both test_connection and
    ai/__init__.py:get_ai_provider() so the two can never drift on what
    "valid" means. Raises AIConfigError with a specific message for
    anything wrong.
    """
    provider = str(config.get("provider") or "anthropic").strip().lower()
    if provider not in _SUPPORTED_PROVIDERS:
        raise AIConfigError(
            f"AI provider {provider!r} is not implemented yet -- supported: "
            f"{sorted(_SUPPORTED_PROVIDERS)}."
        )
    # Blank/unset model defaults rather than errors -- "default to the
    # current value, let it be overridden" (the task's own instruction),
    # not a required field an admin must fill in to get today's behavior.
    model = str(config.get("model") or "").strip() or _DEFAULT_MODEL
    api_key = str(credential.get("api_key") or "").strip()
    if not api_key:
        raise AIConfigError("API key is not set.")
    return AISettings(provider=provider, model=model, api_key=api_key)


def run_completion(settings: AISettings, system: str, user: str, max_tokens: int) -> str:
    """The one place that actually calls the Anthropic SDK -- see this
    module's own docstring for why both test_connection and the real
    ingestion path (ai/anthropic_.py) go through here rather than each
    having their own copy.
    """
    try:
        import anthropic as _anthropic
    except ImportError as e:
        raise AIConnectionError(
            "anthropic package not installed. Run: pip install 'wingrc-backend[ai]'"
        ) from e

    client = _anthropic.Anthropic(api_key=settings.api_key)
    try:
        msg = client.messages.create(
            model=settings.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except _anthropic.AuthenticationError as e:
        raise AIConnectionError(
            "Anthropic rejected the API key (HTTP 401) -- check it's active and correctly entered."
        ) from e
    except _anthropic.PermissionDeniedError as e:
        raise AIConnectionError(
            f"Anthropic denied access (HTTP 403) -- this key may not have permission for "
            f"model {settings.model!r}, or for the API at all."
        ) from e
    except _anthropic.RateLimitError as e:
        raise AIConnectionError(
            "Anthropic rate-limited this request (HTTP 429) -- wait a moment and try again."
        ) from e
    except _anthropic.NotFoundError as e:
        raise AIConnectionError(
            f"Anthropic returned HTTP 404 -- model {settings.model!r} may not exist or "
            "isn't available to this account."
        ) from e
    except _anthropic.APITimeoutError as e:
        raise AIConnectionError("Timed out connecting to Anthropic.") from e
    except _anthropic.APIConnectionError as e:
        raise AIConnectionError(f"Could not reach Anthropic: {e}") from e
    except _anthropic.APIStatusError as e:
        # Catch-all for any other 4xx/5xx this module doesn't name
        # individually (e.g. BadRequestError, OverloadedError) -- still a
        # specific status code and Anthropic's own message, never a bare
        # "failed".
        raise AIConnectionError(f"Anthropic returned HTTP {e.status_code}: {e.message}") from e
    return msg.content[0].text


def _test_connection(
    config: dict, credential: dict, test_input: str | None = None
) -> ConnectorTestResult:
    """A minimal real completion call -- cheap (10 max_tokens) and short --
    not just a connectivity check, since there is no separate "connect"
    step for an HTTPS API call the way SMTP has one. test_input is unused
    (this connector takes no extra input; see CONNECTOR.test_input_label
    below), accepted only to match the shared TestConnectionFn signature.
    """
    try:
        settings = parse_settings(config, credential)
    except AIConfigError as e:
        return ConnectorTestResult(ok=False, message=str(e))

    try:
        run_completion(
            settings,
            "You are a connectivity test.",
            "Reply with exactly the word OK.",
            _TEST_MAX_TOKENS,
        )
    except AIConnectionError as e:
        return ConnectorTestResult(ok=False, message=str(e))

    return ConnectorTestResult(ok=True, message=f"Connected to Anthropic ({settings.model}).")


CONNECTOR = ConnectorSpec(
    key="ai",
    name="AI Provider",
    config_fields=(
        ConfigField(
            name="provider",
            label="Provider",
            type="select",
            options=(ConfigFieldOption(value="anthropic", label="Anthropic"),),
            help_text=(
                "Azure OpenAI (GCC High) and a local-model backend are planned but not "
                "implemented yet."
            ),
        ),
        ConfigField(
            name="model",
            label="Model",
            required=False,
            help_text=(
                f"Defaults to {_DEFAULT_MODEL} if left blank -- override for a cheaper or "
                "more capable tier."
            ),
        ),
    ),
    credential_fields=("api_key",),
    hint_field="api_key",
    test_connection=_test_connection,
    help_text=(
        "Used for AI-assisted document ingestion (baseline extraction from vendor CRMs/"
        "PDFs). Generate an API key from your Anthropic Console. CUI-sensitive deployments: "
        "confirm this meets your data-handling requirements before enabling -- see "
        "CLAUDE.md's BYO-AI note."
    ),
    kind="ai",
)
