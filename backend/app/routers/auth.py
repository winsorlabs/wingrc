# noqa: B008
"""Auth endpoints.

Covers SSO (Microsoft Entra ID via MSAL), local password login,
MFA enrollment and verification, session logout, and GET /auth/me.

Flow summary
------------
SSO:
  GET /auth/login  → redirect browser to Microsoft
  GET /auth/callback  → exchange code, find/create user, issue session

Local (invite-provisioned):
  POST /auth/set-password  → validate invite/reset token, set password
      ↓ mfa_enrolled=False (invite redemption) → {next:"enroll"} + wingrc_mfa_pending(phase=enroll)
      ↓ mfa_enrolled=True  (password reset)    → {next:"verify"} + wingrc_mfa_pending(phase=verify)
      Also the redemption endpoint for admin-issued password resets
      (POST /orgs/{org_id}/users/{user_id}/reset-password, I.5) — same
      token mechanism, same columns, same endpoint as invite acceptance.
      A reset target is an existing, possibly already-MFA-enrolled user,
      unlike a brand-new invite — see next above.
  POST /auth/mfa/enroll  → return TOTP provisioning URI + set wingrc_mfa_setup cookie
  POST /auth/mfa/enroll/confirm  → verify TOTP code, store secret,
                                   issue backup codes, issue session
  POST /auth/login  → password+email
      ↓ wrong password → 401 (lockout counter incremented)
      ↓ mfa_enrolled=False → {next:"enroll"} + wingrc_mfa_pending(phase=enroll)
      ↓ mfa_enrolled=True  → {next:"verify"} + wingrc_mfa_pending(phase=verify)
  POST /auth/mfa/verify  → TOTP or backup code → issue session

Session:
  POST /auth/logout
  GET  /auth/me
"""
from __future__ import annotations

import hashlib  # used for invite-token, backup-code, and session hashing
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import (
    CurrentUser,
    apply_failed_login,
    check_login_rate_limit,
    check_password_reuse,
    check_pwned_password,
    clear_failed_login,
    clear_session_cookie,
    clear_state_cookie,
    create_session,
    get_client_ip,
    get_current_user,
    hash_password,
    make_state_payload,
    record_password,
    revoke_user_sessions,
    set_session_cookie,
    set_state_cookie,
    validate_password_policy,
    verify_password,
    verify_state_cookie,
)
from ..config import get_settings
from ..db import get_session
from ..models import MfaBackupCode, User

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# MSAL helpers
# ---------------------------------------------------------------------------

def _get_msal_app():  # type: ignore[return]
    try:
        from msal import ConfidentialClientApplication
    except ImportError:
        raise HTTPException(status_code=501, detail="msal package not installed") from None

    settings = get_settings()
    if not (settings.entra_client_id and settings.entra_tenant_id and settings.entra_client_secret):
        raise HTTPException(status_code=501, detail="SSO not configured")

    return ConfidentialClientApplication(
        client_id=settings.entra_client_id,
        authority=f"https://login.microsoftonline.com/{settings.entra_tenant_id}",
        client_credential=settings.entra_client_secret,
    )


_SSO_SCOPES = ["openid", "profile", "email"]


# ---------------------------------------------------------------------------
# SSO: initiate + callback
# ---------------------------------------------------------------------------

@router.get("/login")
def sso_login(response_obj: Any = None):
    """Redirect browser to Microsoft Entra ID for OIDC login."""
    settings = get_settings()
    msal_app = _get_msal_app()
    flow = msal_app.initiate_auth_code_flow(
        scopes=_SSO_SCOPES,
        redirect_uri=settings.entra_redirect_uri,
    )
    auth_uri = flow.pop("auth_uri", None)
    if not auth_uri:
        raise HTTPException(status_code=502, detail="MSAL did not return auth_uri")

    from fastapi.responses import RedirectResponse as RR
    resp = RR(auth_uri)
    set_state_cookie(resp, "wingrc_auth_flow", make_state_payload({"flow": flow}))
    return resp


