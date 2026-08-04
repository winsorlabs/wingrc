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
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert [i["action"] for i in body["items"]] == ["test.event.4", "test.event.3"]

    r2 = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log?limit=2&offset=2")
    assert r2.status_code == 200
    assert [i["action"] for i in r2.json()["items"]] == ["test.event.2", "test.event.1"]


@pytest.mark.integration
def test_scoped_to_org(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    other_org = _seed_org(db_session, uuid.uuid4())
    _seed_row(db_session, org_id=org.id)
    _seed_row(db_session, org_id=other_org.id)

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.status_code == 200
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
    assert r.status_code == 200
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
    assert r.status_code == 200
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

    # params=, not an f-string: isoformat()'s "+00:00" offset contains a
    # literal "+", which a naive f-string in a query string decodes as a
    # space server-side (application/x-www-form-urlencoded: "+" means
    # space; a literal "+" must be percent-encoded as "%2B" to survive).
    # httpx's params= dict handles that encoding correctly, matching what
    # the frontend's URLSearchParams does (see frontend/src/api.ts's
    # listAuditLog) — this was a real bug caught on real Postgres: the
    # corrupted "... 00:00" (space) value made Pydantic 422 the request,
    # and this test previously read body["total"] off that 422 body
    # without ever checking status_code first.
    start = (now - timedelta(days=1)).isoformat()
    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log", params={"start": start})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "test.recent"

    end = (now - timedelta(days=5)).isoformat()
    r2 = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log", params={"end": end})
    assert r2.status_code == 200
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
    assert r.status_code == 200
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
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "test.has_ip"

    # Without the filter, both rows (including the NULL one) are visible —
    # NULL means "unknown", not "hidden".
    r_unfiltered = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r_unfiltered.status_code == 200
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
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1


# ---------------------------------------------------------------------------
# Identity resolution — actor and entity_id (when entity_type == "user"),
# both resolved through the same three-way fallback chain (ADR 0006):
#   1. row exists, not anonymized -> display_name + email
#   2. row exists, deleted_at set (anonymized) -> "anonymized", no PII
#   3. row absent entirely (hard-deleted) -> "deleted"
# The GUID itself is always returned regardless of which branch fires — it
# is the durable record; the resolved name/email is a display convenience.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_actor_resolves_active_user(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    actor_user = _seed_user(
        db_session, org_id=org.id, display_name="Jarrod Winsor", email="jarrod.winsor@example.com"
    )
    _seed_row(db_session, org_id=org.id, actor=str(actor_user.id), actor_type="user")

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["actor"] == str(actor_user.id), "raw GUID must still be present, unchanged"
    assert item["actor_user"] == {
        "id": str(actor_user.id),
        "status": "active",
        "display_name": "Jarrod Winsor",
        "email": "jarrod.winsor@example.com",
    }


