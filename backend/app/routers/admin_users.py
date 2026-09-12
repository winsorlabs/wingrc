# noqa: B008
"""Deployment-wide user directory (ADR 0009 M.7 / G.11).

Endpoints:
  GET /admin/users          Every user in the deployment, their home org,
                             and every org_membership row they hold.
  GET /admin/users/msp-org  The designated MSP org (deployment_settings.
                             msp_org_id), for the "invite a new MSP user
                             into our MSP" action. null on a fresh
                             deployment that hasn't run bootstrap-admin yet.

Grant/revoke itself (M.8) is NOT here -- it lives in routers/users.py as
POST/DELETE /orgs/{org_id}/memberships, since it's an org-scoped action
(the target org's own msp_admin grants access into it) reached from a
user found via this directory, not a deployment-tier action in its own
right. This file is only the two reads that don't fit that shape: a
cross-org listing, and a small settings pointer lookup.

Role gate: msp_admin only, router-wide -- NOT consultant_admin, unlike
Integrations (D.1) and Tools (G.9). Those are compliance-data
configuration; this is identity administration across every client on
the deployment, the exact line lib/roles.ts's canSeeUsers/
canSeeIntegrations distinction already draws for the org-scoped
equivalent. See lib/roles.ts's USER_DIRECTORY_ROLES for the mirrored
frontend gate.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..auth import require_role
from ..db import get_session
from ..models import DeploymentSettings, Organization

router = APIRouter(
    prefix="/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(require_role("msp_admin"))],
)


class MembershipOut(BaseModel):
    org_id: uuid.UUID
    org_name: str
    role: str


class UserDirectoryEntryOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    home_org_id: uuid.UUID
    home_org_name: str
    deleted_at: str | None
    memberships: list[MembershipOut]


class MspOrgOut(BaseModel):
    org_id: uuid.UUID
    org_name: str


@router.get("", response_model=list[UserDirectoryEntryOut])
def list_user_directory(session: Session = Depends(get_session)) -> list[UserDirectoryEntryOut]:
    """Cross-org by construction -- auth.all_users_directory() (migration
    0040) bypasses org_membership's RLS the same way msp_role_users()/
    my_org_memberships() do; "every user, every membership, every org"
    cannot be expressed as any single app.current_org value.

    Deliberately does not surface User.role. See migration 0040's own
    docstring: as of M.4, User.role no longer governs access anywhere in
    the normal request path (auth.py:_role_for_membership treats it as a
    fallback for a membership row that should always exist, logging a
    warning when it actually fires) -- showing a bare "role" column next
    to real per-org membership roles here would suggest it means
    something it doesn't.
    """
    rows = session.execute(
        text(
            "SELECT id, email, display_name, home_org_id, deleted_at, memberships "
            "FROM auth.all_users_directory()"
        )
    ).all()
    if not rows:
        return []

    org_ids = {r.home_org_id for r in rows}
    org_names = {
        o.id: o.name
        for o in session.scalars(select(Organization).where(Organization.id.in_(org_ids)))
    }
    return [
        UserDirectoryEntryOut(
            id=r.id,
            email=r.email,
            display_name=r.display_name,
            home_org_id=r.home_org_id,
            home_org_name=org_names.get(r.home_org_id, ""),
            deleted_at=r.deleted_at.isoformat() if r.deleted_at else None,
            memberships=[MembershipOut(**m) for m in r.memberships],
        )
        for r in rows
    ]


@router.get("/msp-org", response_model=MspOrgOut | None)
def get_msp_org(session: Session = Depends(get_session)) -> MspOrgOut | None:
    """The designated MSP org (ADR 0009's deployment_settings.msp_org_id),
    resolved for the "invite a new MSP user into our MSP" UI action.

    Ordinary read, no bypass needed -- DeploymentSettings carries no RLS
    (deployment-wide, same tier as Product/Framework/IntegrationConnection).
    Returns null, not an error, when no row exists yet: a fresh
    deployment that hasn't run `manage.py bootstrap-admin` has zero
    DeploymentSettings rows by design (see that table's own model
    docstring) -- this must degrade gracefully here, not break login or
    any other path, since nothing about authentication depends on this
    table at all.
    """
    settings = session.get(DeploymentSettings, 1)
    if settings is None:
        return None
    org = session.get(Organization, settings.msp_org_id)
    return MspOrgOut(org_id=settings.msp_org_id, org_name=org.name if org else "")
