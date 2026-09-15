"""AI provider factory.

Resolves the configured provider from the connector registry
(IntegrationConnection, connector_key="ai") -- not an environment
variable (2026-09-19; see connectors/ai.py's module docstring for the
full decision, including why the env-var path was dropped rather than
kept as a fallback).

Per-request resolution, deliberately not cached: see connectors/ai.py's
own docstring for why a fresh decrypt + provider construction on every
call is fine at document ingestion's call frequency, and what would need
deciding (invalidation) before adding one.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .base import AIProvider


def get_ai_provider(session: Session) -> AIProvider:
    """Look up the "ai" connector's stored credential and construct the
    matching AIProvider. Three states, each reported distinctly (never
    collapsed into one message):

      1. No row, or a row with no credential at all (never configured, or
         a credential that was removed) -> NullProvider. Its own
         RuntimeError on .complete() already names Administration -> AI
         Provider as the fix (see ai/none_.py) -- this is the exact
         degradation importers/document.py already normalizes into a
         clean 422, unchanged by this module's move off env vars.
      2. A credential exists but can't be decrypted (a broken/rotated-away
         WINGRC_CREDENTIAL_ENCRYPTION_KEYS) -> CredentialCipherError,
         left to propagate uncaught -- callers (routers/admin_products.py)
         decide how to report a deployment-level key problem, matching
         how routers/integrations.py's own set_credential/test_connection
         already treat this exact failure mode.
      3. A credential decrypts fine but the stored provider/model/key
         combination is invalid (connectors/ai.py:parse_settings) ->
         RuntimeError naming the specific problem.

    A fourth state -- a credential that resolves to a real AnthropicProvider
    but then fails *at call time* (bad key, rate limit, network, model not
    found) -- isn't this function's concern at all: that's
    AnthropicProvider.complete() raising its own specific RuntimeError
    when the real ingestion call happens, via connectors/ai.py's error
    categorization. Distinct from all three states above by construction,
    not by extra code here.
    """
    from ..crypto import decrypt_credential
    from ..models import IntegrationConnection

    row = session.scalars(
        select(IntegrationConnection).where(IntegrationConnection.connector_key == "ai")
    ).first()
    if row is None or row.encrypted_credential is None:
        from .none_ import NullProvider

        return NullProvider()

    # CredentialCipherError deliberately not caught here -- see this
    # function's own docstring, state 2.
    credential = json.loads(decrypt_credential(row.encrypted_credential))

    from ..connectors.ai import AIConfigError, parse_settings

    try:
        settings = parse_settings(row.config or {}, credential)
    except AIConfigError as e:
        raise RuntimeError(str(e)) from e

    if settings.provider == "anthropic":
        from .anthropic_ import AnthropicProvider

        return AnthropicProvider(api_key=settings.api_key, model=settings.model)

    # Unreachable today -- parse_settings() already rejects anything
    # outside connectors/ai.py's _SUPPORTED_PROVIDERS before this point.
    # Kept as a loud failure, not silently falling through, in case that
    # set ever grows without the matching construction branch being added
    # here too.
    raise RuntimeError(f"AI provider {settings.provider!r} has no construction path configured.")