@router.get("/callback")
def sso_callback(
    request: Request,
    db: Session = Depends(get_session),
    wingrc_auth_flow: str | None = Cookie(default=None),
):
    """Complete OIDC code exchange; issue session."""
    if not wingrc_auth_flow:
        raise HTTPException(status_code=400, detail="Missing auth_flow cookie")

    payload = verify_state_cookie(wingrc_auth_flow)
    if payload is None:
        raise HTTPException(status_code=400, detail="Invalid or expired auth flow state")

    msal_app = _get_msal_app()
    auth_response = dict(request.query_params)
    result = msal_app.acquire_token_by_auth_code_flow(payload["flow"], auth_response)

    if "error" in result:
        raise HTTPException(
            status_code=400,
            detail=f"SSO error: {result.get('error_description', result['error'])}",
        )

    claims = result.get("id_token_claims", {})
    oid = claims.get("oid") or claims.get("sub")
    email = claims.get("preferred_username") or claims.get("email") or claims.get("upn")
    display_name = claims.get("name") or email or "Unknown"

    if not oid or not email:
        raise HTTPException(status_code=400, detail="SSO response missing required claims")

    user_row = db.execute(
        text("SELECT * FROM auth.find_user_for_login(:oid, :email)"),
        {"oid": oid, "email": email},
    ).first()

    if user_row is None:
        raise HTTPException(
            status_code=403,
            detail="No account provisioned for this identity. Contact your MSP administrator.",
        )

    user = db.get(User, user_row.id)
    if user is None:
        raise HTTPException(status_code=403, detail="User not found")

    if user.requires_admin_reset:
        raise HTTPException(status_code=403, detail="Account requires administrator reset")

    if not user.is_active:
        # First SSO login — activate the user
        user.entra_oid = oid
        user.display_name = display_name
        user.is_active = True

    user.last_login_at = datetime.now(UTC)
    db.execute(text(f"SET LOCAL app.current_org = '{user.home_org_id}'"))

    _, raw_token = create_session(db, user)
    db.commit()

    log_event(
        db,
        org_id=user.home_org_id,
        action="auth.login",
        entity_type="user",
        entity_id=user.id,
        context={"method": "sso", "via": "entra_callback"},
        actor=str(user.id),
        actor_type="user",
    )
    db.commit()

    settings = get_settings()
    resp = RedirectResponse(url=f"{settings.entra_redirect_uri.rsplit('/api', 1)[0]}/")
    clear_state_cookie(resp, "wingrc_auth_flow")
    set_session_cookie(resp, raw_token)
    return resp


# ---------------------------------------------------------------------------
# Local: password login
# ---------------------------------------------------------------------------

class LocalLoginIn(BaseModel):
    email: EmailStr
    password: str


@router.post("/login")
def local_login(
    request: Request,
    body: LocalLoginIn,
    db: Session = Depends(get_session),
):
    """Password login for local accounts.

    Password validation happens BEFORE the mfa_enrolled check so MFA enrollment
    state is never disclosed to callers who fail authentication.
    """
    check_login_rate_limit(get_client_ip(request))

    user_row = db.execute(
        text("SELECT * FROM auth.find_user_for_login(NULL, :email)"),
        {"email": body.email},
    ).first()

    # Constant-time-ish: always hash-check even for nonexistent users
    if user_row is None or user_row.login_method != "local":
        # Perform a dummy hash to prevent timing oracle
        hash_password("dummy-stretch-prevents-timing")
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Every read/write of "user" below runs under wingrc_app (RLS-restricted,
    # not the bypassing owner role) once the Phase 3 cutover lands, so
    # app.current_org must be set before the very first org-scoped access —
    # db.get() immediately below — not just before the writes later in this
    # function. (It previously was only set inside the bad-password branch,
    # after this read had already run unscoped.)
    db.execute(text(f"SET LOCAL app.current_org = '{user_row.home_org_id}'"))

    user = db.get(User, user_row.id)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if user.requires_admin_reset:
        raise HTTPException(status_code=403, detail="Account requires administrator reset")

    now = datetime.now(UTC)
    if user.locked_until and user.locked_until.replace(tzinfo=UTC) > now:
        wait = int((user.locked_until.replace(tzinfo=UTC) - now).total_seconds() / 60) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Account locked. Try again in {wait} minute(s).",
        )

    # --- password check FIRST, before MFA state ---
    if not user.password_hash or not verify_password(body.password, user.password_hash):
        apply_failed_login(db, user)
        db.commit()
        log_event(
            db,
            org_id=user.home_org_id,
            action="auth.login.failed",
            entity_type="user",
            entity_id=user.id,
            context={"reason": "bad_password"},
            actor=str(user.id),
            actor_type="user",
        )
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")

    clear_failed_login(user)

    # --- now branch on MFA state ---
    phase = "enroll" if not user.mfa_enrolled else "verify"
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"next": phase})
    set_state_cookie(resp, "wingrc_mfa_pending", make_state_payload({
        "user_id": str(user.id),
        "org_id": str(user.home_org_id),
        "phase": phase,
    }))
    db.commit()
    return resp


