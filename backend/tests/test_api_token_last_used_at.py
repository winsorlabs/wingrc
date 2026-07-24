"""Integration test for I.6 — api_token.last_used_at persistence.

_resolve_api_token had the same bug I.4 found and fixed for
user_session.last_activity_at: the `UPDATE api_token SET last_used_at = ...`
was a bare db.execute() with no db.commit(), and get_session()'s
`finally: session.close()` rolls back anything uncommitted — so a
Bearer-token request that never hits a mutating endpoint (a read-only
integration, or any token minted at c3pao_assessor) never actually
recorded last_used_at. Fixed with the same shape as _resolve_session's
activity heartbeat: commit immediately after the UPDATE, then re-issue
SET LOCAL app.current_org since the commit ends the transaction it was
scoped to.

What this test can't prove: like test_session_idle.py, it can't show the
fix survives session.close() in production — this test harness's session
never truly commits mid-test (join_transaction_mode="create_savepoint"),
so a same-session read sees the write regardless of whether the
commit()+re-SET-LOCAL fix is present. Confirm out of band on wl-util-1:
issue a real Bearer-token request against a token with last_used_at still
NULL, and confirm it's populated afterward.

Run in-container:
    docker compose exec backend pytest tests/test_api_token_last_used_at.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import ApiToken, Organization
from tests.conftest import _app_session, _authed


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_org(db_session, org_id: uuid.UUID) -> Organization:
    org = Organization(id=org_id, name=f"ApiTokenLastUsedOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org


@pytest.mark.integration
def test_api_token_last_used_at_set_after_authenticated_request(
    client, db_session, fake_msp_admin
):
    _seed_org(db_session, fake_msp_admin.org_id)

    created = client.post(
        f"/orgs/{fake_msp_admin.org_id}/users/api",
        json={"display_name": "CI Bot", "role": "customer_poc"},
    )
    assert created.status_code == 201
    token = created.json()["token"]
    user_id = uuid.UUID(created.json()["id"])

    token_row = db_session.scalars(
        select(ApiToken).where(ApiToken.user_id == user_id)
    ).one()
    assert token_row.last_used_at is None

    # Drop the fixture override so the token resolves through the real
    # get_current_user -> _resolve_api_token path instead of the test bypass.
    del app.dependency_overrides[get_current_user]

    r = client.get(
        f"/orgs/{fake_msp_admin.org_id}/users",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200

    db_session.refresh(token_row)
    assert token_row.last_used_at is not None
