# noqa: B008
"""User management and API token endpoints (msp_admin gated).

POST /orgs/{org_id}/users            — invite a user (returns raw invite token)
GET  /orgs/{org_id}/users            — list users
PATCH /orgs/{org_id}/users/{user_id} — update role / is_active
POST /orgs/{org_id}/users/{user_id}/reset-mfa — admin MFA reset
DELETE /orgs/{org_id}/users/{user_id} — deactivate
POST /orgs/{org_id}/users/{user_id}/unlock — clear lockout state (I.5)
POST /orgs/{org_id}/users/{user_id}/reset-password — issue a one-time reset token (I.5)
POST /orgs/{org_id}/users/{user_id}/delete — permanent hard-delete (ADR 0006, zero-history only)
POST /orgs/{org_id}/users/{user_id}/anonymize — scrub PII, keep row + audit trail (ADR 0006)
POST /orgs/{org_id}/users/api        — create an API user (service account) + its first token

GET    /orgs/{org_id}/api-tokens            — list tokens
POST   /orgs/{org_id}/api-tokens            — create token (raw value returned once)
DELETE /orgs/{org_id}/api-tokens/{token_id} — revoke
"""
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import (
    _ROLE_RANK,
    CurrentUser,
    generate_secret,
    require_org_access,
    require_write,
    revoke_user_sessions,
)
from ..db import get_session
from ..models import ApiToken, AuditLog, User
from ..org_membership import provision_new_user_memberships

router = APIRouter(
    prefix="/orgs/{org_id}",
    tags=["users"],
    dependencies=[Depends(require_write())],
)

_VALID_ROLES = {"msp_admin", "msp_engineer", "customer_poc", "c3pao_assessor"}
_VALID_METHODS = {"local", "sso"}
_INVITE_TTL_HOURS = 48


def _actor_type(current_user: CurrentUser) -> str:
    """API tokens can carry any role including msp_admin, so a token-driven
    call is not the same thing as a human at the keyboard — actor_type must
    reflect that rather than hardcoding "user" regardless of login_method.
    """
    return "api" if current_user.login_method == "api" else "user"


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

class InviteUserIn(BaseModel):
    email: EmailStr
    display_name: str
    role: str
    login_method: str = "local"
    contact_id: uuid.UUID | None = None


@router.post("/users", status_code=201)
def invite_user(
    org_id: uuid.UUID,
    body: InviteUserIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin")),
):
    if body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role: {body.role}")
    if body.login_method not in _VALID_METHODS:
        raise HTTPException(status_code=422, detail=f"Invalid login_method: {body.login_method}")

    existing = db.execute(
        select(User).where(User.home_org_id == org_id, User.email == body.email)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="A user with this email already exists in this org",
        )

    raw_token, token_hash = generate_secret()

    user = User(
        home_org_id=org_id,
        contact_id=body.contact_id,
        email=body.email,
        display_name=body.display_name,
        login_method=body.login_method,
        role=body.role,
        is_active=False,
        invite_token_hash=token_hash,
        invite_expires_at=datetime.now(UTC) + timedelta(hours=_INVITE_TTL_HOURS),
    )
    db.add(user)
    db.flush()

    # ADR 0009 (M.2): grant membership in the org being invited into (every
    # role), plus — for MSP roles only — every other existing org, at the
    # role they're invited with. See org_membership.py's module docstring.
    provision_new_user_memberships(db, user_id=user.id, org_id=org_id, role=body.role)

    log_event(
        db,
        org_id=org_id,
        action="user.invite",
        entity_type="user",
        entity_id=user.id,
        after_value={"email": body.email, "role": body.role, "login_method": body.login_method},
        context={"inviter": str(current_user.id)},
        actor=str(current_user.id),
        actor_type=_actor_type(current_user),
    )
    db.commit()

    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "login_method": user.login_method,
        "is_active": user.is_active,
        "invite_token": raw_token,  # shown once — admin emails this to the user
        "invite_expires_at": user.invite_expires_at.isoformat(),
    }


