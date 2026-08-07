"""Integration tests for I.6 — concurrent session cap.

Confirms create_session() enforces WINGRC_MAX_SESSIONS_PER_USER by revoking
the oldest active sessions once a user exceeds the cap, that the default
(0) remains unlimited, and that lowering the cap after sessions already
exceed it self-heals on the next login rather than needing a one-time
cleanup.

Run in-container:
    docker compose exec backend pytest tests/test_session_cap.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.auth import create_session
from app.config import get_settings
from app.models import Organization, User, UserSession


@pytest.fixture
def _capped(monkeypatch):
    """Set WINGRC_MAX_SESSIONS_PER_USER for the duration of one test.

    monkeypatch.setenv auto-reverts the env var at teardown; the extra
    cache_clear() ensures the next test's get_settings() recomputes from
    the reverted environment rather than serving a stale cached Settings.
    """
    def _set(value: int) -> None:
        monkeypatch.setenv("WINGRC_MAX_SESSIONS_PER_USER", str(value))
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def _seed_user(db_session) -> User:
    org = Organization(name=f"SessionCapOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    user = User(
        home_org_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Session Cap User",
        login_method="local",
        role="customer_poc",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _active_sessions(db_session, user_id):
    return db_session.scalars(
        select(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.created_at.asc())
    ).all()


@pytest.mark.integration
def test_default_is_unlimited(db_session):
    assert get_settings().max_sessions_per_user == 0
    user = _seed_user(db_session)
    for _ in range(5):
        create_session(db_session, user)
        db_session.flush()
    assert len(_active_sessions(db_session, user.id)) == 5


@pytest.mark.integration
def test_cap_revokes_oldest_beyond_limit(db_session, _capped):
    _capped(3)
    user = _seed_user(db_session)
    rows = []
    for _ in range(5):
        row, _raw = create_session(db_session, user)
        db_session.flush()
        rows.append(row)

    active = _active_sessions(db_session, user.id)
    assert len(active) == 3
    # The 3 most recently created sessions survive; the 2 oldest were revoked.
    assert {r.id for r in active} == {rows[-1].id, rows[-2].id, rows[-3].id}
    assert rows[0].revoked_at is not None
    assert rows[1].revoked_at is not None


@pytest.mark.integration
def test_cap_is_self_healing_after_lowering(db_session, _capped):
    """Cap lowered after sessions already exceeded it: the next login
    trims down toward the new cap rather than needing a one-time cleanup."""
    _capped(0)
    user = _seed_user(db_session)
    for _ in range(4):
        create_session(db_session, user)
        db_session.flush()
    assert len(_active_sessions(db_session, user.id)) == 4

    _capped(2)
    create_session(db_session, user)
    db_session.flush()
    assert len(_active_sessions(db_session, user.id)) == 2
