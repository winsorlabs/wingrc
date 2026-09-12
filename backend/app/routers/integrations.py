"""Integration connector endpoints (D.1 — credential entry + test-connection
only; the data pull itself is D.2/D.3, not built here).

GET    /integrations                              Every registered connector + its state
PUT    /integrations/{connector_key}/credential    Set/replace a connector's credential
DELETE /integrations/{connector_key}/credential    Clear a connector's credential
POST   /integrations/{connector_key}/test          Test the stored credential live

Deployment-wide, not org-scoped (see models.py's IntegrationConnection
docstring for why — Liongard's own tenancy model is one API key per MSP
instance). Gated to msp_admin + consultant_admin (migration 0034),
router-wide: unlike most routers here there's no org_id to check
membership against.

consultant_admin's inclusion here is a deliberate compliance-data
classification, not a security one -- integration config feeds the magic
loop's scope population, so it's grouped with scope/assessments/evidence
rather than with users.py's identity-administration gate. Flagged, not
silently accepted: this router shares the exact deployment-wide-scope
property that got routers/objectives.py excluded (a consultant hired for
one client's engagement can reconfigure or clear a credential every other
client on this deployment depends on, same as objectives.py's practitioner
notes would be every client's shared catalog content). The task that added
consultant_admin explicitly named "integrations config" in scope, so this
follows that instruction rather than silently overriding it -- but the
tension is real and worth Jarrod revisiting if a multi-client MSP actually
hires a per-client consultant for this specific screen.

The credential is write-only over the API by design (see crypto.py + the
Hard rules in CLAUDE.md's parent doc): PUT accepts it, nothing ever returns
it. IntegrationOut carries at most credential_hint (last 4 chars) — no
route, log line, or exception here may ever surface the plaintext.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import require_role
from ..connectors import REGISTRY, ConnectorSpec
from ..crypto import CredentialCipherError, decrypt_credential, encrypt_credential
from ..db import get_session
from ..models import IntegrationConnection

router = APIRouter(
    prefix="/integrations",
    tags=["integrations"],
    dependencies=[Depends(require_role("msp_admin", "consultant_admin"))],
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class IntegrationOut(BaseModel):
    connector_key: str
    name: str
    configured: bool
    config: dict[str, str]
    credential_hint: str | None
    last_tested_at: datetime | None
    last_test_ok: bool | None
    last_test_error: str | None
    help_text: str
    config_fields: list[str]
    credential_fields: list[str]
    kind: str
    # config_fields/credential_fields entries that may be submitted blank
    # (e.g. SMTP's username/password for an unauthenticated relay) — the
    # frontend uses this to skip the "required" marker on those fields.
    optional_fields: list[str]


class IntegrationCredentialIn(BaseModel):
    config: dict[str, str] = {}
    credential: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_spec(connector_key: str) -> ConnectorSpec:
    spec = REGISTRY.get(connector_key)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Unknown connector {connector_key!r}")
    return spec


def _get_row(session: Session, connector_key: str) -> IntegrationConnection | None:
    return session.scalars(
        select(IntegrationConnection).where(IntegrationConnection.connector_key == connector_key)
    ).first()


def _out(spec: ConnectorSpec, row: IntegrationConnection | None) -> IntegrationOut:
    return IntegrationOut(
        connector_key=spec.key,
        name=spec.name,
        configured=row is not None and row.encrypted_credential is not None,
        config=(row.config if row else {}) or {},
        credential_hint=row.credential_hint if row else None,
        last_tested_at=row.last_tested_at if row else None,
        last_test_ok=row.last_test_ok if row else None,
        last_test_error=row.last_test_error if row else None,
        help_text=spec.help_text,
        config_fields=list(spec.config_fields),
        credential_fields=list(spec.credential_fields),
        kind=spec.kind,
        optional_fields=sorted(spec.optional_fields),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[IntegrationOut])
def list_integrations(session: Session = Depends(get_session)) -> list[IntegrationOut]:
    rows = {r.connector_key: r for r in session.scalars(select(IntegrationConnection)).all()}
    return [_out(spec, rows.get(spec.key)) for spec in REGISTRY.values()]


@router.put("/{connector_key}/credential", response_model=IntegrationOut)
def set_credential(
    connector_key: str,
    body: IntegrationCredentialIn,
    session: Session = Depends(get_session),
) -> IntegrationOut:
    spec = _get_spec(connector_key)

    missing = [
        f for f in spec.config_fields
        if f not in spec.optional_fields and not body.config.get(f, "").strip()
    ]
    missing += [
        f for f in spec.credential_fields
        if f not in spec.optional_fields and not body.credential.get(f, "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=400, detail=f"Missing required field(s): {', '.join(missing)}"
        )

    # .get(f, "") rather than bare indexing: an optional_fields entry may be
    # absent from the request body entirely (not just blank), and the
    # missing-field check above deliberately doesn't require it to be present.
    credential_payload = json.dumps({f: body.credential.get(f, "") for f in spec.credential_fields})
    try:
        ciphertext, key_version = encrypt_credential(credential_payload)
    except CredentialCipherError as e:
        # Fail closed: nothing is written if the encryption key itself is
        # missing or malformed. The exception message describes the key
        # config problem only — see crypto.py — never the credential.
        raise HTTPException(status_code=500, detail=str(e)) from e

    hint_source = body.credential.get(spec.hint_field, "")
    hint = hint_source[-4:] if hint_source else None

    row = _get_row(session, connector_key)
    is_new = row is None
    if row is None:
        row = IntegrationConnection(connector_key=connector_key)
        session.add(row)

    row.config = {f: body.config.get(f, "") for f in spec.config_fields}
    row.encrypted_credential = ciphertext
    row.credential_key_version = key_version
    row.credential_hint = hint
    # A new credential invalidates any prior test result — don't show a
    # stale "connected" status for a key that was just replaced.
    row.last_tested_at = None
    row.last_test_ok = None
    row.last_test_error = None
    session.flush()

    log_event(
        session,
        org_id=None,
        action="integration_connection.credential_set",
        entity_type="integration_connection",
        entity_id=row.id,
        after_value={
            "connector_key": connector_key,
            "config": row.config,
            "credential_hint": hint,
            "key_version": key_version,
        },
        context={"via": "api", "created": is_new},
    )
    session.commit()
    session.refresh(row)
    return _out(spec, row)


@router.delete("/{connector_key}/credential", status_code=204)
def delete_credential(connector_key: str, session: Session = Depends(get_session)) -> None:
    _get_spec(connector_key)  # 404s on an unknown connector before touching the row
    row = _get_row(session, connector_key)
    if row is None or row.encrypted_credential is None:
        raise HTTPException(status_code=404, detail="No credential configured for this connector")

    row.encrypted_credential = None
    row.credential_key_version = None
    row.credential_hint = None
    row.last_tested_at = None
    row.last_test_ok = None
    row.last_test_error = None

    log_event(
        session,
        org_id=None,
        action="integration_connection.credential_delete",
        entity_type="integration_connection",
        entity_id=row.id,
        before_value={"connector_key": connector_key},
        context={"via": "api"},
    )
    session.commit()


@router.post("/{connector_key}/test", response_model=IntegrationOut)
def test_connection(connector_key: str, session: Session = Depends(get_session)) -> IntegrationOut:
    spec = _get_spec(connector_key)
    row = _get_row(session, connector_key)
    if row is None or row.encrypted_credential is None:
        raise HTTPException(
            status_code=400, detail="No credential configured — add one before testing."
        )

    try:
        credential = json.loads(decrypt_credential(row.encrypted_credential))
    except CredentialCipherError:
        row.last_tested_at = datetime.now(UTC)
        row.last_test_ok = False
        row.last_test_error = (
            "Could not decrypt the stored credential — check the deployment's "
            "WINGRC_CREDENTIAL_ENCRYPTION_KEYS configuration."
        )
        session.flush()
        log_event(
            session,
            org_id=None,
            action="integration_connection.test",
            entity_type="integration_connection",
            entity_id=row.id,
            after_value={"connector_key": connector_key, "ok": False, "reason": "decrypt_failed"},
            context={"via": "api"},
        )
        session.commit()
        session.refresh(row)
        return _out(spec, row)

    result = spec.test_connection(row.config or {}, credential)
    row.last_tested_at = datetime.now(UTC)
    row.last_test_ok = result.ok
    row.last_test_error = None if result.ok else result.message
    session.flush()

    log_event(
        session,
        org_id=None,
        action="integration_connection.test",
        entity_type="integration_connection",
        entity_id=row.id,
        # ok only -- result.message on failure is Liongard's own HTTP-status
        # text (see connectors/liongard.py), never the credential, but is
        # still left out of the audit log to keep it to signal, matching
        # this module's "credential never in a log line" rule with margin.
        after_value={"connector_key": connector_key, "ok": result.ok},
        context={"via": "api"},
    )
    session.commit()
    session.refresh(row)
    return _out(spec, row)
