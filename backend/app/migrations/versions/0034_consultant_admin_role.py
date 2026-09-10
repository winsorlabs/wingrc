"""Add consultant_admin: a restricted platform role for an external
consultant (e.g. a C3PAO hired to help, not to assess) with full access to
compliance data and no access to identity/security administration.

Naming: Jarrod's original suggestion was `mssp_admin`. Rejected in favor of
`consultant_admin` for two reasons, confirmed with Jarrod before building:
(1) an MSSP is the same category of company as the MSP operating the
tenant, so the name doesn't convey "external, restricted"; (2)
ContactDocumentationRole's CHECK constraint already uses `mssp` for a
*documentation* role (a different concept entirely) — two `mssp` meanings
in the same app is the exact naming-drift problem this codebase keeps
having to fix (see CLAUDE.md's "verify reference data" discipline and the
RocketCyber/vendor-CRM-naming lessons elsewhere in this history).

Extends the role CHECK constraint on every table that has one:
  ck_user_role, ck_api_token_role, ck_org_membership_role
ContactDocumentationRole is untouched -- its `mssp` value is a documentation
role (who filled out a form), not a platform auth role, and stays that way.

Rank: auth.py's _ROLE_RANK is renumbered in the same commit --
msp_admin: 5, consultant_admin: 4, msp_engineer: 3, customer_poc: 2,
c3pao_assessor: 1 (was msp_admin: 4 down to c3pao_assessor: 1). Verified
before renumbering that _ROLE_RANK is used only in min()/comparison
expressions (token-mint rank clamp, _resolve_api_token's demotion clamp)
-- no persisted integer depends on the old numbering, so this is a pure
code-level renumber with no data migration required.

What consultant_admin can and cannot do is a route-gate classification,
not implied by rank -- see the "Can/Cannot" audit in this commit's
sibling changes (routers/integrations.py, routers/objectives.py,
routers/audit_log.py, routers/users.py, routers/orgs.py,
org_membership.py) for the full per-route reasoning. Not read-only:
consultant_admin is not added to auth.py's _READ_ONLY_ROLES
(c3pao_assessor stays the only member, unchanged by this migration).

Revision ID: 0034_consultant_admin_role
Revises: 0033_resolve_user_identity
Create Date: 2026-09-11
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0034_consultant_admin_role"
down_revision: str | None = "0033_resolve_user_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ROLES = "'msp_admin','msp_engineer','customer_poc','c3pao_assessor'"
_NEW_ROLES = "'msp_admin','consultant_admin','msp_engineer','customer_poc','c3pao_assessor'"

_CONSTRAINTS = (
    ("ck_user_role", "user"),
    ("ck_api_token_role", "api_token"),
    ("ck_org_membership_role", "org_membership"),
)


def upgrade() -> None:
    for name, table in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, f"role IN ({_NEW_ROLES})")


def downgrade() -> None:
    # Reversed order from upgrade() only for symmetry; these three
    # constraints are independent of each other, so order doesn't matter
    # functionally. A downgrade with any existing consultant_admin row
    # still present fails the new CHECK, same as any other narrowing
    # downgrade in this codebase -- the operator is expected to reassign
    # or remove those rows first, not something this migration can safely
    # do on their behalf.
    for name, table in _CONSTRAINTS:
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, f"role IN ({_OLD_ROLES})")
