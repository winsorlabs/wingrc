"""Multi-org access (M.2 backfill pass 2): existing MSP users -> every org.

See docs/adr/0009-multi-org-user-access.md and app/org_membership.py.
0023 (M.1) backfilled org_membership 1:1 from every existing User row —
each user's access to exactly their own org, unchanged. This migration is
the one-time counterpart to org_membership.py's ongoing
provision_new_org_memberships()/provision_new_user_memberships(): every
existing msp_admin/msp_engineer gets a membership row into every *other*
existing org too, at their own current role — the step that actually
fixes the ADR 0009 defect (an msp_admin locked out of every org but their
own) for accounts that already existed before this migration runs.

Role-at-grant-time: each user's own current `user.role` at migration
time, matching org_membership.py's stated semantics exactly (no special
casing, no derived role) — see that module's docstring for the full
reasoning, restated briefly here since a migration should be readable on
its own without requiring a cross-reference to make sense of what it did.

Idempotent: the NOT EXISTS guard means running this twice (or running it
after M.2's application code has already granted some of the same rows
organically) inserts nothing extra the second time.

No-op on any deployment with exactly one org (nothing to backfill into) —
this deliberately does not change wl-util-1's single-org state; the
multi-org case is covered by dedicated tests
(tests/test_org_membership.py), not by this deployment's own data.

Revision ID: 0024_msp_membership_backfill
Revises: 0023_org_membership
Create Date: 2026-08-08
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024_msp_membership_backfill"
down_revision: str | None = "0023_org_membership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept as a module-level constant, not inlined, so
# tests/test_org_membership.py can load and execute this exact text
# against seeded multi-org fixture data — via
# alembic.script.ScriptDirectory.get_revision(...).module, the same
# dynamic-load mechanism test_migrations.py already uses, not a hand-kept
# copy — there is no duplicate string anywhere to drift out of sync. This
# exists because of a migration-timing problem: by the time integration
# tests run, migrations have already applied to an empty database, so
# there's no realistic way to exercise this against pre-existing
# multi-org data through a real `alembic upgrade` run.
BACKFILL_PASS_2_SQL = """
    INSERT INTO org_membership (id, user_id, org_id, role, created_at)
    SELECT gen_random_uuid(), u.id, o.id, u.role, now()
    FROM "user" u
    CROSS JOIN organization o
    WHERE u.role IN ('msp_admin', 'msp_engineer')
      AND NOT EXISTS (
          SELECT 1 FROM org_membership m
          WHERE m.user_id = u.id AND m.org_id = o.id
      )
"""


def upgrade() -> None:
    op.execute(BACKFILL_PASS_2_SQL)


def downgrade() -> None:
    # Deliberately a no-op, not a guess. By the time this could run, rows
    # inserted by this backfill are indistinguishable from rows the
    # ongoing application logic (org_membership.py, wired into
    # create_org()/invite_user() in this same M.2 slice) has since
    # inserted organically -- both produce the identical shape of row.
    # org_membership has no provenance column (deliberately -- ADR 0009
    # never called for one), so there is no correct way to remove "only
    # the backfilled ones" without risking removing real, later grants
    # too. Leaving the memberships in place is the safe default: it only
    # ever means downgrading past this revision leaves some users with
    # *more* access than pass-1 alone would have given them, never less.
    pass
