"""An error nothing handled answers with a reference, and a deliberate
refusal still answers in its own words.

`main._unhandled_error` is registered for `Exception`. A broad handler on
the auth routes is the kind of change that could turn "Account
deactivated" (#137) back into something generic, so each deliberate
refusal is asserted here exactly, through a client that does not re-raise
server errors, as production does not.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

import app.auth as auth_module
import app.routers.auth as auth_router
from app.db import get_session
from app.main import UNHANDLED_DETAIL, app
from tests.conftest import _app_session
from tests.test_offboarding import PASSWORD, _user, org  # noqa: F401 -- org is a fixture

REF = re.escape(UNHANDLED_DETAIL).replace(re.escape("{ref}"), "([0-9a-f]{8})")


@pytest.fixture
def prod_client():
    auth_module._login_attempts.clear()
    yield TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    app.dependency_overrides.clear()


@pytest.fixture
def prod_db_client(db_session):
    auth_module._login_attempts.clear()
    app.dependency_overrides[get_session] = _app_session(db_session)
    yield TestClient(app, raise_server_exceptions=False, follow_redirects=False)
    app.dependency_overrides.clear()


def test_an_unhandled_error_answers_with_a_reference_and_logs_the_exception(
    prod_client, monkeypatch, caplog,
):
    def _boom(_ip):
        raise RuntimeError("internal detail that must not reach the browser")

    monkeypatch.setattr(auth_router, "check_login_rate_limit", _boom)
    # The suite's in-process alembic run (fileConfig) disables loggers that
    # already exist; production runs alembic in its own process.
    monkeypatch.setattr(logging.getLogger("wingrc.errors"), "disabled", False)
    with caplog.at_level(logging.ERROR, logger="wingrc.errors"):
        resp = prod_client.post("/auth/login", json={"email": "a@example.com", "password": "x"})

    assert resp.status_code == 500
    match = re.fullmatch(REF, resp.json()["detail"])
    assert match, resp.text
    assert "internal detail" not in resp.text
    record = next(r for r in caplog.records if r.name == "wingrc.errors")
    assert f"ref={match.group(1)}" in record.getMessage()
    assert "/auth/login" in record.getMessage()
    assert record.exc_info and "internal detail" in str(record.exc_info[1])


@pytest.mark.integration
def test_invalid_credentials_reaches_the_browser_as_it_is(prod_db_client):
    resp = prod_db_client.post(
        "/auth/login",
        json={"email": f"nobody-{uuid.uuid4().hex[:6]}@example.com", "password": PASSWORD},
    )
    assert (resp.status_code, resp.json()) == (401, {"detail": "Invalid credentials"})


@pytest.mark.integration
def test_account_deactivated_reaches_the_browser_as_it_is(prod_db_client, db_session, org):  # noqa: F811
    user = _user(db_session, org, is_active=False, deactivated_at=datetime.now(UTC))
    resp = prod_db_client.post("/auth/login", json={"email": user.email, "password": PASSWORD})
    assert (resp.status_code, resp.json()) == (403, {"detail": "Account deactivated"})


@pytest.mark.integration
def test_an_invalid_invite_token_reaches_the_browser_as_it_is(prod_db_client):
    resp = prod_db_client.post(
        "/auth/set-password", json={"token": uuid.uuid4().hex, "password": PASSWORD},
    )
    assert (resp.status_code, resp.json()) == (400, {"detail": "Invalid or expired invite token"})
