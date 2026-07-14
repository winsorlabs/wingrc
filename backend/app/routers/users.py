# noqa: B008
"""User management and API token endpoints (msp_admin gated).

POST /orgs/{org_id}/users            — invite a user (returns raw invite token)
GET  /orgs/{org_id}/users            — list users
PATCH /orgs/{org_id}/users/{user_id} — update role / is_active
POST /orgs/{org_id}/users/{user_id}/reset-mfa — admin MFA reset
DELETE /orgs/{org_id}/users/{user_id} — deactivate

GET    /orgs/{org_id}/api-tokens            — list tokens
POST   /orgs/{org_id}/api-tokens            — create token (raw value returned once)
DELETE /orgs/{org_id}/api-tokens/{token_id} — revoke
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import CurrentUser, get_current_user, require_role
from ..db import get_session
from ..models import ApiToken, User

router = APIRouter(prefix="/orgs/{org_id}", tags=["users"])

_VALID_ROLES = {"msp_admin", "msp_engineer", "customer_poc", "c3pao_assessor"}
_VALID_METHODS = {"local", "sso"}
_INVITE_TTL_HOURS = 48


def _own_org(current_user: CurrentUser, org_id: uuid.UUID) -> None:
    if current_user.org_id != org_id:
        raise HTTPException(status_code=403, detail="Cross-org access denied")


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
    current_user: CurrentUser = Depends(require_role("msp_admin")),
):
    _own_org(current_user, org_id)

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

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

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
    current_user: CurrentUser = Depends(get_current_user),
):
    _own_org(current_user, org_id)
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
    current_user: CurrentUser = Depends(require_role("msp_admin")),
):
    _own_org(current_user, org_id)
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
    current_user: CurrentUser = Depends(require_role("msp_admin")),
):
    """Clear MFA enrollment and backup codes; deactivate until re-enroll."""
    _own_org(current_user, org_id)
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
    current_user: CurrentUser = Depends(require_role("msp_admin")),
):
    _own_org(current_user, org_id)
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


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------

class CreateTokenIn(BaseModel):
    name: str
    role: str
    expires_in_days: int | None = None


@router.post("/api-tokens", status_code=201)
def create_api_token(
    org_id: uuid.UUID,
    body: CreateTokenIn,
    db: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_role("msp_admin", "msp_engineer")),
):
    _own_org(current_user, org_id)

    if body.role not in _VALID_ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role: {body.role}")

    # Token role cannot exceed the creator's role
    _role_rank = {"msp_admin": 4, "msp_engineer": 3, "customer_poc": 2, "c3pao_assessor": 1}
    if _role_rank.get(body.role, 0) > _role_rank.get(current_user.role, 0):
        raise HTTPException(
            status_code=403,
            detail="Cannot create a token with a higher role than your own",
        )

    raw = "wingrc_" + secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.now(UTC) + timedelta(days=body.expires_in_days)

    token = ApiToken(
        org_id=org_id,
        user_id=current_user.id,
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
    current_user: CurrentUser = Depends(require_role("msp_admin", "msp_engineer")),
):
    _own_org(current_user, org_id)
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
    current_user: CurrentUser = Depends(require_role("msp_admin", "msp_engineer")),
):
    _own_org(current_user, org_id)
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
