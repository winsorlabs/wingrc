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
- `optional_fields` — config_fields/credential_fields names that may be
  submitted blank. Liongard's two credential fields are both genuinely
  required, so this never came up before; SMTP's auth is legitimately
  optional (an internal relay may take unauthenticated mail from a
  trusted host), which the previous "every declared field is required"
  validation in routers/integrations.py's set_credential couldn't
  express at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ConnectorTestResult:
    ok: bool
    message: str


class TestConnectionFn(Protocol):
    def __call__(self, config: dict, credential: dict) -> ConnectorTestResult: ...


@dataclass(frozen=True)
class ConnectorSpec:
    key: str
    name: str
    # Field names expected in the non-secret `config` JSON (e.g. instance_url).
    config_fields: tuple[str, ...]
    # Field names expected in the encrypted credential JSON (e.g.
    # access_key_id, access_key_secret).
    credential_fields: tuple[str, ...]
    # Which credential_fields' value to take the last-4-char display hint
    # from (routers/integrations.py never returns the rest).
    hint_field: str
    test_connection: TestConnectionFn
    help_text: str
    # "data_source" (feeds compliance scope, e.g. Liongard) or
    # "notification" (outbound comms, e.g. SMTP) -- display grouping only.
    kind: str = "data_source"
    # Names from config_fields/credential_fields above that set_credential
    # may accept blank. Everything not listed here is still required.
    optional_fields: frozenset[str] = field(default_factory=frozenset)


def _build_registry() -> dict[str, ConnectorSpec]:
    from . import liongard, smtp

    return {
        liongard.CONNECTOR.key: liongard.CONNECTOR,
        smtp.CONNECTOR.key: smtp.CONNECTOR,
    }


REGISTRY: dict[str, ConnectorSpec] = _build_registry()