@pytest.mark.integration
def test_actor_resolves_anonymized_user(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    # Mirrors exactly what routers/users.py's anonymize_user leaves behind —
    # row survives, PII scrubbed, deleted_at set.
    actor_user = _seed_user(
        db_session,
        org_id=org.id,
        display_name="Deleted user",
        email=f"deleted-{uuid.uuid4()}@wingrc.invalid",
        deleted_at=datetime.now(UTC),
    )
    _seed_row(db_session, org_id=org.id, actor=str(actor_user.id), actor_type="user")

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["actor"] == str(actor_user.id)
    assert item["actor_user"]["status"] == "anonymized"
    assert item["actor_user"]["display_name"] is None, "must not surface the scrubbed placeholder"
    assert item["actor_user"]["email"] is None, "must not surface the scrubbed placeholder email"
    assert item["actor_user"]["id"] == str(actor_user.id)


@pytest.mark.integration
def test_actor_resolves_deleted_user_when_row_gone(client, db_session, fake_msp_admin):
    """No user row at all — the expected outcome of ADR 0006's zero-history
    hard-delete path, not a data-integrity bug. Must be labeled distinctly
    from "anonymized", not rendered as a bare orphan GUID.
    """
    org = _seed_org(db_session, fake_msp_admin.org_id)
    hard_deleted_id = uuid.uuid4()
    _seed_row(db_session, org_id=org.id, actor=str(hard_deleted_id), actor_type="user")

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["actor"] == str(hard_deleted_id)
    assert item["actor_user"] == {
        "id": str(hard_deleted_id),
        "status": "deleted",
        "display_name": None,
        "email": None,
    }


@pytest.mark.integration
def test_actor_system_literal_is_not_resolved(client, db_session, fake_msp_admin):
    """"system" isn't a GUID — no resolution attempted, actor_user is None
    rather than a false "deleted" (which would misleadingly imply a user
    row once existed for this action).
    """
    org = _seed_org(db_session, fake_msp_admin.org_id)
    _seed_row(db_session, org_id=org.id, actor="system", actor_type="system")

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["actor"] == "system"
    assert item["actor_user"] is None


@pytest.mark.integration
def test_entity_resolves_active_user(client, db_session, fake_msp_admin):
    """The gap Jarrod noticed: a user.deactivate row's entity_id is the
    user who was deactivated, and it must resolve the same way actor does.
    """
    org = _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(
        db_session, org_id=org.id, display_name="Target Person", email="target@example.com"
    )
    _seed_row(
        db_session,
        org_id=org.id,
        action="user.deactivate",
        entity_type="user",
        entity_id=target.id,
    )

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["entity_id"] == str(target.id)
    assert item["entity_user"] == {
        "id": str(target.id),
        "status": "active",
        "display_name": "Target Person",
        "email": "target@example.com",
    }


@pytest.mark.integration
def test_entity_resolves_anonymized_user(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    target = _seed_user(
        db_session,
        org_id=org.id,
        display_name="Deleted user",
        email=f"deleted-{uuid.uuid4()}@wingrc.invalid",
        deleted_at=datetime.now(UTC),
    )
    _seed_row(
        db_session, org_id=org.id, action="user.anonymize", entity_type="user", entity_id=target.id
    )

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["entity_id"] == str(target.id)
    assert item["entity_user"]["status"] == "anonymized"
    assert item["entity_user"]["display_name"] is None
    assert item["entity_user"]["email"] is None


@pytest.mark.integration
def test_entity_resolves_deleted_user_when_row_gone(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    hard_deleted_id = uuid.uuid4()
    _seed_row(
        db_session,
        org_id=org.id,
        action="user.delete",
        entity_type="user",
        entity_id=hard_deleted_id,
    )

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["entity_id"] == str(hard_deleted_id)
    assert item["entity_user"] == {
        "id": str(hard_deleted_id),
        "status": "deleted",
        "display_name": None,
        "email": None,
    }


@pytest.mark.integration
def test_entity_not_resolved_when_entity_type_is_not_user(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin.org_id)
    _seed_row(db_session, org_id=org.id, entity_type="control_state", entity_id=uuid.uuid4())

    r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["entity_user"] is None


@pytest.mark.integration
def test_identity_resolution_batches_into_one_query(client, db_session, fake_msp_admin, db_engine):
    """Five distinct users referenced across the page (mix of actor and
    entity_id) must resolve via exactly one SELECT against "user", not one
    per row/per GUID.
    """
    org = _seed_org(db_session, fake_msp_admin.org_id)
    users = [
        _seed_user(db_session, org_id=org.id, display_name=f"User {i}", email=f"u{i}@example.com")
        for i in range(5)
    ]
    for i, u in enumerate(users):
        if i % 2 == 0:
            _seed_row(db_session, org_id=org.id, actor=str(u.id), actor_type="user")
        else:
            _seed_row(db_session, org_id=org.id, entity_type="user", entity_id=u.id)

    from sqlalchemy import event

    captured: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if 'FROM "user"' in statement:
            captured.append(statement)

    event.listen(db_engine, "before_cursor_execute", _capture)
    try:
        r = client.get(f"/orgs/{fake_msp_admin.org_id}/audit-log")
    finally:
        event.remove(db_engine, "before_cursor_execute", _capture)

    assert r.status_code == 200
    assert r.json()["total"] == 5
    assert len(captured) == 1, (
        f"expected exactly one batch SELECT against \"user\", got {len(captured)}: {captured}"
    )
    # And every row actually got a real name, not a fallback — proving the
    # single query resolved all five distinct GUIDs, not just the first.
    #
    # Scoped to status == "active" and matched by GUID rather than swept
    # from both actor_user/entity_user indiscriminately: the odd-indexed
    # rows above only override entity_id, so their actor falls back to
    # _seed_row's default ("00000000-...-0001") — a GUID with no matching
    # user in this org, which correctly resolves to status "deleted" with
    # display_name None. A broader sweep would collect that None into the
    # set alongside the five real names and fail on an extra None entry —
    # that's correct fallback behavior, not a batching bug, so this
    # assertion isn't the place to also cover it (see
    # test_actor_resolves_deleted_user_when_row_gone for that case).
    items = r.json()["items"]
    resolved_by_id = {}
    for item in items:
        for identity in (item["actor_user"], item["entity_user"]):
            if identity and identity["status"] == "active":
                resolved_by_id[identity["id"]] = identity["display_name"]
    assert resolved_by_id == {str(u.id): u.display_name for u in users}


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
    assert listed.status_code == 200
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
