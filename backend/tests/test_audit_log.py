"""Integration tests for the audit log viewer (GET /orgs/{org_id}/audit-log).

Covers:
  - msp_admin only; every other role gets 403 (I.2 also applies but this is
    a stricter gate than the assessor-only rule — customer_poc/msp_engineer
    are blocked too, not just c3pao_assessor)
  - pagination: offset/limit, total count, newest-first ordering
  - filters: action (exact), actor (substring), ip_address (substring),
    created_at date range — each in isolation and combined
  - NULL ip_address never matches an active ip_address filter (rows
    predating migration 0022, or logged outside an HTTP request)
  - end-to-end IP capture: a real HTTP request through the actual
    middleware stack (not log_event() called directly) with a custom
    X-Real-IP header produces a row whose ip_address matches, proving the
    ContextVar plumbing in main.py/audit.py actually works under this
    codebase's sync `def` endpoints + TestClient, not just in theory

Run in-container:
    docker compose exec backend pytest tests/test_audit_log.py -m integration -v
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import AuditLog, Organization, User
from tests.conftest import _app_session, _authed, _make_fake_user


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_org(db_session, org_id: uuid.UUID) -> Organization:
    org = Organization(id=org_id, name=f"AuditLogOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


def _seed_user(db_session, *, org_id: uuid.UUID, **overrides) -> User:
    defaults = dict(
        org_id=org_id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Target User",
        login_method="local",
        role="customer_poc",
        is_active=False,
    )
    defaults.update(overrides)
    user = User(**defaults)
    db_session.add(user)
    db_session.flush()
    return user


def _seed_row(db_session, *, org_id: uuid.UUID, **overrides) -> AuditLog:
    defaults = dict(
        org_id=org_id,
        actor="00000000-0000-0000-0000-000000000001",
        actor_type="user",
        action="control_state.update",
        entity_type="control_state",
        entity_id=uuid.uuid4(),
        ip_address=None,
    )
    defaults.update(overrides)
    row = AuditLog(**defaults)
    db_session.add(row)
    db_session.flush()
    return row


# ---------------------------------------------------------------------------
# Authorization: msp_admin only
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("role", ["customer_poc", "msp_engineer", "c3pao_assessor"])
def test_non_msp_admin_403(db_session, role):
    org_id = uuid.uuid4()
    org = _seed_org(db_session, org_id)
    non_admin = _make_fake_user(org_id=org.id, role=role)

    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, non_admin)
    try:
        c = TestClient(app)
        assert c.get(f"/orgs/{org.id}/audit-log").status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
def test_msp_admin_can_read(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    _seed_row(db_session, org_id=org.id)

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.status_code == 200
    assert r.json()["total"] == 1


# ---------------------------------------------------------------------------
# Pagination + ordering
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_newest_first_and_pagination(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    base = datetime.now(UTC) - timedelta(hours=10)
    for i in range(5):
        _seed_row(
            db_session,
            org_id=org.id,
            action=f"test.event.{i}",
            created_at=base + timedelta(minutes=i),
        )

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log?limit=2&offset=0")
    body = r.json()
    assert body["total"] == 5
    assert [i["action"] for i in body["items"]] == ["test.event.4", "test.event.3"]

    r2 = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log?limit=2&offset=2")
    assert [i["action"] for i in r2.json()["items"]] == ["test.event.2", "test.event.1"]


@pytest.mark.integration
def test_scoped_to_org(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    other_org = _seed_org(db_session, uuid.uuid4())
    _seed_row(db_session, org_id=org.id)
    _seed_row(db_session, org_id=other_org.id)

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.json()["total"] == 1


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_filter_by_action_exact_match(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    _seed_row(db_session, org_id=org.id, action="user.deactivate")
    _seed_row(db_session, org_id=org.id, action="user.unlock")

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log?action=user.unlock")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "user.unlock"


@pytest.mark.integration
def test_filter_by_actor_substring(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    target_actor = str(uuid.uuid4())
    _seed_row(db_session, org_id=org.id, actor=target_actor)
    _seed_row(db_session, org_id=org.id, actor="system")

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log?actor={target_actor[:8]}")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["actor"] == target_actor


@pytest.mark.integration
def test_filter_by_date_range(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    now = datetime.now(UTC)
    old_row = _seed_row(
        db_session, org_id=org.id, action="test.old", created_at=now - timedelta(days=10)
    )
    recent_row = _seed_row(
        db_session, org_id=org.id, action="test.recent", created_at=now - timedelta(hours=1)
    )

    start = (now - timedelta(days=1)).isoformat()
    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log?start={start}")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "test.recent"

    end = (now - timedelta(days=5)).isoformat()
    r2 = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log?end={end}")
    body2 = r2.json()
    assert body2["total"] == 1
    assert body2["items"][0]["action"] == "test.old"

    assert old_row.id != recent_row.id  # sanity: two distinct rows really were seeded


@pytest.mark.integration
def test_filter_by_ip_address_substring(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    _seed_row(db_session, org_id=org.id, action="test.matching_ip", ip_address="10.0.0.42")
    _seed_row(db_session, org_id=org.id, action="test.other_ip", ip_address="192.168.1.1")

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log?ip_address=10.0.0")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "test.matching_ip"


@pytest.mark.integration
def test_null_ip_never_matches_active_ip_filter(client, db_session, fake_msp_admin):
    """Rows predating migration 0022 (or logged outside an HTTP request)
    have ip_address IS NULL. An active ip_address filter must exclude them
    rather than surface them as if they matched — a false positive would be
    worse than the row being absent.
    """
    org = _seed_org(db_session, fake_msp_admin.org_id)
    _seed_row(db_session, org_id=org.id, action="test.no_ip_captured", ip_address=None)
    _seed_row(db_session, org_id=org.id, action="test.has_ip", ip_address="203.0.113.5")

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log?ip_address=203.0.113.5")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "test.has_ip"

    # Without the filter, both rows (including the NULL one) are visible —
    # NULL means "unknown", not "hidden".
    r_unfiltered = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    items = r_unfiltered.json()["items"]
    actions = {i["action"] for i in items}
    assert actions == {"test.no_ip_captured", "test.has_ip"}
    no_ip_item = next(i for i in items if i["action"] == "test.no_ip_captured")
    assert no_ip_item["ip_address"] is None


@pytest.mark.integration
def test_filters_combine_with_and(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    ip = "1.1.1.1"
    _seed_row(db_session, org_id=org.id, action="user.unlock", actor="admin-a", ip_address=ip)
    _seed_row(db_session, org_id=org.id, action="user.unlock", actor="admin-b", ip_address=ip)
    _seed_row(db_session, org_id=org.id, action="user.deactivate", actor="admin-a", ip_address=ip)

    r = client.get(
        f"/orgs/{fake_msp_admin.org_id}/audit-log?action=user.unlock&actor=admin-a"
    )
    body = r.json()
    assert body["total"] == 1


# ---------------------------------------------------------------------------
# End-to-end IP capture through the real middleware (not log_event() directly)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_real_request_captures_client_ip_via_middleware(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    locked_until = datetime.now(UTC) + timedelta(minutes=5)
    user = _seed_user(db_session, org_id=org.id, locked_until=locked_until)

    r = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users/{user.id}/unlock",
        headers={"X-Real-IP": "203.0.113.77"},
    )
    assert r.status_code == 200

    row = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "user.unlock", AuditLog.entity_id == user.id)
    ).one()
    assert row.ip_address == "203.0.113.77"

    listed = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log?ip_address=203.0.113.77")
    assert listed.json()["total"] == 1


@pytest.mark.integration
def test_log_event_called_directly_outside_a_request_has_null_ip(db_session, fake_msp_admin):
    """No ambient request/middleware in this path (log_event called directly,
    same as every other test in this file's _seed_row helper and every
    non-HTTP caller in the codebase) — ip_address must be NULL, not raise
    or silently reuse a stale value from a previous request's ContextVar.
    """
    from app.audit import log_event

    org = _seed_org(db_session, fake_msp_admin.org_id)
    entry = log_event(
        db_session,
        org_id=org.id,
        action="test.direct_call",
        entity_type="test",
        entity_id=uuid.uuid4(),
    )
    db_session.flush()
    assert entry.ip_address is None


def test_log_event_outside_any_request_never_raises_no_db_required():
    """No DB, no app, no ASGI request at all — the shape of a seed script,
    catalog-loading CLI command, or future background job calling
    log_event() directly. _current_ip is a ContextVar constructed with
    default=None (see audit.py), so .get() on it always succeeds even when
    .set() was never called anywhere in this process — it cannot raise
    LookupError. This is a plain unit test (no @pytest.mark.integration,
    no fixtures) specifically so it runs even where no Postgres is
    reachable, proving the audit writer can't crash a caller that has no
    HTTP context, rather than only asserting it via an integration test
    that needs a live DB to execute at all.
    """
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
    assert entry.ip_address is None
    assert session.added is entry
