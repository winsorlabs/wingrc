"""A durable SSP import session, decided row by row over days.

`ssp_import_session` is one customer SSP read into one assessment; its
`ssp_import_row`s are that file's rows, each matched against the catalog
and each waiting for a person's decision (docs/roadmap.md, "Importing
implementation statements from a customer's SSP", the review session).

**The file is not kept (Jarrod, 2026-11-18).** It is parsed in memory and
discarded. The session pins it by SHA-256, name and size, so a revised SSP
is a new session and anyone holding the customer's copy can show it is the
same file. A row keeps its statement text, the Control cell, the source's
status claim, and only whether Notes and RACI were present: never RACI's
content, never Notes' text.

**One open session per assessment**, held by a partial unique index, so a
second open is refused by the database and not only by a check that runs
before it.

No backfill: nothing has been imported before this migration.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0074_ssp_import_session"
down_revision: str | None = "0073_list_reference_columns"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )


def upgrade() -> None:
    op.create_table(
        "ssp_import_session",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"),
                  nullable=False, index=True),
        sa.Column("assessment_id", UUID(as_uuid=True), sa.ForeignKey("assessment.id"),
                  nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("importer", sa.String(40), nullable=False),
        sa.Column("started_by", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("closed_by", sa.Text(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('open', 'completed', 'discarded')",
                           name="ck_ssp_import_session_status"),
        sa.CheckConstraint("(status = 'open') = (closed_at IS NULL)",
                           name="ck_ssp_import_session_closed"),
    )
    op.create_index(
        "uq_ssp_import_session_one_open", "ssp_import_session", ["assessment_id"],
        unique=True, postgresql_where=sa.text("status = 'open'"),
    )
    _rls("ssp_import_session")

    op.create_table(
        "ssp_import_row",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("session_id", UUID(as_uuid=True),
                  sa.ForeignKey("ssp_import_session.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"),
                  nullable=False, index=True),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("family", sa.String(4), nullable=True),
        sa.Column("control_text", sa.Text(), nullable=False),
        sa.Column("source_status", sa.Text(), nullable=True),
        sa.Column("notes_present", sa.Boolean(), nullable=False),
        sa.Column("raci_present", sa.Boolean(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=True),
        sa.Column("match_kind", sa.String(20), nullable=False),
        sa.Column("matched_objective_id", UUID(as_uuid=True),
                  sa.ForeignKey("assessment_objective.id"), nullable=True),
        sa.Column("candidates", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("decision", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("resolution", sa.String(20), nullable=True),
        sa.Column("decided_objective_id", UUID(as_uuid=True),
                  sa.ForeignKey("assessment_objective.id"), nullable=True),
        sa.Column("statement_id", UUID(as_uuid=True),
                  sa.ForeignKey("implementation_statement.id"), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("session_id", "number", name="uq_ssp_import_row_number"),
        sa.CheckConstraint(
            "match_kind IN ('exact', 'ambiguous', 'family_mismatch', 'no_family', 'unmatched')",
            name="ck_ssp_import_row_kind"),
        sa.CheckConstraint("decision IN ('pending', 'accepted', 'edited', 'rejected')",
                           name="ck_ssp_import_row_decision"),
        sa.CheckConstraint("resolution IS NULL OR resolution IN ('replaced', 'kept_existing')",
                           name="ck_ssp_import_row_resolution"),
        sa.CheckConstraint(
            "(decision IN ('accepted', 'edited')) = (statement_id IS NOT NULL)",
            name="ck_ssp_import_row_written"),
        sa.CheckConstraint("(match_kind = 'exact') = (matched_objective_id IS NOT NULL)",
                           name="ck_ssp_import_row_exact"),
    )
    _rls("ssp_import_row")


def downgrade() -> None:
    op.drop_table("ssp_import_row")
    op.drop_index("uq_ssp_import_session_one_open", table_name="ssp_import_session")
    op.drop_table("ssp_import_session")
