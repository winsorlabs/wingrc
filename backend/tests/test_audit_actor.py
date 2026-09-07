"""Integration tests for the audit-actor ContextVar retrofit (2026-09-07
roadmap reconciliation finding: audit_log.actor defaulted to "system" on
every router except users.py/auth.py, even though the authenticated
identity was available).

Covers:
  - A representative mutating action on each router that previously
    defaulted to "system" now attributes to the real authenticated user:
    bundle.py (bundle.export — the sharpest case, since this is the record
    of who generated a package handed to a C3PAO), contacts.py, evidence.py,
    orgs.py, scope.py, and engine.py's deactivate_org_product (reached via
    routers/assessments.py, which has no log_event() calls of its own).
  - The API-token path: a request authenticated via a real Bearer token
    (not the _authed() test bypass) attributes to the user behind the
    token, not to the token itself or to "system" — and actor_type
    reflects that it's a login_method='api' service account.
  - A genuine system-context call (no request at all) still defaults to
    "system" — the ContextVar fallback, not an accidental blanket
    replacement of every "system" row.

Run in-container:
    docker compose exec backend pytest tests/test_audit_actor.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    Assessment,
    AssessmentObjective,
    AuditLog,
    Control,
    ControlState,
    EvidenceTask,
    EvidenceTaskStateLink,
    Framework,
    Organization,
    OrgProduct,
    Product,
)
from tests.conftest import _app_session, _authed, _grant

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _org(db_session, org_id: uuid.UUID) -> Organization:
    org = Organization(id=org_id, name=f"AuditActorOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _last_row(db_session, action: str, org_id: uuid.UUID) -> AuditLog:
    return db_session.scalars(
        select(AuditLog)
        .where(AuditLog.action == action, AuditLog.org_id == org_id)
        .order_by(AuditLog.created_at.desc())
    ).first()


# ---------------------------------------------------------------------------
# Per-router: representative mutating action attributes to the real user
# ---------------------------------------------------------------------------


def test_bundle_export_attributes_to_authenticated_user(client, db_session, fake_msp_admin):
    """The sharpest case: bundle.export is the record of who generated the
    package handed to a C3PAO — it must not say "system"."""
    org = _org(db_session, fake_msp_admin.org_id)
    _grant(db_session, fake_msp_admin)
    fw = Framework(key=f"fw-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    assessment = start_assessment(db_session, org.id, fw.id, "Actor Test")
    db_session.commit()

    r = client.get(f"/orgs/{org.id}/assessments/{assessment.id}/bundle")
    assert r.status_code == 200

    row = _last_row(db_session, "bundle.export", org.id)
    assert row is not None
    assert row.actor == str(fake_msp_admin.id)
    assert row.actor_type == "user"


def test_contacts_create_attributes_to_authenticated_user(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin.org_id)
    _grant(db_session, fake_msp_admin)

    r = client.post(
        f"/orgs/{org.id}/contacts",
        json={"name": "Jane Smith", "email": "jane@example.com", "affiliation": "msp"},
    )
    assert r.status_code == 201

    row = _last_row(db_session, "contact.create", org.id)
    assert row is not None
    assert row.actor == str(fake_msp_admin.id)
    assert row.actor_type == "user"


def test_evidence_collect_reference_attributes_to_authenticated_user(
    client, db_session, fake_msp_admin
):
    org = _org(db_session, fake_msp_admin.org_id)
    _grant(db_session, fake_msp_admin)
    fw = Framework(key=f"fw-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    ctrl = Control(
        framework_id=fw.id, control_id="AU.L2-3.3.1", family="AU", title="Audit",
        requirement_text="Audit logs.", sprs_weight=3, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Objective [a]")
    db_session.add(obj)
    db_session.flush()
    assessment = start_assessment(db_session, org.id, fw.id, "Actor Test")
    db_session.flush()
    cs = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id, ControlState.objective_id == obj.id
        )
    ).one()
    task = EvidenceTask(
        org_id=org.id, assessment_id=assessment.id, title="Export", artifact_type="export",
        status="open",
    )
    db_session.add(task)
    db_session.flush()
    db_session.add(EvidenceTaskStateLink(task_id=task.id, control_state_id=cs.id))
    db_session.commit()

    r = client.post(
        f"/orgs/{org.id}/assessments/{assessment.id}/evidence-tasks/{task.id}/collect/reference",
        json={"title": "Export link", "artifact_type": "export", "location": "https://example.com/x"},
    )
    assert r.status_code == 201

    row = _last_row(db_session, "evidence_task.collect", org.id)
    assert row is not None
    assert row.actor == str(fake_msp_admin.id)
    assert row.actor_type == "user"


def test_orgs_patch_profile_attributes_to_authenticated_user(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin.org_id)
    _grant(db_session, fake_msp_admin)

    r = client.patch(f"/orgs/{org.id}/profile", json={"industry": "Defense"})
    assert r.status_code == 200

    row = _last_row(db_session, "org.profile.update", org.id)
    assert row is not None
    assert row.actor == str(fake_msp_admin.id)
    assert row.actor_type == "user"


def test_scope_create_entity_attributes_to_authenticated_user(client, db_session, fake_msp_admin):
    org = _org(db_session, fake_msp_admin.org_id)
    _grant(db_session, fake_msp_admin)

    r = client.post(
        f"/orgs/{org.id}/scope",
        json={"entity_type": "device", "natural_key": "WS-ACTOR-TEST"},
    )
    assert r.status_code == 201

    row = _last_row(db_session, "scope_entity.create", org.id)
    assert row is not None
    assert row.actor == str(fake_msp_admin.id)
    assert row.actor_type == "user"


def test_assessments_deactivate_product_attributes_to_authenticated_user(
    client, db_session, fake_msp_admin
):
    """assessments.py has no log_event() calls of its own — this exercises
    engine.py's deactivate_org_product via the real HTTP endpoint, which is
    exactly the case the ContextVar mechanism exists for (no CurrentUser in
    scope inside engine.py itself)."""
    org = _org(db_session, fake_msp_admin.org_id)
    _grant(db_session, fake_msp_admin)
    fw = Framework(key=f"fw-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    product = Product(
        framework_id=fw.id, key=f"prod-{uuid.uuid4().hex[:6]}", name="Test Tool",
        provider="Test Inc", category="EDR", asset_type="SPA", role="Test product",
    )
    db_session.add(product)
    db_session.flush()
    assessment = start_assessment(db_session, org.id, fw.id, "Actor Test")
    db_session.flush()
    op = OrgProduct(org_id=org.id, product_id=product.id, status="active")
    db_session.add(op)
    db_session.commit()

    r = client.post(
        f"/orgs/{org.id}/assessments/{assessment.id}/products/{product.id}/deactivate"
    )
    assert r.status_code == 200

    row = _last_row(db_session, "org_product.deactivate", org.id)
    assert row is not None
    assert row.actor == str(fake_msp_admin.id)
    assert row.actor_type == "user"


# ---------------------------------------------------------------------------
# API-token path: attributes to the user behind the token, not the token
# itself and not "system" — actor_type reflects the service-account nature.
# ---------------------------------------------------------------------------


def test_api_token_authenticated_action_attributes_to_the_user_behind_the_token(
    client, db_session, fake_msp_admin
):
    org = _org(db_session, fake_msp_admin.org_id)
    _grant(db_session, fake_msp_admin)

    created = client.post(
        f"/orgs/{org.id}/users/api",
        json={"display_name": "CI Bot", "role": "msp_engineer"},
    )
    assert created.status_code == 201
    token = created.json()["token"]
    service_user_id = created.json()["id"]

    # Drop the fixture override so the token resolves through the real
    # get_current_user -> _resolve_api_token path instead of the test
    # bypass — same pattern as test_api_token_last_used_at.py.
    del app.dependency_overrides[get_current_user]

    r = client.post(
        f"/orgs/{org.id}/scope",
        json={"entity_type": "device", "natural_key": "WS-API-TOKEN-TEST"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201

    row = _last_row(db_session, "scope_entity.create", org.id)
    assert row is not None
    assert row.actor == service_user_id, (
        "must attribute to the service-account user behind the token, "
        "not the token itself and not the admin who minted it"
    )
    assert row.actor_type == "api"


# ---------------------------------------------------------------------------
# Genuine system-context actions: no request at all -> still "system",
# a deliberate fallback, not an artifact of forgetting to pass an actor.
# ---------------------------------------------------------------------------


def test_log_event_called_directly_outside_a_request_defaults_to_system(db_session):
    """Mirrors test_audit_log.py's IP-address equivalent. No ambient
    request/ContextVar in this path (log_event called directly from plain
    test-function code, not through TestClient) — actor/actor_type must
    fall back to "system", not raise or reuse a stale value from some
    earlier request's ContextVar (a separate TestClient request's inner
    async-task context can't leak into this bare call — proven empirically
    by the existing, already-passing IP equivalent)."""
    from app.audit import log_event

    org = Organization(name=f"AuditActorSystemOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()

    entry = log_event(
        db_session,
        org_id=org.id,
        action="test.direct_call",
        entity_type="test",
        entity_id=uuid.uuid4(),
    )
    db_session.flush()
    assert entry.actor == "system"
    assert entry.actor_type == "system"


def test_log_event_outside_any_request_never_raises_no_db_required():
    """No DB, no app, no ASGI request at all. _current_actor/
    _current_actor_type are ContextVars constructed with default=None (see
    audit.py), so .get() on them always succeeds even when .set() was
    never called anywhere in this process — mirrors the identical IP test
    in test_audit_log.py."""
    from app.audit import log_event

    class _RecordingSession:
        added = None

        def add(self, obj):
            self.added = obj

    session = _RecordingSession()
    entry = log_event(
        session,
        org_id=None,
        action="test.no_request_no_db",
        entity_type="test",
        entity_id=uuid.uuid4(),
    )
    assert entry.actor == "system"
    assert entry.actor_type == "system"
    assert session.added is entry


def test_explicit_system_actor_wins_over_a_set_contextvar(db_session):
    """A caller that means a real system-triggered action must be able to
    force actor="system" explicitly even if the ContextVar happens to be
    set (e.g. a background job invoked from within request-handling code
    that still has a stale identity in scope) -- explicit always takes
    precedence over the default, never silently overridden."""
    from app.audit import log_event, set_current_actor

    set_current_actor(str(uuid.uuid4()), "user")
    try:
        org = Organization(name=f"AuditActorExplicitOrg-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        entry = log_event(
            db_session,
            org_id=org.id,
            action="test.deliberate_system_action",
            entity_type="test",
            entity_id=uuid.uuid4(),
            actor="system",
            actor_type="system",
        )
        db_session.flush()
        assert entry.actor == "system"
        assert entry.actor_type == "system"
    finally:
        set_current_actor("", "")