# ---------------------------------------------------------------------------
# Invite / set-password
# ---------------------------------------------------------------------------

class SetPasswordIn(BaseModel):
    token: str
    password: str


@router.post("/set-password")
def set_password(
    body: SetPasswordIn,
    db: Session = Depends(get_session),
):
    """Redeem an invite or admin-issued reset token and set the password.

    Same token mechanism, same columns (invite_token_hash/invite_expires_at),
    same endpoint for both flows — see the module docstring. For a brand-new
    invite this does not activate the account (activation happens at MFA
    enrollment confirmation); for a password reset the account is already
    active and already MFA-enrolled, so this must not force it back through
    enrollment — see the mfa_enrolled branch below (I.5 deviation from the
    original spec, which assumed only the invite case).
    """
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
    user_row = db.execute(
        text("SELECT * FROM auth.find_user_for_invite(:h)"),
        {"h": token_hash},
    ).first()

    if user_row is None:
        raise HTTPException(status_code=400, detail="Invalid or expired invite token")

    errors = validate_password_policy(body.password)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    if check_pwned_password(body.password):
        raise HTTPException(
            status_code=422,
            detail=(
                "This password has appeared in a known data breach."
                " Please choose a different password."
            ),
        )

    user = db.get(User, user_row.id)
    if user is None:
        raise HTTPException(status_code=400, detail="User not found")

    db.execute(text(f"SET LOCAL app.current_org = '{user.home_org_id}'"))

    settings = get_settings()
    if user.password_hash and check_password_reuse(
        db, user.id, body.password, settings.password_history_generations
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Password must not match any of your last "
                f"{settings.password_history_generations} passwords."
            ),
        )

    user.password_hash = hash_password(body.password)
    user.invite_token_hash = None
    user.invite_expires_at = None
    record_password(db, user.id, user.password_hash)
    db.commit()

    phase = "enroll" if not user.mfa_enrolled else "verify"
    from fastapi.responses import JSONResponse
    resp = JSONResponse({"next": phase}, status_code=200)
    set_state_cookie(resp, "wingrc_mfa_pending", make_state_payload({
        "user_id": str(user.id),
        "org_id": str(user.home_org_id),
        "phase": phase,
    }))
    return resp


# ---------------------------------------------------------------------------
# MFA enrollment
# ---------------------------------------------------------------------------

def _mfa_qr_data_uri(provisioning_uri: str) -> str:
    """Render the TOTP provisioning URI as an inline SVG QR code, generated
    entirely server-side.

    ADR 0008: this used to be the frontend's job — it sent `provisioning_uri`
    (which embeds the live TOTP shared secret) to a third-party QR-rendering
    service (api.qrserver.com) as a query parameter, disclosing the secret to
    an operator WinGRC has no relationship with. Rendering server-side means
    the secret never leaves the deployment's own trust boundary; the caller
    just displays the returned `data:` URI directly as an <img src>, with no
    outbound request at enrollment time at all.
    """
    try:
        import segno
    except ImportError:
        raise HTTPException(status_code=501, detail="segno not installed") from None

    qr = segno.make(provisioning_uri, error="M")
    return qr.svg_data_uri(scale=4, border=2)