@router.get("/users")
def list_users(
    org_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access()),
):
    rows = db.execute(
        select(User).where(User.home_org_id == org_id).order_by(User.created_at)
    ).scalars().all()
    return [_user_out(u) for u in rows]


class PatchUserIn(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    display_name: str | None = None


@router.patch("/users/{user_id}")
def patch_user(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    body: PatchUserIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin")),
):
    user = _get_user(db, org_id, user_id)

    if body.role is not None:
        if body.role not in _VALID_ROLES:
            raise HTTPException(status_code=422, detail=f"Invalid role: {body.role}")
        if body.role != user.role:
            log_event(
                db,
                org_id=org_id,
                action="user.role_change",
                entity_type="user",
                entity_id=user.id,
                before_value={"role": user.role},
                after_value={"role": body.role},
                context={"admin": str(current_user.id)},
                actor=str(current_user.id),
                actor_type=_actor_type(current_user),
            )
        user.role = body.role
    if body.is_active is not None:
        if body.is_active and user.deleted_at is not None:
            raise HTTPException(
                status_code=409,
                detail="This user has been permanently anonymized and cannot be reactivated",
            )
        if body.is_active != user.is_active:
            log_event(
                db,
                org_id=org_id,
                action="user.activation_change",
                entity_type="user",
                entity_id=user.id,
                before_value={"is_active": user.is_active},
                after_value={"is_active": body.is_active},
                context={"admin": str(current_user.id)},
                actor=str(current_user.id),
                actor_type=_actor_type(current_user),
            )
            if body.is_active is False:
                revoke_user_sessions(db, user.id)
        user.is_active = body.is_active
    if body.display_name is not None:
        user.display_name = body.display_name

    db.commit()
    return _user_out(user)


@router.post("/users/{user_id}/reset-mfa", status_code=200)
def reset_user_mfa(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin")),
):
    """Clear MFA enrollment and backup codes; deactivate until re-enroll."""
    user = _get_user(db, org_id, user_id)

    user.totp_secret = None
    user.mfa_enrolled = False
    user.is_active = False
    user.requires_admin_reset = False

    db.execute(
        text("DELETE FROM mfa_backup_code WHERE user_id = :uid"),
        {"uid": user_id},
    )
    db.commit()

    log_event(
        db,
        org_id=org_id,
        action="user.mfa_reset",
        entity_type="user",
        entity_id=user_id,
        context={"admin": str(current_user.id)},
        actor=str(current_user.id),
        actor_type=_actor_type(current_user),
    )
    db.commit()
    return {"ok": True}


@router.delete("/users/{user_id}", status_code=200)
def deactivate_user(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin")),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
    user = _get_user(db, org_id, user_id)
    was_active = user.is_active
    user.is_active = False
    revoke_user_sessions(db, user_id)
    log_event(
        db,
        org_id=org_id,
        action="user.deactivate",
        entity_type="user",
        entity_id=user.id,
        before_value={"is_active": was_active},
        after_value={"is_active": False},
        context={"admin": str(current_user.id)},
        actor=str(current_user.id),
        actor_type=_actor_type(current_user),
    )
    db.commit()
    return {"ok": True}


def _audit_history_count(db: Session, org_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """ADR 0006's definition of "history": any audit_log row where the user
    is the actor, or the row's entity is this user record.
    """
    return db.execute(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.org_id == org_id,
            or_(
                AuditLog.actor == str(user_id),
                and_(AuditLog.entity_type == "user", AuditLog.entity_id == user_id),
            ),
        )
    ).scalar_one()


@router.post("/users/{user_id}/delete", status_code=200)
def delete_user_permanent(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin")),
):
    """Permanent hard-delete (ADR 0006) — only for a deactivated user with
    zero audit_log footprint. Never falls back to anonymizing on its own if
    history exists; the admin must choose that as a separate, deliberate
    action via /anonymize (see UsersPanel's confirm flow).
    """
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user = _get_user(db, org_id, user_id)
    if user.deleted_at is not None:
        raise HTTPException(status_code=400, detail="User has already been anonymized")
    if user.is_active:
        raise HTTPException(
            status_code=400,
            detail="User must be deactivated before it can be deleted",
        )

    history_count = _audit_history_count(db, org_id, user_id)
    if history_count > 0:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This user has {history_count} audit log "
                f"{'entry' if history_count == 1 else 'entries'} and can't be "
                "deleted without breaking the audit trail. Anonymize instead? "
                "Their PII is scrubbed, the account stays disabled, and audit "
                "history is preserved under an anonymized identifier."
            ),
        )

    log_event(
        db,
        org_id=org_id,
        action="user.delete",
        entity_type="user",
        entity_id=user.id,
        after_value={"deleted": True},
        context={"admin": str(current_user.id)},
        actor=str(current_user.id),
        actor_type=_actor_type(current_user),
    )
    # user_session/mfa_backup_code/api_token/password_history all carry
    # ON DELETE CASCADE FKs to user.id (see ADR 0006) — one DELETE on the
    # parent row is sufficient; Postgres removes the rest.
    db.execute(text('DELETE FROM "user" WHERE id = :uid'), {"uid": user_id})
    db.commit()
    return {"ok": True, "deleted": True}


