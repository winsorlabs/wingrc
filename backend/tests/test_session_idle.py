"""Integration tests for I.4 — session inactivity timeout (3.1.11).

Exercises the real cookie-session path (get_current_user -> _resolve_session
-> auth.resolve_session) rather than the fake-user dependency-override bypass
used elsewhere, since the idle window lives in that resolution path.

Covers, per docs/PLAN-auth-rbac-completion.md I.4:
  - a session with a recent last_activity_at resolves
  - a session past the idle window 401s even though expires_at is still in
    the future
  - activity within the window extends the session
  - a continuously-active session still terminates at absolute expires_at
    (idle timeout must not become a renewal mechanism)

What these tests cannot prove: whether the last_activity_at heartbeat
actually survives session.close() on a real GET-only request in production.
tests/conftest.py's db_session fixture runs with
join_transaction_mode="create_savepoint", so an in-request db.commit() only
releases a SAVEPOINT — the real top-level transaction never ends mid-test,
and a later same-session read sees the write regardless of whether the
commit()+re-SET-LOCAL fix in _resolve_session is present or not. That gap is
real and known (see _resolve_session's comments) — confirm the fix out of
band on wl-util-1: log in, issue only GET requests spaced more than 60s
apart across longer than session_idle_minutes, confirm the session survives.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import create_session
from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Organization, User
from tests.conftest import _app_session


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = _app_session(db_session)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_user(db_session) -> User:
    org = Organization(name=f"IdleTestOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    user = User(
        home_org_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Idle Test User",
        login_method="local",
        role="customer_poc",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _seeded_session(db_session, user, *, last_activity_at, expires_at):
    """Mint a real session row via the real create_session(), then force its
    last_activity_at/expires_at to specific values for the scenario under test."""
    session_row, raw = create_session(db_session, user)
    db_session.flush()
    session_row.last_activity_at = last_activity_at
    session_row.expires_at = expires_at
    db_session.flush()
    return session_row, raw


# ---------------------------------------------------------------------------
# Recent activity resolves
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_recent_activity_resolves(client, db_session):
    user = _seed_user(db_session)
    now = datetime.now(UTC)
    _row, raw = _seeded_session(
        db_session,
        user,
        last_activity_at=now - timedelta(minutes=1),
        expires_at=now + timedelta(hours=8),
    )
    client.cookies.set("wingrc_session", raw)
    r = client.get("/auth/me")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Past the idle window 401s even though expires_at is still in the future
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_past_idle_window_401s_even_with_future_expiry(client, db_session):
    user = _seed_user(db_session)
    idle_minutes = get_settings().session_idle_minutes
    now = datetime.now(UTC)
    _row, raw = _seeded_session(
        db_session,
        user,
        last_activity_at=now - timedelta(minutes=idle_minutes + 5),
        expires_at=now + timedelta(hours=8),  # nowhere near absolute expiry
    )
    client.cookies.set("wingrc_session", raw)
    r = client.get("/auth/me")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Activity within the window extends the session
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_activity_within_window_extends_session(client, db_session):
    user = _seed_user(db_session)
    now = datetime.now(UTC)
    row, raw = _seeded_session(
        db_session,
        user,
        # Older than the 60s throttle window so the heartbeat write fires,
        # but well inside the idle window so this request still resolves.
        last_activity_at=now - timedelta(minutes=5),
        expires_at=now + timedelta(hours=8),
    )
    client.cookies.set("wingrc_session", raw)

    r1 = client.get("/auth/me")
    assert r1.status_code == 200

    db_session.refresh(row)
    bumped_at = row.last_activity_at
    assert bumped_at > now - timedelta(minutes=1), (
        "last_activity_at should have been bumped to ~now by the throttled heartbeat"
    )

    # Push it stale again, short of the idle window, and confirm a second
    # request still resolves off the bumped timestamp rather than the
    # original (pre-request) one — i.e. the bump actually extended the
    # session rather than being a one-off no-op.
    row.last_activity_at = bumped_at - timedelta(minutes=1)
    db_session.flush()
    r2 = client.get("/auth/me")
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Absolute expires_at still terminates a continuously-active session —
# idle timeout must not become a renewal mechanism. Most likely case to get
# missed; do not skip it.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_continuous_activity_still_terminates_at_absolute_expiry(client, db_session):
    user = _seed_user(db_session)
    now = datetime.now(UTC)
    _row, raw = _seeded_session(
        db_session,
        user,
        last_activity_at=now,  # as fresh as activity can be
        expires_at=now - timedelta(seconds=1),  # already past absolute expiry
    )
    client.cookies.set("wingrc_session", raw)
    r = client.get("/auth/me")
    assert r.status_code == 401
