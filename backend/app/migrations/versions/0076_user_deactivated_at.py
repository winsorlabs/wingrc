"""Offboarding gets its own column: user.deactivated_at.

`user.is_active = false` meant three things: an invitee who has not
enrolled, an account whose MFA an administrator reset, and an account an
administrator deactivated. The first two must be able to enrol, so the
enrolment path could not refuse an inactive account, and a deactivated
person with a working password enrolled a new authenticator and was
active again (docs/roadmap.md, "A deactivated user could sign back in").

Backfill: the row cannot tell the three apart, but the audit log can. An
account is backfilled when it is inactive now and its latest activation
event is a deactivation: `user.deactivate`, or `user.activation_change`
to false. `deactivated_at` takes that event's time. An invitee has no
such event, and an MFA reset writes `user.mfa_reset`, which is not one,
so neither is locked out. A reactivation after the deactivation
(`user.activation_change` to true) is the latest event and leaves the
account alone. On live (2026-11-20), 3 of 5 users were inactive, all
three anonymized, each with a `user.deactivate`; none was MFA-enrolled.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0076_user_deactivated_at"
down_revision: str | None = "0075_responsibility_set_by"
branch_labels = None
depends_on = None

BACKFILL_SQL = """
UPDATE "user" u
   SET deactivated_at = latest.created_at
  FROM (
        SELECT DISTINCT ON (entity_id) entity_id, action, after_value, created_at
          FROM audit_log
         WHERE entity_type = 'user'
           AND action IN ('user.deactivate', 'user.activation_change', 'user.reactivate')
         ORDER BY entity_id, created_at DESC, id DESC
       ) latest
 WHERE u.id = latest.entity_id
   AND NOT u.is_active
   AND u.deactivated_at IS NULL
   AND (latest.action = 'user.deactivate'
        OR (latest.action = 'user.activation_change'
            AND latest.after_value ->> 'is_active' = 'false'))
"""


def upgrade() -> None:
    op.add_column(
        "user", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_column("user", "deactivated_at")
