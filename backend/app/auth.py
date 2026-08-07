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
import logging
import os
import secrets
import time
import urllib.request
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_session

logger = logging.getLogger(__name__)

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

# Single ranking definition shared by API token minting (routers/users.py,
# a token cannot be minted above the issuer's/target's own rank) and token
# resolution below (a token cannot outlive the demotion of the user behind
# it — see _resolve_api_token).
_ROLE_RANK = {"msp_admin": 4, "msp_engineer": 3, "customer_poc": 2, "c3pao_assessor": 1}


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
    mfa_enrolled: bool


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
# Password history / reuse (I.5)
# ---------------------------------------------------------------------------

def check_password_reuse(
    db: Session, user_id: uuid.UUID, password: str, generations: int
) -> bool:
    """True if `password` matches any of the user's last `generations` hashes.

    Runs up to `generations` PBKDF2 verifications at 600k iterations each —
    real CPU time (see hash_password). Belongs only on the set/reset path,
    never on login.

    Orders by seq, not created_at — see PasswordHistory.seq / migration 0020.
    """
    from .models import PasswordHistory

    hashes = db.execute(
        select(PasswordHistory.password_hash)
        .where(PasswordHistory.user_id == user_id)
        .order_by(PasswordHistory.seq.desc())
        .limit(generations)
    ).scalars().all()
    return any(verify_password(password, h) for h in hashes)


def record_password(db: Session, user_id: uuid.UUID, password_hash: str) -> None:
    """Insert a password_history row, then trim beyond the configured
    generation count (config.py: password_history_generations).

    Orders by seq, not created_at — see PasswordHistory.seq / migration 0020.
    """
    from .models import PasswordHistory

    settings = get_settings()
    db.add(PasswordHistory(user_id=user_id, password_hash=password_hash))
    db.flush()

    stale_ids = db.execute(
        select(PasswordHistory.id)
        .where(PasswordHistory.user_id == user_id)
        .order_by(PasswordHistory.seq.desc())
        .offset(settings.password_history_generations)
    ).scalars().all()
    if stale_ids:
        db.execute(delete(PasswordHistory).where(PasswordHistory.id.in_(stale_ids)))


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
# Login rate limit by source IP (distinct from per-account lockout above)
# ---------------------------------------------------------------------------
#
# apply_failed_login gates one account after repeated failures against it.
# That alone permits spraying one attempt each across many accounts from a
# single source — no individual account ever reaches its own threshold.
# This gates the source address instead, independent of which account(s)
# it targets.
#
# In-memory fixed-window counter: this deployment runs a single uvicorn
# process per instance (see backend/Dockerfile — no --workers), so there is
# no cross-process state to reconcile. Resets on process restart, an
# accepted tradeoff for this control (see docs/PLAN-auth-rbac-completion.md
# I.6) rather than adding a DB table or an external store for it.

_LOGIN_RATE_LIMIT = 20  # attempts
_LOGIN_RATE_WINDOW_SECONDS = 900  # 15 minutes

_login_attempts: dict[str, tuple[float, int]] = {}


