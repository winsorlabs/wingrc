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
  POST /auth/set-password  → validate invite token, set password
                             → responds {next:"enroll"} + wingrc_mfa_pending(phase=enroll)
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
    check_pwned_password,
    clear_failed_login,
    clear_session_cookie,
    clear_state_cookie,
    create_session,
    get_current_user,
    hash_password,
    make_state_payload,
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
    db.execute(text(f"SET LOCAL app.current_org = '{user.org_id}'"))

    _, raw_token = create_session(db, user)
    db.commit()

    log_event(
        db,
        org_id=user.org_id,
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
    body: LocalLoginIn,
    db: Session = Depends(get_session),
):
    """Password login for local accounts.

    Password validation happens BEFORE the mfa_enrolled check so MFA enrollment
    state is never disclosed to callers who fail authentication.
    """
    user_row = db.execute(
        text("SELECT * FROM auth.find_user_for_login(NULL, :email)"),
        {"email": body.email},
    ).first()

    # Constant-time-ish: always hash-check even for nonexistent users
    if user_row is None or user_row.login_method != "local":
        # Perform a dummy hash to prevent timing oracle
        hash_password("dummy-stretch-prevents-timing")
        raise HTTPException(status_code=401, detail="Invalid credentials")

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
        db.execute(text(f"SET LOCAL app.current_org = '{user.org_id}'"))
        apply_failed_login(db, user)
        db.commit()
        log_event(
            db,
            org_id=user.org_id,
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
        "org_id": str(user.org_id),
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
    """Accept an invite token and set the initial password.

    Does not activate the account — activation happens at MFA enrollment confirmation.
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

    db.execute(text(f"SET LOCAL app.current_org = '{user.org_id}'"))
    user.password_hash = hash_password(body.password)
    user.invite_token_hash = None
    user.invite_expires_at = None
    db.commit()

    from fastapi.responses import JSONResponse
    resp = JSONResponse({"next": "enroll"}, status_code=200)
    set_state_cookie(resp, "wingrc_mfa_pending", make_state_payload({
        "user_id": str(user.id),
        "org_id": str(user.org_id),
        "phase": "enroll",
    }))
    return resp


# ---------------------------------------------------------------------------
# MFA enrollment
# ---------------------------------------------------------------------------

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
    resp = JSONResponse({"provisioning_uri": uri, "secret": secret})
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
    }
