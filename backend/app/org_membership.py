"""Auto-provisioning for org_membership (ADR 0009, M.2).

Two entry points, called from routers/orgs.py's create_org() and
routers/users.py's invite_user() respectively — the two places a new
(user, org) pairing can come into existence. Neither is wired into
require_org_access() yet; that's M.4. As of M.2, org_membership rows are
fully correct and complete, but User.org_id/User.role remain the only
thing access control actually reads.

Role-at-grant-time semantics (ADR 0009's Decision, stated explicitly per
that section rather than left to fall out of the loop): every grant made
here uses the ROLE THE EXISTING USER ALREADY HOLDS (their own current
role — an msp_admin stays msp_admin on the new org, an msp_engineer stays
msp_engineer) or, for a newly-invited user, the role THEY WERE JUST
INVITED WITH. There is no special case for "the org's creator" — the
creator is simply one of the existing msp_admin/msp_engineer users the
loop in provision_new_org_memberships() already iterates, and gets the
exact same treatment as every other one. A grant is a snapshot at the
moment it's made, not a live link back to the grantee's role elsewhere —
if their role in one org changes later, their other memberships are
unaffected (this is the whole point of "role travels with the membership,
not the person").

Every read of "every existing MSP user" and every write of an
org_membership row here goes through the SECURITY DEFINER functions from
migration 0025 (auth.msp_role_users / auth.grant_org_membership), not a
plain ORM query. See docs/adr/0009-multi-org-user-access.md's "System-
level cross-org operations" subsection for why: both operations must see
or write across every org in the deployment within one request, which is
not expressible as any single value of RLS's per-request app.current_org
GUC. A first attempt at this used plain SQLAlchemy queries and crashed
(or silently under-scoped) against real Postgres — see that ADR section
for the incident. Both org_membership.py functions call the SECURITY
DEFINER functions uniformly for every insert, including the one grant
(a newly invited user's own org) that would actually be RLS-legal without
the bypass — one code path, not a conditional one, matching the
migration's own reasoning for why grant_org_membership takes no
authorization shortcuts of its own: the caller here has already decided
every grant it makes is authorized (it's downstream of require_role/
require_org_access), so there's nothing to special-case.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import Organization

_AUTO_PROVISION_ROLES = frozenset({"msp_admin", "msp_engineer"})

_GRANT_SQL = text(
    "SELECT auth.grant_org_membership(:user_id, :org_id, :role)"
)


def _grant(db: Session, *, user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> bool:
    """True if a new membership row was actually inserted (False if one
    already existed for this (user_id, org_id) pair — grant_org_membership
    is idempotent via ON CONFLICT DO NOTHING and returns NULL in that
    case)."""
    result = db.execute(
        _GRANT_SQL, {"user_id": user_id, "org_id": org_id, "role": role}
    ).scalar()
    return result is not None


def provision_new_org_memberships(db: Session, org_id: uuid.UUID) -> int:
    """A new org was just created: grant every existing msp_admin/
    msp_engineer a membership in it, at their own current role.

    Idempotent (safe to call more than once for the same org) via
    grant_org_membership's own ON CONFLICT DO NOTHING — no existence
    check needed on this side. Returns the number of memberships granted.
    """
    candidates = db.execute(text("SELECT id, role FROM auth.msp_role_users()")).all()
    return sum(
        _grant(db, user_id=user_id, org_id=org_id, role=role)
        for user_id, role in candidates
    )


def provision_new_user_memberships(
    db: Session, *, user_id: uuid.UUID, org_id: uuid.UUID, role: str
) -> int:
    """A new user was just invited/created: grant membership in the org
    they're being invited into (every role gets this — it's the base
    grant, not the auto-provisioning rule), and, for MSP roles only,
    membership in every other existing org too, at the role they were
    invited with.

    Returns the number of memberships granted.
    """
    granted = int(_grant(db, user_id=user_id, org_id=org_id, role=role))

    if role not in _AUTO_PROVISION_ROLES:
        return granted

    # Organization carries no RLS policy of its own (it's the tenant
    # boundary itself, not something scoped by one) — this read needs no
    # SECURITY DEFINER bypass, unlike the grants below.
    other_org_ids = db.execute(
        select(Organization.id).where(Organization.id != org_id)
    ).scalars().all()
    granted += sum(
        _grant(db, user_id=user_id, org_id=other_org_id, role=role)
        for other_org_id in other_org_ids
    )
    return granted
