"""Enrolment refuses an account it must not touch.

Until 2026-11-17 `/auth/mfa/enroll` and `/auth/mfa/enroll/confirm` checked
only that the user row existed, so a forged pending cookie gave a session as
any user and replaced their authenticator (docs/roadmap.md, "The auth state
cookies were signed with a public key"). The pending cookie here is signed
with the real key on purpose: it stands for a defect in how one is issued,
which is what these refusals are for.

Each case is written out; none is read from `enrollment_refusal` itself.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient

from app.auth import make_state_payload, sign_state_cookie
from app.db import get_session
from app.main import app
from app.models import Organization, User
from tests.conftest import _app_session, _set_cookie_value

pytestmark = pytest.mark.integration

ORIGINAL_SECRET = pyotp.random_base32()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = _app_session(db_session)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _user(db_session, **overrides) -> User:
    org = Organization(name=f"EnrollRefusal-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    fields = dict(
        home_org_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Enrol Refusal User",
        login_method="local",
        role="msp_admin",
        is_active=False,
        mfa_enrolled=False,
    )
    fields.update(overrides)
    user = User(**fields)
    db_session.add(user)
    db_session.flush()
    return user


def _pending(user: User) -> str:
    return sign_state_cookie(make_state_payload({
        "user_id": str(user.id), "org_id": str(user.home_org_id), "phase": "enroll",
    }))


REFUSED = [
    ("already enrolled",
     dict(is_active=True, mfa_enrolled=True, totp_secret=ORIGINAL_SECRET),
     "MFA is already enrolled for this account"),
    ("removed", dict(deleted_at=datetime.now(UTC)), "This account has been removed"),
    ("flagged for an administrator reset",
     dict(requires_admin_reset=True), "Account requires administrator reset"),
    ("locked out",
     dict(locked_until=datetime.now(UTC) + timedelta(minutes=15)), "Account locked"),
]


@pytest.mark.parametrize(("case", "fields", "detail"), REFUSED, ids=[c[0] for c in REFUSED])
def test_enroll_refuses(client, db_session, case, fields, detail):
    user = _user(db_session, **fields)
    resp = client.post("/auth/mfa/enroll", cookies={"wingrc_mfa_pending": _pending(user)})
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == detail
    assert "secret" not in resp.json(), "a refused enrolment must hand out no secret"
    assert _set_cookie_value(resp, "wingrc_mfa_setup") is None


@pytest.mark.parametrize(("case", "fields", "detail"), REFUSED, ids=[c[0] for c in REFUSED])
def test_confirm_refuses_and_leaves_the_account_alone(client, db_session, case, fields, detail):
    """The account turns refusable between the two calls: enrol while it is
    enrollable, then change it, then confirm."""
    user = _user(db_session)
    pending = _pending(user)
    enroll = client.post("/auth/mfa/enroll", cookies={"wingrc_mfa_pending": pending})
    assert enroll.status_code == 200, (
        "fixture must enrol an enrollable user, or this proves nothing"
    )
    setup = _set_cookie_value(enroll, "wingrc_mfa_setup")
    new_secret = enroll.json()["secret"]

    for k, v in fields.items():
        setattr(user, k, v)
    db_session.flush()
    before = (user.totp_secret, user.mfa_enrolled, user.is_active)

    resp = client.post(
        "/auth/mfa/enroll/confirm",
        json={"code": pyotp.TOTP(new_secret).now()},
        cookies={"wingrc_mfa_pending": pending, "wingrc_mfa_setup": setup},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == detail
    assert _set_cookie_value(resp, "wingrc_session") is None
    db_session.refresh(user)
    assert (user.totp_secret, user.mfa_enrolled, user.is_active) == before


@pytest.mark.parametrize(
    "case",
    ["an invitee", "an account whose MFA an administrator reset"],
)
def test_an_inactive_account_awaiting_enrolment_still_enrols(client, db_session, case):
    """Both are inactive, with mfa_enrolled false, until they enrol here.
    Refusing inactive accounts would break them both."""
    user = _user(db_session, is_active=False, mfa_enrolled=False)
    pending = _pending(user)
    enroll = client.post("/auth/mfa/enroll", cookies={"wingrc_mfa_pending": pending})
    assert enroll.status_code == 200, enroll.text
    resp = client.post(
        "/auth/mfa/enroll/confirm",
        json={"code": pyotp.TOTP(enroll.json()["secret"]).now()},
        cookies={"wingrc_mfa_pending": pending,
                 "wingrc_mfa_setup": _set_cookie_value(enroll, "wingrc_mfa_setup")},
    )
    assert resp.status_code == 200, resp.text
    db_session.refresh(user)
    assert user.mfa_enrolled and user.is_active


def test_an_expired_lockout_does_not_refuse(client, db_session):
    user = _user(db_session, locked_until=datetime.now(UTC) - timedelta(minutes=1))
    resp = client.post("/auth/mfa/enroll", cookies={"wingrc_mfa_pending": _pending(user)})
    assert resp.status_code == 200, resp.text