@router.post("/users/{user_id}/anonymize", status_code=200)
def anonymize_user(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin")),
):
    """Scrub PII, keep the row so audit_log.actor/entity_id still resolve
    (ADR 0006, option C). audit_log itself is never touched — this only
    ever inserts one new, non-PII user.anonymize event via log_event().
    """
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot anonymize your own account")
    user = _get_user(db, org_id, user_id)
    if user.deleted_at is not None:
        raise HTTPException(status_code=400, detail="User has already been anonymized")
    if user.is_active:
        raise HTTPException(
            status_code=400,
            detail="User must be deactivated before it can be anonymized",
        )

    user.email = f"deleted-{user.id}@wingrc.invalid"
    user.display_name = "Deleted user"
    user.entra_oid = None
    user.totp_secret = None
    user.mfa_enrolled = False
    user.password_hash = None
    user.deleted_at = datetime.now(UTC)

    # Pure auth mechanics, no compliance narrative (ADR 0006) — cascade
    # these explicitly since the user row itself is NOT deleted here, so the
    # ON DELETE CASCADE FKs never fire. password_history postdates the
    # ADR's own table but shares the identical shape (FK to user.id,
    # ON DELETE CASCADE, nothing but password hashes), so it's cleared the
    # same way as the three tables the ADR names.
    db.execute(text("DELETE FROM user_session WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM mfa_backup_code WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM api_token WHERE user_id = :uid"), {"uid": user_id})
    db.execute(text("DELETE FROM password_history WHERE user_id = :uid"), {"uid": user_id})

    log_event(
        db,
        org_id=org_id,
        action="user.anonymize",
        entity_type="user",
        entity_id=user.id,
        after_value={"anonymized": True},
        context={"admin": str(current_user.id)},
        actor=str(current_user.id),
        actor_type=_actor_type(current_user),
    )
    db.commit()
    return _user_out(user)


@router.post("/users/{user_id}/unlock", status_code=200)
def unlock_user(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin")),
):
    """Clear lockout state. Does not touch MFA enrollment or password —
    reset_user_mfa and reset_user_password are separate, deliberate actions.
    """
    user = _get_user(db, org_id, user_id)
    before = {
        "locked_until": user.locked_until.isoformat() if user.locked_until else None,
        "failed_login_count": user.failed_login_count,
        "lockout_count": user.lockout_count,
        "requires_admin_reset": user.requires_admin_reset,
    }
    user.locked_until = None
    user.failed_login_count = 0
    user.lockout_count = 0
    user.requires_admin_reset = False

    log_event(
        db,
        org_id=org_id,
        action="user.unlock",
        entity_type="user",
        entity_id=user.id,
        before_value=before,
        after_value={
            "locked_until": None,
            "failed_login_count": 0,
            "lockout_count": 0,
            "requires_admin_reset": False,
        },
        context={"admin": str(current_user.id)},
        actor=str(current_user.id),
        actor_type=_actor_type(current_user),
    )
    db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/reset-password", status_code=200)