@router.post("/mfa/enroll")
def mfa_enroll(
    db: Session = Depends(get_session),
    wingrc_mfa_pending: str | None = Cookie(default=None),
):
    """Start TOTP enrollment. Returns provisioning URI; secret stored in signed cookie."""
    try:
        import pyotp
    except ImportError:
        raise HTTPException(status_code=501, detail="pyotp not installed") from None

    if not wingrc_mfa_pending:
        raise HTTPException(status_code=401, detail="No MFA pending state")

    pending = verify_state_cookie(wingrc_mfa_pending)
    if not pending or pending.get("phase") != "enroll":
        raise HTTPException(status_code=400, detail="Invalid or expired MFA pending state")

    user_id = uuid.UUID(pending["user_id"])
    org_id = uuid.UUID(pending["org_id"])
    db.execute(text(f"SET LOCAL app.current_org = '{org_id}'"))

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=user.email, issuer_name="WinGRC")

    from fastapi.responses import JSONResponse
    resp = JSONResponse({
        "provisioning_uri": uri,
        "secret": secret,
        "qr_data_uri": _mfa_qr_data_uri(uri),
    })
    set_state_cookie(resp, "wingrc_mfa_setup", make_state_payload({
        "user_id": str(user_id),
        "org_id": str(org_id),
        "totp_secret": secret,
    }))
    return resp


class MfaEnrollConfirmIn(BaseModel):
    code: str


@router.post("/mfa/enroll/confirm")
def mfa_enroll_confirm(
    body: MfaEnrollConfirmIn,
    db: Session = Depends(get_session),
    wingrc_mfa_pending: str | None = Cookie(default=None),
    wingrc_mfa_setup: str | None = Cookie(default=None),
):
    """Verify TOTP code, store secret, issue backup codes and session."""
    try:
        import pyotp
    except ImportError:
        raise HTTPException(status_code=501, detail="pyotp not installed") from None

    if not wingrc_mfa_pending or not wingrc_mfa_setup:
        raise HTTPException(status_code=401, detail="Missing MFA enrollment cookies")

    pending = verify_state_cookie(wingrc_mfa_pending)
    setup = verify_state_cookie(wingrc_mfa_setup)

    if not pending or pending.get("phase") != "enroll":
        raise HTTPException(status_code=400, detail="Invalid MFA pending state")
    if not setup:
        raise HTTPException(status_code=400, detail="Invalid MFA setup state")

    if pending.get("user_id") != setup.get("user_id"):
        raise HTTPException(status_code=400, detail="MFA state mismatch")

    user_id = uuid.UUID(pending["user_id"])
    org_id = uuid.UUID(pending["org_id"])
    totp_secret = setup["totp_secret"]

    totp = pyotp.TOTP(totp_secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    db.execute(text(f"SET LOCAL app.current_org = '{org_id}'"))
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.totp_secret = totp_secret
    user.mfa_enrolled = True
    user.is_active = True

    # Generate backup codes
    raw_codes = [secrets.token_hex(4) for _ in range(10)]
    for code in raw_codes:
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        db.add(MfaBackupCode(user_id=user.id, code_hash=code_hash))

    _, raw_token = create_session(db, user)
    user.last_login_at = datetime.now(UTC)
    db.commit()

    log_event(
        db,
        org_id=org_id,
        action="auth.mfa.enrolled",
        entity_type="user",
        entity_id=user_id,
        context={"method": "totp"},
        actor=str(user_id),
        actor_type="user",
    )
    db.commit()

    from fastapi.responses import JSONResponse
    resp = JSONResponse({"backup_codes": raw_codes})
    clear_state_cookie(resp, "wingrc_mfa_pending")
    clear_state_cookie(resp, "wingrc_mfa_setup")
    set_session_cookie(resp, raw_token)
    return resp


# ---------------------------------------------------------------------------
# MFA verify (post-login for already-enrolled users)
# ---------------------------------------------------------------------------

class MfaVerifyIn(BaseModel):
    code: str


@router.post("/mfa/verify")
def mfa_verify(
    body: MfaVerifyIn,
    db: Session = Depends(get_session),
    wingrc_mfa_pending: str | None = Cookie(default=None),
):
    """Verify TOTP code or backup code after successful password login."""
    try:
        import pyotp
    except ImportError:
        raise HTTPException(status_code=501, detail="pyotp not installed") from None

    if not wingrc_mfa_pending:
        raise HTTPException(status_code=401, detail="No MFA pending state")

    pending = verify_state_cookie(wingrc_mfa_pending)
    if not pending or pending.get("phase") != "verify":
        raise HTTPException(status_code=400, detail="Invalid or expired MFA pending state")

    user_id = uuid.UUID(pending["user_id"])
    org_id = uuid.UUID(pending["org_id"])
    db.execute(text(f"SET LOCAL app.current_org = '{org_id}'"))

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    verified = False

    # Try TOTP first
    if user.totp_secret:
        totp = pyotp.TOTP(user.totp_secret)
        if totp.verify(body.code, valid_window=1):
            verified = True

    # Try backup codes
    if not verified:
        code_hash = hashlib.sha256(body.code.encode()).hexdigest()
        from sqlalchemy import select
        backup = db.execute(
            select(MfaBackupCode).where(
                MfaBackupCode.user_id == user_id,
                MfaBackupCode.code_hash == code_hash,
                MfaBackupCode.used_at.is_(None),
            )
        ).scalar_one_or_none()
        if backup is not None:
            backup.used_at = datetime.now(UTC)
            verified = True

    if not verified:
        raise HTTPException(status_code=401, detail="Invalid MFA code")

    _, raw_token = create_session(db, user)
    user.last_login_at = datetime.now(UTC)
    db.commit()

    log_event(
        db,
        org_id=org_id,
        action="auth.login",
        entity_type="user",
        entity_id=user_id,
        context={"method": "local", "mfa": "totp"},
        actor=str(user_id),
        actor_type="user",
    )
    db.commit()

    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True})
    clear_state_cookie(resp, "wingrc_mfa_pending")
    set_session_cookie(resp, raw_token)
    return resp


