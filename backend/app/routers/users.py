# noqa: B008
"""User management and API token endpoints (msp_admin gated).

POST /orgs/{org_id}/users            — invite a user (returns raw invite token)
GET  /orgs/{org_id}/users            — list users
PATCH /orgs/{org_id}/users/{user_id} — update role / is_active
POST /orgs/{org_id}/users/{user_id}/reset-mfa — admin MFA reset
DELETE /orgs/{org_id}/users/{user_id} — deactivate
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
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import CurrentUser, generate_secret, require_org_access
from ..db import get_session
from ..models import ApiToken, User

router = APIRouter(prefix="/orgs/{org_id}", tags=["users"])

_VALID_ROLES = {"msp_admin", "msp_engineer", "customer_poc", "c3pao_assessor"}
_VALID_METHODS = {"local", "sso"}
_INVITE_TTL_HOURS = 48
_role_rank = {"msp_admin": 4, "msp_engineer": 3, "customer_poc": 2, "c3pao_assessor": 1}


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
        select(User).where(User.org_id == org_id, User.email == body.email)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="A user with this email already exists in this org",
        )

    raw_token, token_hash = generate_secret()

    user = User(
        org_id=org_id,
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

    log_event(
        db,
        org_id=org_id,
        action="user.invite",
        entity_type="user",
        entity_id=user.id,
        after_value={"email": body.email, "role": body.role, "login_method": body.login_method},
        context={"inviter": str(current_user.id)},
        actor=str(current_user.id),
        actor_type="user",
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
        select(User).where(User.org_id == org_id).order_by(User.created_at)
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
        user.role = body.role
    if body.is_active is not None:
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
        actor_type="user",
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
    user.is_active = False
    db.execute(
        text(
            "UPDATE user_session SET revoked_at = :now"
            " WHERE user_id = :uid AND revoked_at IS NULL"
        ),
        {"now": datetime.now(UTC), "uid": user_id},
    )
    db.commit()
    return {"ok": True}


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
        org_id=org_id,
        contact_id=None,
        email=email,
        display_name=body.display_name,
        login_method="api",
        role=body.role,
        is_active=True,
    )
    db.add(user)
    db.flush()

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
        actor_type="api" if current_user.login_method == "api" else "user",
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
    if _role_rank.get(body.role, 0) > _role_rank.get(rank_against_role, 0):
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
    token.revoked_at = datetime.now(UTC)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user(db: Session, org_id: uuid.UUID, user_id: uuid.UUID) -> User:
    user = db.execute(
        select(User).where(User.id == user_id, User.org_id == org_id)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _user_out(u: User) -> dict:
    return {
        "id": str(u.id),
        "org_id": str(u.org_id),
        "contact_id": str(u.contact_id) if u.contact_id else None,
        "email": u.email,
        "display_name": u.display_name,
        "role": u.role,
        "login_method": u.login_method,
        "is_active": u.is_active,
        "mfa_enrolled": u.mfa_enrolled,
        "requires_admin_reset": u.requires_admin_reset,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat(),
    }
