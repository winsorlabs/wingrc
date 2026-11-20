"""An offboarded account cannot sign itself back in; the accounts that are
inactive only until they enrol still can.

`user.is_active = false` meant three things: an invitee, an account whose
MFA an administrator reset, and an account an administrator deactivated.
Until 2026-11-20 two sign-in paths treated all three as "about to
enrol" and made the account active again: the Entra callback, on any
sign-in, and password login with no MFA, through enrolment. Only an
MFA-enrolled password account was refused, at /mfa/verify
(docs/roadmap.md, "A deactivated user could sign back in").

Every case goes through real HTTP. The two regressions this fix could
cause, an invitee and an MFA reset no longer able to enrol, are the
tests that matter most, and each runs the whole flow.
"""
from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

import app.auth as auth_module
import app.routers.auth as auth_router
from app.auth import (
    create_session,
    get_current_user,
    hash_password,
    make_state_payload,
    sign_state_cookie,
)
from app.db import get_session
from app.main import app
from app.models import AuditLog, Organization, User, UserSession
from tests.conftest import _app_session, _authed, _grant, _make_fake_user, _set_cookie_value

PASSWORD = "Correct-Horse-Battery-Staple-41"
USERS_PY = Path(__file__).resolve().parents[1] / "app" / "routers" / "users.py"

# Written out: every function in routers/users.py that sets is_active to
# anything but True, each of which must revoke the account's sessions.
SETS_INACTIVE = {"deactivate_user", "patch_user", "reset_user_mfa"}