def reset_user_password(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin")),
):
    """Mint a one-time password-reset token, redeemed at POST /auth/set-password.

    Reuses invite_token_hash/invite_expires_at and the invite redemption
    endpoint rather than a parallel reset-token code path — see
    docs/PLAN-auth-rbac-completion.md I.5. Revokes the user's live sessions
    immediately: an admin-initiated reset must not leave an already-open
    session (possibly the attacker's, if this reset was prompted by a
    suspected compromise) valid in the meantime.
    """
    user = _get_user(db, org_id, user_id)
    raw_token, token_hash = generate_secret()
    user.invite_token_hash = token_hash
    user.invite_expires_at = datetime.now(UTC) + timedelta(hours=_INVITE_TTL_HOURS)
    revoke_user_sessions(db, user.id)

    log_event(
        db,
        org_id=org_id,
        action="user.password_reset_issued",
        entity_type="user",
        entity_id=user.id,
        context={"admin": str(current_user.id)},
        actor=str(current_user.id),
        actor_type=_actor_type(current_user),
    )
    db.commit()
    return {
        "reset_token": raw_token,  # shown once — admin delivers this out of band
        "expires_at": user.invite_expires_at.isoformat(),
    }


class CreateApiUserIn(BaseModel):
    display_name: str
    role: str


@router.post("/users/api", status_code=201)
def create_api_user(
    org_id: uuid.UUID,
    body: CreateApiUserIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin")),
):
    """Create a login_method='api' service-account user and mint its first
    token in one transaction. No email field: the address is a generated,
    non-deliverable placeholder — this account never receives mail, it only
    authenticates via the returned token. Organization has no slug column,
    so the org_id's short form fills that role in the generated address.
    """
    if body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role: {body.role}")

    email = f"api-{secrets.token_urlsafe(6)}@{org_id.hex[:8]}.internal"

    user = User(
        home_org_id=org_id,
        contact_id=None,
        email=email,
        display_name=body.display_name,
        login_method="api",
        role=body.role,
        is_active=True,
    )
    db.add(user)
    db.flush()

    # ADR 0009: same base grant invite_user() already makes. Without this,
    # an API user created here would have zero org_membership rows -- once
    # M.4 makes org_membership authoritative for auth, that account
    # couldn't be resolved to a role anywhere, including at its own home
    # org.
    provision_new_user_memberships(db, user_id=user.id, org_id=org_id, role=body.role)

    raw, token_hash = generate_secret("wingrc_")
    token = ApiToken(
        org_id=org_id,
        user_id=user.id,
        name=f"{body.display_name} (default)",
        token_hash=token_hash,
        role=body.role,
    )
    db.add(token)
    db.flush()

    log_event(
        db,
        org_id=org_id,
        action="api_user.create",
        entity_type="user",
        entity_id=user.id,
        after_value={"email": user.email, "role": user.role, "display_name": user.display_name},
        context={"creator": str(current_user.id), "token_id": str(token.id)},
        actor=str(current_user.id),
        actor_type=_actor_type(current_user),
    )
    db.commit()

    return {
        "id": str(user.id),
        "username": user.email,
        "role": user.role,
        "token": raw,  # shown once
    }


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------

class CreateTokenIn(BaseModel):
    name: str
    role: str
    expires_in_days: int | None = None
    user_id: uuid.UUID | None = None  # None = self-issue (unchanged default behavior)