# ---------------------------------------------------------------------------
# Self-service account management (I.9)
#
# Everything below acts on the *caller's own* account via an existing
# session (Depends(get_current_user)) — distinct from both the pre-auth
# flows above (keyed by signed state cookies, no session yet) and the
# msp_admin-gated admin actions in routers/users.py (act on someone else's
# account). None of this is gated by canWrite/require_write: that gate
# governs assessment/org data mutation, a different axis entirely from
# "can this authenticated person manage their own login" — confirmed by
# this router never having a require_write() dependency in the first
# place. A c3pao_assessor must be able to change their own password same
# as anyone.
# ---------------------------------------------------------------------------


def _verify_step_up(user: User, current_password: str | None, totp_code: str | None) -> bool:
    """Confirm live proof of account control before a high-stakes
    self-service action (MFA secret rotation, backup-code regeneration).

    A valid session alone isn't enough here: a hijacked session cookie
    could otherwise be used to silently swap the second factor and
    entrench takeover past a later password change. Accepts either the
    current password (works even if the user has lost their authenticator
    device — the primary real-world reason to re-enroll) or a current TOTP
    code (works if they still have the device and just want to rotate
    proactively). Backup codes are deliberately not accepted here — they're
    a scarce recovery resource for *login*, not step-up proof for a
    settings action.
    """
    if current_password:
        return bool(user.password_hash) and verify_password(current_password, user.password_hash)
    if totp_code:
        if not user.totp_secret:
            return False
        try:
            import pyotp
        except ImportError:
            raise HTTPException(status_code=501, detail="pyotp not installed") from None
        return pyotp.TOTP(user.totp_secret).verify(totp_code, valid_window=1)
    return False


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


class StepUpIn(BaseModel):
    current_password: str | None = None
    totp_code: str | None = None