def _functions_setting_inactive(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    found = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (isinstance(target, ast.Attribute) and target.attr == "is_active"
                        and not (isinstance(node.value, ast.Constant)
                                 and node.value.value is True)):
                    found[fn.name] = fn
    return found


def test_every_path_that_sets_a_user_inactive_revokes_their_sessions():
    """reset_user_mfa did not until 2026-11-20. Its session rows stayed live
    and were accepted again once the person re-enrolled."""
    found = _functions_setting_inactive(ast.parse(USERS_PY.read_text(encoding="utf-8")))
    assert set(found) == SETS_INACTIVE
    for name, fn in found.items():
        calls = {n.func.id for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "revoke_user_sessions" in calls, f"{name} sets is_active without revoking"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org(db_session):
    o = Organization(name=f"Offboard-{uuid.uuid4().hex[:8]}")
    db_session.add(o)
    db_session.flush()
    return o


@pytest.fixture
def admin(db_session, org):
    who = _make_fake_user(org_id=org.id, email=f"admin-{uuid.uuid4().hex[:6]}@example.com")
    _grant(db_session, who)
    return who


@pytest.fixture
def client(db_session):
    auth_module._login_attempts.clear()
    app.dependency_overrides[get_session] = _app_session(db_session)
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.clear()


def _as_admin(db_session, admin):
    """The admin's routes need get_current_user overridden; the sign-in
    routes must not have it, so this is switched on per call."""
    app.dependency_overrides[get_current_user] = _authed(db_session, admin)


def _as_nobody():
    app.dependency_overrides.pop(get_current_user, None)


def _user(db_session, org, **overrides) -> User:
    fields = dict(
        home_org_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Offboarding Test User",
        login_method="local",
        role="msp_engineer",
        is_active=True,
        mfa_enrolled=False,
        password_hash=hash_password(PASSWORD),
    )
    fields.update(overrides)
    user = User(**fields)
    db_session.add(user)
    db_session.flush()
    return user


def _enrolled(db_session, org, **overrides) -> tuple[User, str]:
    secret = pyotp.random_base32()
    user = _user(db_session, org, mfa_enrolled=True, totp_secret=secret, **overrides)
    return user, secret


def _login(client, user) -> object:
    return client.post("/auth/login", json={"email": user.email, "password": PASSWORD})


def _enrol(client, pending: str):
    """The enrolment half of sign-in: start, then confirm with a good code."""
    enroll = client.post("/auth/mfa/enroll", cookies={"wingrc_mfa_pending": pending})
    if enroll.status_code != 200:
        return enroll
    return client.post(
        "/auth/mfa/enroll/confirm",
        json={"code": pyotp.TOTP(enroll.json()["secret"]).now()},
        cookies={"wingrc_mfa_pending": pending,
                 "wingrc_mfa_setup": _set_cookie_value(enroll, "wingrc_mfa_setup")},
    )


def _pending(user: User, phase: str) -> str:
    return sign_state_cookie(make_state_payload({
        "user_id": str(user.id), "org_id": str(user.home_org_id), "phase": phase,
    }))


def _actions(db_session, user) -> list[str]:
    return list(db_session.execute(
        select(AuditLog.action).where(AuditLog.entity_id == user.id)
        .order_by(AuditLog.created_at, AuditLog.id)
    ).scalars())


# ---------------------------------------------------------------------------
# The three sign-in paths, deactivated
# ---------------------------------------------------------------------------

class _FakeMsal:
    def __init__(self, oid: str, email: str):
        self.claims = {"oid": oid, "preferred_username": email, "name": "Entra Person"}

    def acquire_token_by_auth_code_flow(self, flow, auth_response):
        return {"id_token_claims": self.claims}


def _sso_callback(client, monkeypatch, user):
    monkeypatch.setattr(auth_router, "_get_msal_app", lambda: _FakeMsal(user.entra_oid, user.email))
    flow = sign_state_cookie(make_state_payload({"flow": {"state": "s"}}))
    return client.get("/auth/callback?code=c&state=s", cookies={"wingrc_auth_flow": flow})


@pytest.mark.integration
def test_sso_reactivated_an_inactive_account_before_this_fix(client, db_session, org, monkeypatch):
    """The callback's activation, with no deactivation recorded. This is what
    a deactivated SSO user got too; the next test is the refusal."""
    user = _user(db_session, org, login_method="sso", password_hash=None, is_active=False,
                 entra_oid=f"oid-{uuid.uuid4().hex}")
    resp = _sso_callback(client, monkeypatch, user)
    assert resp.status_code in (302, 307), (
        f"fixture must reach the callback's activation, or the refusal proves nothing: {resp.text}"
    )
    db_session.refresh(user)
    assert user.is_active


@pytest.mark.integration
def test_a_deactivated_sso_user_is_refused_and_stays_inactive(client, db_session, org, monkeypatch):
    user = _user(db_session, org, login_method="sso", password_hash=None, is_active=False,
                 entra_oid=f"oid-{uuid.uuid4().hex}", deactivated_at=datetime.now(UTC))
    resp = _sso_callback(client, monkeypatch, user)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Account deactivated"
    assert _set_cookie_value(resp, "wingrc_session") is None
    db_session.refresh(user)
    assert not user.is_active
    assert user.deactivated_at is not None


@pytest.mark.integration
def test_a_deactivated_local_user_with_no_mfa_gets_no_way_to_enrol(client, db_session, org):
    user = _user(db_session, org, is_active=False, deactivated_at=datetime.now(UTC))

    resp = _login(client, user)
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "Account deactivated"
    assert _set_cookie_value(resp, "wingrc_mfa_pending") is None

    # And with an enrol cookie anyway (a defect in how one is issued):
    confirm = _enrol(client, _pending(user, "enroll"))
    assert confirm.status_code == 403, confirm.text
    assert confirm.json()["detail"] == "Account deactivated"
    assert _set_cookie_value(confirm, "wingrc_session") is None
    db_session.refresh(user)
    assert not user.is_active and not user.mfa_enrolled
    assert db_session.execute(
        select(UserSession).where(UserSession.user_id == user.id)
    ).first() is None


@pytest.mark.integration
def test_a_wrong_password_on_a_deactivated_account_says_only_invalid_credentials(
    client, db_session, org,
):
    """Account state is told only to someone who authenticated."""
    user = _user(db_session, org, is_active=False, deactivated_at=datetime.now(UTC))
    resp = client.post("/auth/login", json={"email": user.email, "password": "wrong-" + PASSWORD})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


@pytest.mark.integration
def test_a_deactivated_local_user_with_mfa_is_still_refused_at_verify(client, db_session, org):
    """The path that was already sound. Login now refuses first, so verify is
    reached here with a pending cookie from before the deactivation."""
    user, secret = _enrolled(db_session, org, is_active=False, deactivated_at=datetime.now(UTC))
    assert _login(client, user).status_code == 403
    resp = client.post("/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()},
                       cookies={"wingrc_mfa_pending": _pending(user, "verify")})
    assert resp.status_code == 401, resp.text
    assert _set_cookie_value(resp, "wingrc_session") is None


@pytest.mark.integration
def test_a_refused_sign_in_is_in_the_users_history(client, db_session, org):
    user = _user(db_session, org, is_active=False, deactivated_at=datetime.now(UTC))
    _login(client, user)
    row = db_session.execute(
        select(AuditLog).where(AuditLog.entity_id == user.id,
                               AuditLog.action == "auth.login.failed")
    ).scalar_one()
    assert row.context == {"reason": "deactivated", "method": "local"}


# ---------------------------------------------------------------------------
# The regressions this could cause
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_an_invitee_still_enrols_and_signs_in(client, db_session, org, admin):
    _as_admin(db_session, admin)
    invite = client.post(f"/orgs/{org.id}/users", json={
        "email": f"invitee-{uuid.uuid4().hex[:6]}@example.com",
        "display_name": "New Invitee", "role": "msp_engineer", "login_method": "local",
    })
    assert invite.status_code == 201, invite.text
    _as_nobody()

    set_pw = client.post("/auth/set-password",
                         json={"token": invite.json()["invite_token"], "password": PASSWORD})
    assert set_pw.status_code == 200, set_pw.text
    assert set_pw.json() == {"next": "enroll"}

    confirm = _enrol(client, _set_cookie_value(set_pw, "wingrc_mfa_pending"))
    assert confirm.status_code == 200, confirm.text
    assert _set_cookie_value(confirm, "wingrc_session")
    user = db_session.get(User, uuid.UUID(invite.json()["id"]))
    db_session.refresh(user)
    assert user.is_active and user.deactivated_at is None
    # A first activation reads as enrolment, never as a reactivation.
    assert "auth.mfa.enrolled" in _actions(db_session, user)
    assert "user.reactivate" not in _actions(db_session, user)


@pytest.mark.integration
def test_an_mfa_reset_still_re_enrols_and_signs_in(client, db_session, org, admin):
    user, _ = _enrolled(db_session, org)
    old_session, old_raw = create_session(db_session, user)
    db_session.flush()

    _as_admin(db_session, admin)
    assert client.post(f"/orgs/{org.id}/users/{user.id}/reset-mfa").status_code == 200
    _as_nobody()
    db_session.refresh(user)
    assert not user.is_active and user.deactivated_at is None

    login = _login(client, user)
    assert login.status_code == 200, login.text
    assert login.json() == {"next": "enroll"}
    confirm = _enrol(client, _set_cookie_value(login, "wingrc_mfa_pending"))
    assert confirm.status_code == 200, confirm.text
    assert _set_cookie_value(confirm, "wingrc_session")
    db_session.refresh(user)
    assert user.is_active and user.mfa_enrolled

    # The session from before the reset stays dead after re-enrolment.
    db_session.refresh(old_session)
    assert old_session.revoked_at is not None
    me = client.get("/auth/me", cookies={"wingrc_session": old_raw})
    assert me.status_code == 401, me.text


@pytest.mark.integration
def test_an_mfa_reset_on_a_deactivated_account_does_not_reopen_it(client, db_session, org, admin):
    user, _ = _enrolled(db_session, org, is_active=False, deactivated_at=datetime.now(UTC))
    _as_admin(db_session, admin)
    assert client.post(f"/orgs/{org.id}/users/{user.id}/reset-mfa").status_code == 200
    _as_nobody()
    db_session.refresh(user)
    assert user.deactivated_at is not None
    assert _login(client, user).status_code == 403


# ---------------------------------------------------------------------------
# Deactivate and reactivate through the admin routes
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_deactivate_then_reactivate_round_trip(client, db_session, org, admin):
    user, secret = _enrolled(db_session, org)

    _as_admin(db_session, admin)
    assert client.delete(f"/orgs/{org.id}/users/{user.id}").status_code == 200
    _as_nobody()
    db_session.refresh(user)
    assert not user.is_active and user.deactivated_at is not None
    assert _login(client, user).status_code == 403

    _as_admin(db_session, admin)
    resp = client.patch(f"/orgs/{org.id}/users/{user.id}", json={"is_active": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["deactivated_at"] is None
    _as_nobody()
    db_session.refresh(user)
    assert user.is_active and user.deactivated_at is None

    login = _login(client, user)
    assert login.status_code == 200 and login.json() == {"next": "verify"}, login.text
    pending = _set_cookie_value(login, "wingrc_mfa_pending")
    verify = client.post("/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()},
                         cookies={"wingrc_mfa_pending": pending})
    assert verify.status_code == 200, verify.text

    actions = _actions(db_session, user)
    assert actions.index("user.deactivate") < actions.index("user.reactivate")
    reactivation = db_session.execute(
        select(AuditLog).where(AuditLog.entity_id == user.id,
                               AuditLog.action == "user.reactivate")
    ).scalar_one()
    assert reactivation.before_value["deactivated_at"] is not None
    assert reactivation.after_value == {"is_active": True, "deactivated_at": None}


@pytest.mark.integration
def test_patch_inactive_offboards_and_revokes(client, db_session, org, admin):
    user, _ = _enrolled(db_session, org)
    session_row, _ = create_session(db_session, user)
    db_session.flush()
    _as_admin(db_session, admin)
    resp = client.patch(f"/orgs/{org.id}/users/{user.id}", json={"is_active": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["deactivated_at"] is not None
    db_session.refresh(session_row)
    assert session_row.revoked_at is not None


@pytest.mark.integration
def test_patch_inactive_on_an_invitee_offboards_it_with_an_audit_row(
    client, db_session, org, admin,
):
    """is_active does not change, so the old code wrote nothing; cancelling
    an invite is still offboarding."""
    user = _user(db_session, org, is_active=False, password_hash=None)
    _as_admin(db_session, admin)
    resp = client.patch(f"/orgs/{org.id}/users/{user.id}", json={"is_active": False})
    assert resp.status_code == 200, resp.text
    db_session.refresh(user)
    assert user.deactivated_at is not None
    assert "user.activation_change" in _actions(db_session, user)


# ---------------------------------------------------------------------------
# Migration 0076's backfill
# ---------------------------------------------------------------------------

def _migration():
    import importlib.util
    path = (Path(__file__).resolve().parents[1] / "app" / "migrations" / "versions"
            / "0076_user_deactivated_at.py")
    spec = importlib.util.spec_from_file_location("m0076", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(db_session, user, action, after=None, at="2026-11-01T00:00:00Z"):
    db_session.add(AuditLog(org_id=user.home_org_id, action=action, entity_type="user",
                            entity_id=user.id, after_value=after,
                            created_at=datetime.fromisoformat(at.replace("Z", "+00:00"))))


# Written out: the history each account has, and whether the backfill
# reads it as offboarded.
BACKFILL_CASES = [
    ("deactivated", False, [("user.deactivate", None)], True),
    ("patched inactive", False, [("user.activation_change", {"is_active": False})], True),
    ("invitee", False, [("user.invite", None)], False),
    ("mfa reset", False, [("user.mfa_reset", None)], False),
    ("deactivated, then mfa reset", False,
     [("user.deactivate", None), ("user.mfa_reset", None)], True),
    ("deactivated, then reactivated, then mfa reset", False,
     [("user.deactivate", None), ("user.activation_change", {"is_active": True}),
      ("user.mfa_reset", None)], False),
    ("deactivated, then enrolled back in through the hole", True,
     [("user.deactivate", None), ("auth.mfa.enrolled", None)], False),
]


@pytest.mark.integration
@pytest.mark.parametrize(("case", "is_active", "history", "offboarded"), BACKFILL_CASES,
                         ids=[c[0] for c in BACKFILL_CASES])
def test_backfill_reads_offboarding_from_the_audit_log(
    db_session, org, case, is_active, history, offboarded,
):
    user = _user(db_session, org, is_active=is_active)
    for i, (action, after) in enumerate(history):
        _event(db_session, user, action, after, at=f"2026-11-0{i + 1}T00:00:00Z")
    db_session.flush()
    db_session.execute(text(_migration().BACKFILL_SQL))
    db_session.refresh(user)
    assert (user.deactivated_at is not None) == offboarded


# Written out: (mfa_enrolled, is_active) with no audit history, and whether
# the second rule reads the account as offboarded.
ENROLLED_CASES = [
    ("inactive and enrolled", True, False, True),
    ("inactive, not enrolled", False, False, False),
    ("active and enrolled", True, True, False),
]


@pytest.mark.integration
@pytest.mark.parametrize(("case", "enrolled", "is_active", "offboarded"), ENROLLED_CASES,
                         ids=[c[0] for c in ENROLLED_CASES])
def test_backfill_reads_inactive_and_enrolled_as_offboarded(
    db_session, org, case, enrolled, is_active, offboarded,
):
    user = _user(db_session, org, is_active=is_active, mfa_enrolled=enrolled)
    db_session.execute(text(_migration().ENROLLED_SQL))
    db_session.refresh(user)
    assert (user.deactivated_at is not None) == offboarded
