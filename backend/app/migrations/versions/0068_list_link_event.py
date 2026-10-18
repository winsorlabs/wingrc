"""A list's evidence links are in its own history -- roadmap L.10.

`list_link_event` records every time a version of a list was linked to an
objective as evidence, moved forward, or unlinked, beside the list's
versions and tag events. Until now the answer to "was this list ever
attached?" was in the audit log only, which `consultant_admin` cannot read
and no operator should have to search; the first time it was asked on
live (2026-10-18) it took the request log to answer. L.2.2 put tag changes
in the list's history for the same reason: two histories in two places is
how the question goes unanswered.

The record names its version, objective and assessment as they were, the
assessment's name included, so it survives a rename. It is a record, not a
link: `list_library.tag_removal_blockers` still reads the live
EvidenceStateLinks, which are the authority. Append-only, as
`list_tag_event` is (migration 0065).

No backfill: L.4 deployed 2026-10-18 and no list had been linked on live
when this was written (zero `artifact_type = 'list'` evidence rows). A
deployment with links made between 0067 and this has them in the audit log
(`evidence.list_link` and its siblings) and not here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0068_list_link_event"
down_revision: str | None = "0067_list_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "list_link_event",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "list_id", UUID(as_uuid=True),
            sa.ForeignKey("list_definition.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"),
            nullable=False, index=True,
        ),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("change", sa.String(20), nullable=False),
        sa.Column(
            "list_version_id", UUID(as_uuid=True),
            sa.ForeignKey("list_version.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("from_version_numbers", JSONB(), nullable=True),
        # No FK: the record outlives the assessment, which is why its name
        # is kept beside the id.
        sa.Column("assessment_id", UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_name", sa.Text(), nullable=False),
        sa.Column("control_key", sa.String(40), nullable=False),
        sa.Column("objective_key", sa.String(5), nullable=False),
        sa.Column("saved_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "change IN ('linked', 'moved_forward', 'unlinked')", name="ck_list_link_event_change"
        ),
        sa.CheckConstraint(
            "(change = 'moved_forward') = (from_version_numbers IS NOT NULL)",
            name="ck_list_link_event_from",
        ),
    )
    op.execute("ALTER TABLE list_link_event ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY list_link_event_tenant_isolation ON list_link_event
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )
    op.execute(
        "CREATE FUNCTION list_link_event_immutable() RETURNS trigger LANGUAGE plpgsql AS "
        "$fn$ BEGIN RAISE EXCEPTION "
        "'list_link_event is append-only: a link change is recorded by a new event'; END $fn$"
    )
    op.execute(
        "CREATE TRIGGER list_link_event_no_update BEFORE UPDATE ON list_link_event "
        "FOR EACH ROW EXECUTE FUNCTION list_link_event_immutable()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER list_link_event_no_update ON list_link_event")
    op.execute("DROP FUNCTION list_link_event_immutable()")
    op.drop_table("list_link_event")