class MfaReenrollConfirmIn(BaseModel):
    code: str


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Self-service password change for an already-authenticated local user.

    Distinct from /set-password (I.5): that endpoint redeems a one-time
    invite/reset token minted by someone else and never checks a live
    password. This is the "I know my current password and just want a new
    one" path — it requires the current password as proof, not a token,
    and never existed before I.9.
    """
    if current_user.login_method != "local":
        raise HTTPException(
            status_code=400,
            detail="Password change is only available for local accounts.",
        )

    user = db.get(User, current_user.id)
    if user is None or not user.password_hash or not verify_password(
        body.current_password, user.password_hash
    ):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    errors = validate_password_policy(body.new_password)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors))

    if check_pwned_password(body.new_password):
        raise HTTPException(
            status_code=422,
            detail=(
                "This password has appeared in a known data breach."
                " Please choose a different password."
            ),
        )

    settings = get_settings()
    if check_password_reuse(
        db, user.id, body.new_password, settings.password_history_generations
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Password must not match any of your last "
                f"{settings.password_history_generations} passwords."
            ),
        )

    user.password_hash = hash_password(body.new_password)
    record_password(db, user.id, user.password_hash)
    db.commit()

    log_event(
        db,
        org_id=user.home_org_id,
        action="auth.password_change",
        entity_type="user",
        entity_id=user.id,
        context={"via": "self_service"},
        actor=str(user.id),
        actor_type="user",
    )
    db.commit()

    return {"ok": True}


@router.post("/mfa/reenroll")
def mfa_reenroll(
    body: StepUpIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Start self-service MFA re-enrollment for an already-authenticated
    local user (e.g. a lost or replaced authenticator device).

    Not the same as pre-auth /mfa/enroll (I.5): that one is keyed by a
    signed state cookie set during login/invite redemption and trusts
    "password + one-time token" as proof of control. This one only has an
    existing session to go on, which isn't equivalent proof for something
    this sensitive, so it requires step-up (see _verify_step_up) before
    staging a new candidate secret. Staging uses the same signed-cookie
    mechanism as pre-auth enrollment (set_state_cookie/verify_state_cookie
    are generic, not pre-auth-specific) under a distinct cookie name, since
    there is no wingrc_mfa_pending cookie in this flow to key off of.
    """
    try:
        import pyotp
    except ImportError:
        raise HTTPException(status_code=501, detail="pyotp not installed") from None

    if current_user.login_method != "local":
        raise HTTPException(
            status_code=400,
            detail="MFA re-enrollment is only available for local accounts.",
        )

    user = db.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if not _verify_step_up(user, body.current_password, body.totp_code):
        raise HTTPException(
            status_code=401, detail="Current password or authenticator code required"
        )

    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=user.email, issuer_name="WinGRC")

    from fastapi.responses import JSONResponse
    resp = JSONResponse({
        "provisioning_uri": uri,
        "secret": secret,
        "qr_data_uri": _mfa_qr_data_uri(uri),
    })
    set_state_cookie(resp, "wingrc_mfa_reenroll", make_state_payload({
        "user_id": str(user.id),
        "totp_secret": secret,
    }))
    return resp


@router.post("/mfa/reenroll/confirm")
def mfa_reenroll_confirm(
    body: MfaReenrollConfirmIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(get_current_user),
    wingrc_mfa_reenroll: str | None = Cookie(default=None),
):
    """Verify the new TOTP code and commit the rotated secret + fresh backup
    codes.

    Deliberately does NOT mint a new session or touch wingrc_session — the
    caller is already authenticated; re-enrolling MFA shouldn't silently
    rotate their session as a side effect the way pre-auth
    /mfa/enroll/confirm does (that one has to — it's how you first get
    logged in).
    """
    try:
        import pyotp
    except ImportError:
        raise HTTPException(status_code=501, detail="pyotp not installed") from None

    if not wingrc_mfa_reenroll:
        raise HTTPException(status_code=401, detail="No re-enrollment in progress")

    staged = verify_state_cookie(wingrc_mfa_reenroll)
    if not staged or staged.get("user_id") != str(current_user.id):
        raise HTTPException(status_code=400, detail="Invalid or expired re-enrollment state")

    totp_secret = staged["totp_secret"]
    if not pyotp.TOTP(totp_secret).verify(body.code, valid_window=1):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")

    user = db.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    user.totp_secret = totp_secret
    user.mfa_enrolled = True

    db.execute(text("DELETE FROM mfa_backup_code WHERE user_id = :uid"), {"uid": user.id})
    raw_codes = [secrets.token_hex(4) for _ in range(10)]
    for code in raw_codes:
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        db.add(MfaBackupCode(user_id=user.id, code_hash=code_hash))

    db.commit()

    log_event(
        db,
        org_id=user.home_org_id,
        action="auth.mfa.reenrolled",
        entity_type="user",
        entity_id=user.id,
        context={"via": "self_service"},
        actor=str(user.id),
        actor_type="user",
    )
    db.commit()

    from fastapi.responses import JSONResponse
    resp = JSONResponse({"backup_codes": raw_codes})
    clear_state_cookie(resp, "wingrc_mfa_reenroll")
    return resp