@router.post("/api-tokens", status_code=201)
def create_api_token(
    org_id: uuid.UUID,
    body: CreateTokenIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin", "msp_engineer")),
):
    if body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role: {body.role}")

    on_behalf_of = body.user_id is not None and body.user_id != current_user.id
    if on_behalf_of:
        if current_user.role != "msp_admin":
            raise HTTPException(
                status_code=403,
                detail="Only msp_admin may create tokens on behalf of another user",
            )
        target_user = _get_user(db, org_id, body.user_id)
        target_user_id = target_user.id
        rank_against_role = target_user.role
    else:
        target_user_id = current_user.id
        rank_against_role = current_user.role

    # Token role cannot exceed the rank of whoever it's being issued for
    if _ROLE_RANK.get(body.role, 0) > _ROLE_RANK.get(rank_against_role, 0):
        detail = (
            "Cannot create a token with a higher role than your own"
            if not on_behalf_of
            else "Cannot create a token with a higher role than the target user's"
        )
        raise HTTPException(status_code=403, detail=detail)

    raw, token_hash = generate_secret("wingrc_")
    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.now(UTC) + timedelta(days=body.expires_in_days)

    token = ApiToken(
        org_id=org_id,
        user_id=target_user_id,
        name=body.name,
        token_hash=token_hash,
        role=body.role,
        expires_at=expires_at,
    )
    db.add(token)
    db.flush()

    log_event(
        db,
        org_id=org_id,
        action="api_token.create",
        entity_type="api_token",
        entity_id=token.id,
        after_value={
            "name": token.name,
            "role": token.role,
            "expires_at": token.expires_at.isoformat() if token.expires_at else None,
            "on_behalf_of": on_behalf_of,
        },
        context={"issuer": str(current_user.id), "user_id": str(target_user_id)},
        actor=str(current_user.id),
        actor_type=_actor_type(current_user),
    )
    db.commit()

    return {
        "id": str(token.id),
        "name": token.name,
        "role": token.role,
        "expires_at": token.expires_at.isoformat() if token.expires_at else None,
        "token": raw,  # shown once
    }


@router.get("/api-tokens")
def list_api_tokens(
    org_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin", "msp_engineer")),
):
    rows = db.execute(
        select(ApiToken)
        .where(ApiToken.org_id == org_id, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at)
    ).scalars().all()
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "role": t.role,
            "created_at": t.created_at.isoformat(),
            "expires_at": t.expires_at.isoformat() if t.expires_at else None,
            "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
        }
        for t in rows
    ]


@router.delete("/api-tokens/{token_id}", status_code=200)
def revoke_api_token(
    org_id: uuid.UUID,
    token_id: uuid.UUID,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_org_access("msp_admin", "msp_engineer")),
):
    token = db.execute(
        select(ApiToken).where(ApiToken.id == token_id, ApiToken.org_id == org_id)
    ).scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    was_revoked_at = token.revoked_at
    token.revoked_at = datetime.now(UTC)
    log_event(
        db,
        org_id=org_id,
        action="api_token.revoke",
        entity_type="api_token",
        entity_id=token.id,
        before_value={"revoked_at": was_revoked_at.isoformat() if was_revoked_at else None},
        after_value={"revoked_at": token.revoked_at.isoformat()},
        context={"revoker": str(current_user.id), "token_name": token.name},
        actor=str(current_user.id),
        actor_type=_actor_type(current_user),
    )
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user(db: Session, org_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = db.execute(
        select(User).where(User.id == user_id, User.home_org_id == org_id)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _user_out(u: User) -> dict:
    return {
        "id": str(u.id),
        # JSON key stays "org_id" -- this is the API response contract,
        # unaffected by the User.home_org_id rename (M.3).
        "org_id": str(u.home_org_id),
        "contact_id": str(u.contact_id) if u.contact_id else None,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "login_method": u.login_method,
        "is_active": u.is_active,
        "mfa_enrolled": u.mfa_enrolled,
        "requires_admin_reset": u.requires_admin_reset,
        # I.5 deviation from the original spec: the spec's users.py changes
        # only added the unlock/reset-password endpoints, but the unlock UI
        # can't be honest without seeing lockout state that predates
        # requires_admin_reset (1st/2nd lockout sets locked_until without
        # requires_admin_reset — see auth.py:apply_failed_login). Added here
        # rather than left implicit.
        "locked_until": u.locked_until.isoformat() if u.locked_until else None,
        "lockout_count": u.lockout_count,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat(),
        # ADR 0006: distinguishes permanent anonymization from ordinary
        # is_active=False — the UI must never offer to reactivate a row
        # where this is set.
        "deleted_at": u.deleted_at.isoformat() if u.deleted_at else None,
    }
