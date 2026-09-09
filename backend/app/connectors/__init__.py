"""Connector registry (D.1: credential entry + test-connection only).

Each connector module exposes CONNECTOR: ConnectorSpec. routers/integrations.py
looks connectors up by key here rather than hard-coding Liongard — so a
second connector is "add a module + one registry entry," not a rework of
the router or the screen. The actual data-pull (`collect()` from
ROADMAP.md's item D) is D.2/D.3 and deliberately isn't part of this
interface yet — test_connection is the only capability D.1 needs.
"""

from __future__ import annotations

from dataclasses import dataclass
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


def _build_registry() -> dict[str, ConnectorSpec]:
    from . import liongard

    return {liongard.CONNECTOR.key: liongard.CONNECTOR}


REGISTRY: dict[str, ConnectorSpec] = _build_registry()