def get_client_ip(request: Request) -> str:
    """Resolve the real client address behind the nginx reverse proxy.

    deploy/nginx/nginx.conf sets X-Real-IP from its own $remote_addr,
    overwriting any client-supplied value — not spoofable through nginx.
    request.client.host alone would resolve to nginx's own address in the
    deployed topology, since uvicorn isn't run with --proxy-headers. Falls
    back to request.client.host for direct-connection dev/tests, where
    there is no proxy in front to set the header at all.
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"


def check_login_rate_limit(ip: str) -> None:
    """Raise 429 once an IP exceeds _LOGIN_RATE_LIMIT attempts within the window."""
    now = time.monotonic()
    window_start, count = _login_attempts.get(ip, (now, 0))
    if now - window_start > _LOGIN_RATE_WINDOW_SECONDS:
        window_start, count = now, 0
    count += 1
    _login_attempts[ip] = (window_start, count)
    if count > _LOGIN_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts from this address. Try again later.",
        )


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

    if settings.max_sessions_per_user > 0:
        _enforce_session_cap(db, user.id, settings.max_sessions_per_user, now)

    session_row = UserSession(
        user_id=user.id,
        org_id=user.home_org_id,
        token_hash=token_hash,
        created_at=now,
        expires_at=now + timedelta(hours=settings.session_expiry_hours),
    )
    db.add(session_row)
    return session_row, raw


def _enforce_session_cap(db: Session, user_id: uuid.UUID, cap: int, now: datetime) -> None:
    """Revoke the oldest active sessions for `user_id` beyond `cap`.

    Accounts for the session about to be created by this same call: the
    session under construction isn't added/flushed yet (SessionLocal has
    autoflush=False), so it can't appear in this SELECT regardless — the
    +1 below reserves its slot in the cap rather than relying on that.
    Self-healing if the cap is lowered after sessions already exceed it:
    each new login trims down toward the new cap rather than requiring a
    one-time cleanup.
    """
    from .models import UserSession

    existing = db.execute(
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .order_by(UserSession.created_at.asc())
    ).scalars().all()

    excess = len(existing) + 1 - cap
    if excess <= 0:
        return
    for stale in existing[:excess]:
        stale.revoked_at = now


def revoke_user_sessions(db: Session, user_id: uuid.UUID) -> None:
    """Revoke every live session for a user (does not commit).

    Shared by every write path that disables a user account
    (routers/users.py: deactivate_user, patch_user's is_active=False case)
    so they revoke identically instead of each re-implementing the query.
    """
    db.execute(
        text(
            "UPDATE user_session SET revoked_at = :now"
            " WHERE user_id = :uid AND revoked_at IS NULL"
        ),
        {"now": datetime.now(UTC), "uid": user_id},
    )


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


def _role_for_membership(
    db: Session, user_id: uuid.UUID, org_id: uuid.UUID, *, fallback: str
) -> str:
    """Look up the caller's role for (user_id, org_id) from org_membership.

    Callers must already have app.current_org set to org_id — this is
    then an ordinary, RLS-respecting same-org read, not a bypass. See
    require_org_access's docstring for why setting the GUC to exactly the
    org being asked about is the correct, narrowest context, not a
    broader one being opened up.

    Falls back to `fallback` (the legacy User.role column) if no
    membership row exists. This should never actually fire once every
    user-creation path provisions a membership at creation time (see
    org_membership.py, and the create_api_user fix at 41886f1) — logged
    loudly rather than silently, so a missing-membership bug doesn't
    masquerade as ordinary auth. Remove this fallback at M.9, once
    User.role itself is dropped and there's no longer a value to fall
    back to.
    """
    from .models import OrgMembership

    role = db.execute(
        select(OrgMembership.role).where(
            OrgMembership.user_id == user_id, OrgMembership.org_id == org_id
        )
    ).scalar_one_or_none()
    if role is not None:
        return role
    logger.warning(
        "No org_membership row for user_id=%s org_id=%s -- falling back to "
        "User.role (%s). This should not happen once every user-creation "
        "path provisions membership; see org_membership.py.",
        user_id, org_id, fallback,
    )
    return fallback


def _resolve_session(db: Session, raw: str) -> CurrentUser:
    from .models import User
    h = _token_hash(raw)
    settings = get_settings()

    row = db.execute(
        text("SELECT user_id, org_id, expires_at FROM auth.resolve_session(:h, :idle)"),
        {"h": h, "idle": settings.session_idle_minutes * 60},
    ).first()

    if row is None:
        raise HTTPException(status_code=401, detail="Session expired or invalid")

    # Set app.current_org for all subsequent RLS-gated queries in this request
    db.execute(text(f"SET LOCAL app.current_org = '{row.org_id}'"))

    # Throttled activity heartbeat: only write when the stored value is more
    # than 60s stale, so a live session doesn't take a write on every single
    # request — bounds idle-check accuracy to ±60s, immaterial against a
    # 15-minute window. token_hash is unique-indexed, so it identifies the
    # session row without auth.resolve_session needing to also return its id.
    db.execute(
        text(
            "UPDATE user_session SET last_activity_at = now()"
            " WHERE token_hash = :h AND last_activity_at < now() - interval '60 seconds'"
        ),
        {"h": h},
    )

    # Commit immediately so the heartbeat survives session.close() even on a
    # pure GET request, which never calls commit() on its own — without
    # this, idle tracking would silently never advance for read-only
    # activity. This is not hypothetical: c3pao_assessor (I.2) is
    # permanently read-only, so every request that role makes is a GET —
    # under the bare UPDATE, an actively-reviewing assessor would
    # deterministically hit the idle timeout regardless of engagement.
    # SET LOCAL is transaction-scoped and this commit ends that
    # transaction, so app.current_org must be re-issued for the new one
    # that starts under this same session — every RLS-gated query for the
    # rest of the request depends on it, not just the lookup below.
    db.commit()
    db.execute(text(f"SET LOCAL app.current_org = '{row.org_id}'"))

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    # Default org/role: the user's home org and their membership there.
    # This is only the default — require_org_access overwrites both once
    # a specific request's path org_id is known (ADR 0009 M.4). Safe to
    # read under ordinary RLS: app.current_org is already set to
    # user.home_org_id, above.
    role = _role_for_membership(db, user.id, user.home_org_id, fallback=user.role)

    return CurrentUser(
        id=user.id,
        org_id=user.home_org_id,
        email=user.email,
        display_name=user.display_name,
        role=role,
        is_active=user.is_active,
        login_method=user.login_method,
        mfa_enrolled=user.mfa_enrolled,
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

    # Commit immediately so this survives session.close() even on a pure
    # GET request, which never calls commit() on its own — without this,
    # last_used_at would silently never advance for read-only Bearer-token
    # traffic (a read-only integration, or any token minted at
    # c3pao_assessor). Same fix shape as _resolve_session's activity
    # heartbeat: SET LOCAL is transaction-scoped and this commit ends that
    # transaction, so app.current_org must be re-issued for the new one
    # that starts under this same session — every RLS-gated query for the
    # rest of the request depends on it, not just the lookup below.
    db.commit()
    db.execute(text(f"SET LOCAL app.current_org = '{row.org_id}'"))

    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    # Clamp to whichever is lower: the role frozen on the token at mint time,
    # or the user's current role IN THIS TOKEN'S OWN ORG (row.org_id, not
    # necessarily the user's home org — a token is minted for one specific
    # org and self-issuing into a non-home org is possible once M.4 lands,
    # since access is membership-based, not home-org equality). A demotion
    # after mint — including a demotion that's specific to this one org,
    # leaving the user's role elsewhere untouched — must not leave the
    # token running at its old (now-excessive) privilege; a promotion must
    # not retroactively escalate a token minted at a lower role. Safe to
    # read under ordinary RLS: app.current_org is already set to
    # row.org_id, above.
    current_role = _role_for_membership(db, user.id, row.org_id, fallback=user.role)
    effective_role = min(row.role, current_role, key=lambda r: _ROLE_RANK[r])

    return CurrentUser(
        id=user.id,
        org_id=row.org_id,
        email=user.email,
        display_name=user.display_name,
        role=effective_role,
        is_active=user.is_active,
        login_method=user.login_method,
        mfa_enrolled=user.mfa_enrolled,
    )


def require_org_access(*roles: str):
    """FastAPI dependency factory: confirms the authenticated user has an
    org_membership row for the org_id path parameter (403 if not), and
    optionally that membership's role (403 if roles are given and it
    isn't among them). Returns a CurrentUser copy with org_id/role
    overwritten to this org's membership — the caller's identity stays
    the same, but which org/role they're acting as becomes whatever the
    URL actually targets, not whatever get_current_user defaulted to
    (ADR 0009 M.4).

    Usage:  Depends(require_org_access())                   # org-scope only
            Depends(require_org_access("msp_admin"))         # + role gate

    Sets app.current_org to org_id *before* reading org_membership. This
    is not a bypass: org_id is exactly the one org this check is about,
    so scoping the read to it is the correct, narrowest-possible RLS
    context, not a broader one being opened up. Contrast with M.2/M.5's
    SECURITY DEFINER functions (migration 0025), which exist for reads
    that must see *every* org in one query — no single app.current_org
    value can serve those; this one only ever needs exactly one, and the
    URL already tells us which.

    Uses set_config(..., true) (the parameterized equivalent of
    SET LOCAL) rather than the f-string-embedded SET LOCAL used
    elsewhere in this module (_resolve_session/_resolve_api_token, and
    tests/conftest.py's _authed) — SET/SET LOCAL itself doesn't accept
    bind parameters, which is why those call sites embed org_id via
    f-string instead (safe there only because it's already a uuid.UUID
    by that point, per their own comments). set_config is available and
    parameterizable, so new code should prefer it; the older call sites
    aren't being touched here since that's a separate, non-M.4 cleanup.
    """
    def _check(
        org_id: uuid.UUID,
        db: Session = Depends(get_session),
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        from .models import OrgMembership

        db.execute(
            text("SELECT set_config('app.current_org', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        membership = db.execute(
            select(OrgMembership).where(
                OrgMembership.user_id == current_user.id, OrgMembership.org_id == org_id
            )
        ).scalar_one_or_none()
        if membership is None:
            raise HTTPException(status_code=403, detail="Cross-org access denied")
        if roles and membership.role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Requires one of: {', '.join(roles)}",
            )
        return replace(current_user, org_id=org_id, role=membership.role)
    return _check


_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_READ_ONLY_ROLES = frozenset({"c3pao_assessor"})


def require_write():
    """Rejects read-only roles (c3pao_assessor) on any non-idempotent method.

    Applied at router level so new mutating routes inherit the gate by
    default rather than by remembering to add it (see
    docs/PLAN-auth-rbac-completion.md, I.2).
    """
    def _check(
        request: Request,
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if request.method in _READ_METHODS:
            return current_user
        if current_user.role in _READ_ONLY_ROLES:
            raise HTTPException(
                status_code=403,
                detail="Read-only role cannot modify data",
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