@router.post("/mfa/backup-codes/regenerate")
def regenerate_backup_codes(
    body: StepUpIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Replace this user's backup codes without touching their TOTP secret.

    The lightweight alternative to the admin's reset-mfa
    (POST /orgs/{org_id}/users/{user_id}/reset-mfa), which nulls the TOTP
    secret entirely and deactivates the account (see ADR 0008's remediation
    section) — the wrong tool for a user who just wants a fresh set of
    codes, not full re-enrollment.
    """
    if current_user.login_method != "local":
        raise HTTPException(
            status_code=400,
            detail="Backup codes are only available for local accounts.",
        )

    user = db.get(User, current_user.id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.mfa_enrolled:
        raise HTTPException(status_code=400, detail="MFA is not enrolled")

    if not _verify_step_up(user, body.current_password, body.totp_code):
        raise HTTPException(
            status_code=401, detail="Current password or authenticator code required"
        )

    db.execute(text("DELETE FROM mfa_backup_code WHERE user_id = :uid"), {"uid": user.id})
    raw_codes = [secrets.token_hex(4) for _ in range(10)]
    for code in raw_codes:
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        db.add(MfaBackupCode(user_id=user.id, code_hash=code_hash))
    db.commit()

    log_event(
        db,
        org_id=user.home_org_id,
        action="auth.mfa.backup_codes_regenerated",
        entity_type="user",
        entity_id=user.id,
        context={"via": "self_service"},
        actor=str(user.id),
        actor_type="user",
    )
    db.commit()

    return {"backup_codes": raw_codes}


@router.get("/sessions")
def list_sessions(
    request: Request,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List this user's own active sessions — the user-visible payoff for
    last_activity_at (I.4). No IP/device column exists on user_session, so
    this is time-only: created_at, last_activity_at, expires_at.
    """
    raw_session = request.cookies.get("wingrc_session")
    current_hash = hashlib.sha256(raw_session.encode()).hexdigest() if raw_session else None

    now = datetime.now(UTC)
    rows = db.execute(
        text(
            "SELECT id, token_hash, created_at, last_activity_at, expires_at"
            " FROM user_session"
            " WHERE user_id = :uid AND revoked_at IS NULL AND expires_at > :now"
            " ORDER BY last_activity_at DESC"
        ),
        {"uid": current_user.id, "now": now},
    ).all()

    return [
        {
            "id": str(r.id),
            "created_at": r.created_at.isoformat(),
            "last_activity_at": r.last_activity_at.isoformat(),
            "expires_at": r.expires_at.isoformat(),
            "is_current": r.token_hash == current_hash,
        }
        for r in rows
    ]


@router.post("/sessions/revoke-all")
def revoke_all_sessions(
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Sign out everywhere.

    Deliberately unconditional — reuses the same revoke_user_sessions
    helper the admin unlock/reset paths already use, which revokes every
    live session including the one making this request. The scenario this
    exists for is "I think my account is compromised," where revoking
    everything including the current tab is the correct, safer default.
    The response clears the session cookie for the same reason logout
    does — the frontend should treat this exactly like a logout, not a
    no-op on the current tab.
    """
    revoke_user_sessions(db, current_user.id)
    db.commit()

    log_event(
        db,
        org_id=current_user.org_id,
        action="auth.sessions.revoke_all",
        entity_type="user",
        entity_id=current_user.id,
        context={"via": "self_service"},
        actor=str(current_user.id),
        actor_type="user",
    )
    db.commit()

    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True})
    clear_session_cookie(resp)
    return resp


# ---------------------------------------------------------------------------
# Logout + me
# ---------------------------------------------------------------------------

@router.post("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(get_current_user),
):
    raw_session = request.cookies.get("wingrc_session")
    if raw_session:
        h = hashlib.sha256(raw_session.encode()).hexdigest()
        db.execute(
            text("UPDATE user_session SET revoked_at = :now WHERE token_hash = :h"),
            {"now": datetime.now(UTC), "h": h},
        )
        db.commit()

    log_event(
        db,
        org_id=current_user.org_id,
        action="auth.logout",
        entity_type="user",
        entity_id=current_user.id,
        context={},
        actor=str(current_user.id),
        actor_type="user",
    )
    db.commit()

    from fastapi.responses import JSONResponse
    resp = JSONResponse({"ok": True})
    clear_session_cookie(resp)
    return resp


@router.get("/me")
def me(current_user: CurrentUser = Depends(get_current_user)):
    return {
        "id": str(current_user.id),
        "org_id": str(current_user.org_id),
        "email": current_user.email,
        "display_name": current_user.display_name,
        "role": current_user.role,
        "login_method": current_user.login_method,
        "mfa_enrolled": current_user.mfa_enrolled,
    }
