"""Authentication: session management, password hashing, FastAPI dependencies.

Credential resolution order in get_current_user:
  1. wingrc_session cookie (human sessions)
  2. Authorization: Bearer wingrc_<token> header (API tokens)

Session cookies are HttpOnly + SameSite=Lax. The Secure flag is set only in
production (settings.environment == "production") because the dev server runs
over plain HTTP at 10.10.24.35:8000.

State cookies (wingrc_auth_flow, wingrc_mfa_pending) are signed with HMAC-SHA256
using WINGRC_AUTH_FLOW_SECRET. They carry a short-lived payload (5 min) for the
OIDC code exchange and the in-progress MFA verification step respectively.

Password hashing: PBKDF2-HMAC-SHA256 with 600,000 iterations (NIST SP 800-63B /
SP 800-132). bcrypt/argon2/scrypt are not FIPS-140-validated; stdlib hashlib is.

Account lockout: exponential backoff — 5 failures triggers 15min * 2^lockout_count
(capped at 8h). After 3 lockout events requires_admin_reset is set.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session

_HASH_ALGO = "sha256"
_PBKDF2_ITERS = 600_000
_LOCKOUT_THRESHOLD = 5
_LOCKOUT_BASE_MINUTES = 15
_LOCKOUT_MAX_HOURS = 8
_LOCKOUT_RESET_COUNT = 3
_BACKUP_CODE_COUNT = 10
_PASSWORD_MIN_LEN = 15
_PASSWORD_MAX_LEN = 128
_STATE_COOKIE_TTL = 300  # 5 minutes
_TOKEN_PREFIX = "wingrc_"


# ---------------------------------------------------------------------------
# Resolved identity (works for both session and API token auth)
# ---------------------------------------------------------------------------

@dataclass
class CurrentUser:
    id: Any  # uuid.UUID
    org_id: Any  # uuid.UUID
    email: str
    display_name: str
    role: str
    is_active: bool
    login_method: str


# ---------------------------------------------------------------------------
# Password hashing (PBKDF2-HMAC-SHA256, FIPS-140 compatible)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(32)
    h = hashlib.pbkdf2_hmac(_HASH_ALGO, password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return f"{_PBKDF2_ITERS}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        iters_str, salt_hex, hash_hex = stored.split("$")
        iters = int(iters_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac(_HASH_ALGO, password.encode("utf-8"), salt, iters)
    return hmac.compare_digest(candidate, expected)


def validate_password_policy(password: str) -> list[str]:
    errors: list[str] = []
    if len(password) < _PASSWORD_MIN_LEN:
        errors.append(f"Password must be at least {_PASSWORD_MIN_LEN} characters.")
    if len(password) > _PASSWORD_MAX_LEN:
        errors.append(f"Password must not exceed {_PASSWORD_MAX_LEN} characters.")
    return errors


def _load_common_passwords() -> frozenset[str]:
    path = os.path.join(os.path.dirname(__file__), "data", "common_passwords.txt")
    try:
        with open(path) as f:
            return frozenset(line.strip().lower() for line in f if line.strip())
    except FileNotFoundError:
        return frozenset()


_COMMON_PASSWORDS: frozenset[str] | None = None


def check_pwned_password(password: str) -> bool:
    """True if the password appears in known breach lists.

    Checks a bundled local list first (instant, no network), then queries
    the Pwned Passwords k-anonymity API. Only the first 5 hex chars of the
    SHA-1 hash are sent — the password itself is never transmitted.

    Returns False on API timeout or network error (fail open, log warning).
    SHA-1 is used here purely for the HIBP lookup protocol, not for security;
    usedforsecurity=False satisfies FIPS-mode validation.
    """
    global _COMMON_PASSWORDS
    settings = get_settings()
    if not settings.pwned_passwords_check:
        return False

    if _COMMON_PASSWORDS is None:
        _COMMON_PASSWORDS = _load_common_passwords()
    if password.lower() in _COMMON_PASSWORDS:
        return True

    try:
        sha1 = hashlib.sha1(password.encode("utf-8"), usedforsecurity=False).hexdigest().upper()
        prefix, suffix = sha1[:5], sha1[5:]
        url = f"https://api.pwnedpasswords.com/range/{prefix}"
        req = urllib.request.Request(url, headers={"Add-Padding": "true"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            body = resp.read().decode()
        for line in body.splitlines():
            if ":" in line:
                h, _ = line.split(":", 1)
                if h.upper() == suffix:
                    return True
    except Exception:
        pass  # API unreachable: fail open
    return False


# ---------------------------------------------------------------------------
# Lockout helpers
# ---------------------------------------------------------------------------

def _lockout_duration_minutes(lockout_count: int) -> int:
    minutes = _LOCKOUT_BASE_MINUTES * (2 ** (lockout_count - 1))
    return min(minutes, _LOCKOUT_MAX_HOURS * 60)


def apply_failed_login(db: Session, user: Any) -> None:
    """Increment failure counter; set lockout if threshold reached."""
    user.failed_login_count += 1
    if user.failed_login_count >= _LOCKOUT_THRESHOLD:
        user.lockout_count += 1
        user.failed_login_count = 0
        minutes = _lockout_duration_minutes(user.lockout_count)
        user.locked_until = datetime.now(UTC) + timedelta(minutes=minutes)
        if user.lockout_count >= _LOCKOUT_RESET_COUNT:
            user.requires_admin_reset = True


def clear_failed_login(user: Any) -> None:
    user.failed_login_count = 0
    user.locked_until = None


# ---------------------------------------------------------------------------
# State cookie signing (HMAC-SHA256)
# ---------------------------------------------------------------------------

def sign_state_cookie(payload: dict[str, Any]) -> str:
    """Encode payload as URL-safe base64 + HMAC-SHA256 signature."""
    settings = get_settings()
    secret = settings.auth_flow_secret.encode()
    msg = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
    b64 = base64.urlsafe_b64encode(msg).rstrip(b"=").decode()
    return f"{b64}.{sig}"


def verify_state_cookie(value: str) -> dict[str, Any] | None:
    try:
        b64, sig = value.rsplit(".", 1)
        padding = 4 - len(b64) % 4
        msg = base64.urlsafe_b64decode(b64 + "=" * padding)
        settings = get_settings()
        secret = settings.auth_flow_secret.encode()
        expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(msg)
        # Check TTL
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


def make_state_payload(extra: dict[str, Any]) -> dict[str, Any]:
    return {**extra, "exp": int(time.time()) + _STATE_COOKIE_TTL}


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_secret(prefix: str = "") -> tuple[str, str]:
    """Generate a random secret and its hash. Returns (raw, hash).

    The raw value is what gets shown to the caller once and is never stored;
    only the hash is persisted. Shared by every call site that mints a
    bearer-style secret (session cookie, invite token, API token) so they
    stay byte-for-byte consistent instead of each reimplementing
    token_urlsafe(32) + hashing.
    """
    raw = prefix + secrets.token_urlsafe(32)
    return raw, _token_hash(raw)


def create_session(db: Session, user: Any) -> tuple[Any, str]:
    """Create a UserSession row. Returns (session_row, raw_token).

    The raw token must be set as the wingrc_session cookie; it is never stored.
    """
    from .models import UserSession
    settings = get_settings()
    raw, token_hash = generate_secret()
    now = datetime.now(UTC)
    session_row = UserSession(
        user_id=user.id,
        org_id=user.org_id,
        token_hash=token_hash,
        created_at=now,
        expires_at=now + timedelta(hours=settings.session_expiry_hours),
    )
    db.add(session_row)
    return session_row, raw


def set_session_cookie(response: Response, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        "wingrc_session",
        raw_token,
        httponly=True,
        samesite="lax",
        path="/api",
        max_age=settings.session_expiry_hours * 3600,
        secure=(settings.environment == "production"),
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.set_cookie(
        "wingrc_session",
        "",
        httponly=True,
        samesite="lax",
        path="/api",
        max_age=0,
        secure=(settings.environment == "production"),
    )


def set_state_cookie(response: Response, name: str, payload: dict[str, Any]) -> None:
    settings = get_settings()
    response.set_cookie(
        name,
        sign_state_cookie(payload),
        httponly=True,
        samesite="lax",
        path="/api/auth",
        max_age=_STATE_COOKIE_TTL,
        secure=(settings.environment == "production"),
    )


def clear_state_cookie(response: Response, name: str) -> None:
    settings = get_settings()
    response.set_cookie(
        name, "", httponly=True, samesite="lax",
        path="/api/auth", max_age=0,
        secure=(settings.environment == "production"),
    )


# ---------------------------------------------------------------------------
# FastAPI auth dependencies
# ---------------------------------------------------------------------------

def get_current_user(
    request: Request,
    db: Session = Depends(get_session),
) -> CurrentUser:
    """Resolve wingrc_session cookie or Bearer token. Raises 401 if absent/invalid."""
    raw_session = request.cookies.get("wingrc_session")
    if raw_session:
        return _resolve_session(db, raw_session)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return _resolve_api_token(db, auth_header[7:])

    raise HTTPException(status_code=401, detail="Not authenticated")


def _resolve_session(db: Session, raw: str) -> CurrentUser:
    from .models import User
    h = _token_hash(raw)

    row = db.execute(
        text("SELECT user_id, org_id, expires_at FROM auth.resolve_session(:h)"),
        {"h": h},
    ).first()

    if row is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    # Set app.current_org for all subsequent RLS-gated queries in this request
    db.execute(text(f"SET LOCAL app.current_org = '{row.org_id}'"))

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    return CurrentUser(
        id=user.id,
        org_id=user.org_id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        login_method=user.login_method,
    )


def _resolve_api_token(db: Session, raw: str) -> CurrentUser:
    from .models import User
    h = _token_hash(raw)
    now = datetime.now(UTC)

    row = db.execute(
        text(
            "SELECT id, org_id, user_id, role, expires_at, revoked_at"
            " FROM auth.resolve_api_token(:h)"
        ),
        {"h": h},
    ).first()

    if row is None:
        raise HTTPException(status_code=401, detail="Invalid API token")

    if row.revoked_at is not None:
        raise HTTPException(status_code=401, detail="API token revoked")

    if row.expires_at is not None and row.expires_at.replace(tzinfo=UTC) < now:
        raise HTTPException(status_code=401, detail="API token expired")

    # Set current_org then update last_used_at (RLS now satisfied)
    db.execute(text(f"SET LOCAL app.current_org = '{row.org_id}'"))
    db.execute(
        text("UPDATE api_token SET last_used_at = :now WHERE id = :id"),
        {"now": now, "id": row.id},
    )

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    return CurrentUser(
        id=user.id,
        org_id=row.org_id,
        email=user.email,
        display_name=user.display_name,
        role=row.role,  # token's role; may be lower than user.role
        is_active=user.is_active,
        login_method=user.login_method,
    )


def require_org_access(*roles: str):
    """FastAPI dependency factory: confirms the authenticated user's org_id
    matches the org_id path parameter (403 if not), and optionally the
    user's role (403 if roles are given and the user's role isn't among
    them).

    Usage:  Depends(require_org_access())                   # org-scope only
            Depends(require_org_access("msp_admin"))         # + role gate
    """
    def _check(
        org_id: uuid.UUID,
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if current_user.org_id != org_id:
            raise HTTPException(status_code=403, detail="Cross-org access denied")
        if roles and current_user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Requires one of: {', '.join(roles)}",
            )
        return current_user
    return _check


def require_role(*roles: str):
    """FastAPI dependency factory for role-only gates — no org_id check.

    For routes with no target org in their own path to compare against
    (e.g. GET/POST /orgs, which list/create across orgs rather than acting
    on one), so require_org_access() doesn't apply. Prefer
    require_org_access() for any route that does have an org_id path
    parameter — this exists specifically for the routes that don't.

    Usage:  Depends(require_role("msp_admin"))
    """
    def _check(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Requires one of: {', '.join(roles)}",
            )
        return current_user
    return _check
