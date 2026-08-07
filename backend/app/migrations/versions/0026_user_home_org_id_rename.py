"""Multi-org access (M.3): rename user.org_id to user.home_org_id.

See docs/adr/0009-multi-org-user-access.md's Migration path, step 4.
Purely mechanical -- no authorization logic changes, no behavior changes.
This is deliberately its own slice, landing before M.4's
require_org_access() enforcement cutover, so that diff is reviewable as
the actual authorization change and not tangled up with a wide rename.

Under multi-org membership, a user's accessible orgs come from
org_membership, not a single fixed column on `user` -- `org_id` was never
an accurate name for what this column is *for* once that's true. It
remains a real, permanent column (not a migration artifact to drop
later): it's the anchor `get_current_user` uses to set app.current_org
for the account-mechanics tables (user, user_session, mfa_backup_code,
api_token, password_history) before any business-org context is known,
and the audit-log anchor for account-level events (login, password
change, ...) that aren't scoped to any particular business org. See the
ADR's Design section for both.

`ALTER TABLE ... RENAME COLUMN` propagates automatically to every
dependent object Postgres tracks by catalog dependency rather than by
name -- the `user_org` RLS policy (0015) and the `ck_user_login_method`/
`ck_user_role` CHECK constraints all continue working unchanged, still
referencing the same column, now under its new name, with no separate
statement needed for any of them. The `uq_user_org_email` unique
constraint is explicitly renamed alongside the column (not automatic --
constraint *names* are just labels, not tracked dependencies) so it
doesn't keep saying "org_email" once the column it constrains no longer
does.

Revision ID: 0026_user_home_org_id_rename
Revises: 0025_org_membership_security_definer
Create Date: 2026-08-09
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026_user_home_org_id_rename"
down_revision: str | None = "0025_org_membership_security_definer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('ALTER TABLE "user" RENAME COLUMN org_id TO home_org_id')
    op.execute(
        'ALTER TABLE "user" RENAME CONSTRAINT uq_user_org_email '
        "TO uq_user_home_org_id_email"
    )


def downgrade() -> None:
    op.execute(
        'ALTER TABLE "user" RENAME CONSTRAINT uq_user_home_org_id_email '
        "TO uq_user_org_email"
    )
    op.execute('ALTER TABLE "user" RENAME COLUMN home_org_id TO org_id')
