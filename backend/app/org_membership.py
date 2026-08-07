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
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Organization, OrgMembership, User

_AUTO_PROVISION_ROLES = frozenset({"msp_admin", "msp_engineer"})


def provision_new_org_memberships(db: Session, org_id: uuid.UUID) -> int:
    """A new org was just created: grant every existing msp_admin/
    msp_engineer a membership in it, at their own current role.

    Existence-checked (not blind-inserted) so this is safe to call more
    than once for the same org — matches this codebase's established
    dedup idiom (see engine.py's evidence-task fan-out) rather than
    reaching for a database-level ON CONFLICT, which nothing else here
    uses. Returns the number of memberships granted.
    """
    already_granted = set(
        db.execute(
            select(OrgMembership.user_id).where(OrgMembership.org_id == org_id)
        ).scalars().all()
    )
    candidates = db.execute(
        select(User.id, User.role).where(User.role.in_(_AUTO_PROVISION_ROLES))
    ).all()

    granted = 0
    for user_id, role in candidates:
        if user_id in already_granted:
            continue
        db.add(OrgMembership(user_id=user_id, org_id=org_id, role=role))
        granted += 1
    return granted


def provision_new_user_memberships(
    db: Session, *, user_id: uuid.UUID, org_id: uuid.UUID, role: str
) -> int:
    """A new user was just invited/created: grant membership in the org
    they're being invited into (every role gets this — it's the base
    grant, not the auto-provisioning rule), and, for MSP roles only,
    membership in every other existing org too, at the role they were
    invited with.

    `user_id` is always brand new here (invite_user() only ever creates a
    fresh User row as of M.2 — the re-invite-into-a-second-org path for an
    *existing* identity is M.8), so no existence check is needed before
    inserting: there cannot already be a membership row for a user_id that
    didn't exist a moment ago. Returns the number of memberships granted.
    """
    db.add(OrgMembership(user_id=user_id, org_id=org_id, role=role))
    granted = 1

    if role not in _AUTO_PROVISION_ROLES:
        return granted

    other_org_ids = db.execute(
        select(Organization.id).where(Organization.id != org_id)
    ).scalars().all()
    for other_org_id in other_org_ids:
        db.add(OrgMembership(user_id=user_id, org_id=other_org_id, role=role))
        granted += 1
    return granted
