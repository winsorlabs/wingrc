"""Connector registry (D.1: credential entry + test-connection only).

Each connector module exposes CONNECTOR: ConnectorSpec. routers/integrations.py
looks connectors up by key here rather than hard-coding Liongard — so a
second connector is "add a module + one registry entry," not a rework of
the router or the screen. The actual data-pull (`collect()` from
ROADMAP.md's item D) is D.2/D.3 and deliberately isn't part of this
interface yet — test_connection is the only capability D.1 needs.

Extended for the SMTP connector (D.3's outbound-email prerequisite) with
two additions, both backward-compatible (defaulted, so Liongard's own
`ConnectorSpec(...)` call site needed no change):

- `kind` — a display discriminator only, never consulted by
  routers/integrations.py's own logic (every connector goes through the
  identical CRUD/test-connection code regardless of kind). Liongard is a
  `"data_source"` (feeds compliance scope); SMTP is a `"notification"`
  (an outbound comms channel with no compliance content by design — see
  email_service.py's module docstring). The frontend uses this to decide
  which Administration section a connector's card renders under
  (Integrations vs. Email) without hardcoding connector keys.
- `optional_fields` — credential_fields names that may be submitted blank
  (config fields now carry their own `required` flag on ConfigField
  instead — see below). Liongard's two credential fields are both
  genuinely required, so this never came up before; SMTP's auth is
  legitimately optional (an internal relay may take unauthenticated mail
  from a trusted host), which the previous "every declared field is
  required" validation in routers/integrations.py's set_credential
  couldn't express at all.

Extended again (2026-09-14, live SMTP setup on wl-util-1) with structured
config field descriptors and a per-invocation test-connection input:

**Why config_fields changed from `tuple[str, ...]` to
`tuple[ConfigField, ...]`.** Jarrod configured SMTP2GO with
`encryption_mode: tls` against port 2525 and got
`[SSL: WRONG_VERSION_NUMBER]` — `tls` (implicit TLS) is wrong for that
port, which wants `starttls` (plaintext-then-upgrade). The connector's
error message was diagnosable; the form was not, because `config_fields`
carried names only and the form rendered every one of them as a bare text
input. `help_text` already explained the three modes correctly, but as a
single paragraph at the top of the credential drawer, not next to the
field where the choice is actually made. A field that can render itself —
a label, a type, and for a `select`, real options with human labels — is
the fix; `tls` typed into a text box next to a paragraph of prose is not
self-explanatory, a labeled dropdown reading "Implicit TLS — encrypted
from the first byte (port 465)" is.

This is deliberately a small, closed generalization, not a form
framework: four field types (text/number/select/boolean), one optional
per-option cross-field suggestion (see ConfigFieldOption.suggests), no
validation rules and no conditional visibility. `credential_fields`
(always password-type, always secret) is untouched — this only affects
the non-secret `config` JSON.

**Why TestConnectionFn gained `test_input`.** Requested directly: test-
connection should be able to send a real message, not just connect. The
generic interface needed one optional, connector-defined slot for a
per-invocation value (SMTP's is a recipient address; Liongard's test
takes none and ignores the parameter). Not a parameter list shaped around
SMTP — a single `str | None`, keyword-only, defaulted, so an existing
2-argument call (`test_connection(config, credential)`, as every prior
test in test_smtp_connector.py/test_liongard_connector.py already does)
keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ConnectorTestResult:
    ok: bool
    message: str


class TestConnectionFn(Protocol):
    def __call__(
        self, config: dict, credential: dict, test_input: str | None = None
    ) -> ConnectorTestResult: ...


@dataclass(frozen=True)
class ConfigFieldOption:
    """One choice in a `select`-type ConfigField."""

    value: str
    # What the option DOES, not just the raw enum value -- see
    # smtp.CONNECTOR's encryption_mode options for the worked example
    # this was written for (naming the typical port(s) for each mode is
    # exactly what would have made Jarrod's SMTP2GO misconfiguration
    # obvious at selection time instead of only in the error message).
    label: str
    # Field name -> value to pre-fill when this option is selected, but
    # ONLY if that field is currently blank -- never overwrites a value
    # someone already typed. This is a suggestion, not a constraint:
    # SMTP2GO's own alternate ports (2525 for starttls, 8025 for tls)
    # exist specifically to work around blocked standard ports, so
    # forcing a "matching" port would recreate the exact class of bug
    # this feature exists to prevent. Empty dict (the default) suggests
    # nothing.
    suggests: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ConfigField:
    """One field in a connector's non-secret `config` JSON, carrying
    enough for the form to render itself instead of assuming a bare text
    input for everything. See this module's own docstring for why."""

    name: str
    label: str
    # "text" | "number" | "select" | "boolean" -- a rendering hint only.
    # No connector-specific validation lives here (parse_settings-style
    # checks stay in the connector module, e.g. smtp.py's port-range/
    # encryption-mode validation) -- adding that here is exactly the
    # "form framework" scope creep this design deliberately avoids.
    type: str = "text"
    help_text: str | None = None
    required: bool = True
    # Only meaningful for type="select".
    options: tuple[ConfigFieldOption, ...] = ()


@dataclass(frozen=True)
class ConnectorSpec:
    key: str
    name: str
    config_fields: tuple[ConfigField, ...]
    # Field names expected in the encrypted credential JSON (e.g.
    # access_key_id, access_key_secret). Unchanged by the ConfigField
    # generalization above -- every credential field is a secret, always
    # rendered as a password input, so there was never a second type to
    # distinguish here the way config fields needed.
    credential_fields: tuple[str, ...]
    # Which credential_fields' value to take the last-4-char display hint
    # from (routers/integrations.py never returns the rest).
    hint_field: str
    test_connection: TestConnectionFn
    help_text: str
    # "data_source" (feeds compliance scope, e.g. Liongard) or
    # "notification" (outbound comms, e.g. SMTP) -- display grouping only.
    kind: str = "data_source"
    # Names from credential_fields above that set_credential may accept
    # blank (e.g. SMTP's username/password for an unauthenticated relay).
    # Config-field required-ness now lives on ConfigField.required
    # instead -- this set is scoped to credential_fields only.
    optional_fields: frozenset[str] = field(default_factory=frozenset)
    # None (the default): this connector's test_connection takes no extra
    # input (Liongard). A label string: the Administration screen shows
    # one optional text field with this label before running "Test
    # connection," and passes whatever's typed through as test_input --
    # e.g. SMTP's "Send a test message to (optional)". Connector-defined
    # so the frontend never hardcodes "recipient" as an SMTP concept.
    test_input_label: str | None = None


def _build_registry() -> dict[str, ConnectorSpec]:
    from . import liongard, smtp

    return {
        liongard.CONNECTOR.key: liongard.CONNECTOR,
        smtp.CONNECTOR.key: smtp.CONNECTOR,
    }


REGISTRY: dict[str, ConnectorSpec] = _build_registry()
