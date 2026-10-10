"""List library core -- roadmap L.1 (docs/PLAN-list-library.md).

A list is a definition plus its own versioned rows. Versioned from this
first migration, never retrofitted (roadmap item P): `list_version` is
append-only, and a trigger rejects any UPDATE of it, so "the prior version
is byte-identical afterwards" is a property of the database, not of a code
path remembering to behave. DELETE stays possible -- a version goes away
only with its definition (ON DELETE CASCADE), which tenant teardown needs.

Every table carries its own org_id and the same tenant-isolation policy
as every other org-scoped table (migrations 0059-0061).

Purely additive: three new tables, no existing row touched.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0064_list_library"
down_revision: str | None = "0063_document_type_baseline"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )


def _id() -> sa.Column:
    return sa.Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def _org() -> sa.Column:
    return sa.Column(
        "org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False, index=True
    )


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.create_table(
        "list_definition",
        _id(),
        _org(),
        sa.Column("list_key", sa.String(120), nullable=False),
        sa.Column("is_template", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source_ref", sa.String(400), nullable=True),
        sa.Column("projection_view_id", sa.String(60), nullable=True),
        _created(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("org_id", "list_key", name="uq_list_definition_key"),
    )
    _rls("list_definition")

    op.create_table(
        "list_version",
        _id(),
        sa.Column(
            "list_id",
            UUID(as_uuid=True),
            sa.ForeignKey("list_definition.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        _org(),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("responsible", sa.Text(), nullable=True),
        sa.Column("review_cadence", sa.Text(), nullable=True),
        sa.Column("columns", JSONB(), nullable=False),
        sa.Column("rows", JSONB(), nullable=False),
        sa.Column("provenance", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _created(),
        sa.UniqueConstraint("list_id", "version_number", name="uq_list_version_identity"),
    )
    _rls("list_version")
    op.execute(
        "CREATE FUNCTION list_version_immutable() RETURNS trigger LANGUAGE plpgsql AS "
        "$fn$ BEGIN RAISE EXCEPTION "
        "'list_version is append-only: edit a list by creating a new version'; END $fn$"
    )
    op.execute(
        "CREATE TRIGGER list_version_no_update BEFORE UPDATE ON list_version "
        "FOR EACH ROW EXECUTE FUNCTION list_version_immutable()"
    )

    # Added once list_version exists -- the same ordering document/
    # document_version use (migration 0059).
    op.add_column(
        "list_definition",
        sa.Column(
            "current_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("list_version.id", name="fk_list_definition_current_version"),
            nullable=True,
        ),
    )
    op.add_column(
        "list_definition",
        sa.Column(
            "template_list_version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("list_version.id", name="fk_list_definition_template_version"),
            nullable=True,
        ),
    )

    op.create_table(
        "list_control_tag",
        _id(),
        sa.Column(
            "list_id",
            UUID(as_uuid=True),
            sa.ForeignKey("list_definition.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        _org(),
        sa.Column("control_key", sa.String(40), nullable=False, index=True),
        sa.UniqueConstraint("list_id", "control_key", name="uq_list_control_tag"),
    )
    _rls("list_control_tag")


def downgrade() -> None:
    op.drop_table("list_control_tag")
    op.drop_constraint("fk_list_definition_current_version", "list_definition")
    op.drop_constraint("fk_list_definition_template_version", "list_definition")
    op.execute("DROP TRIGGER list_version_no_update ON list_version")
    op.execute("DROP FUNCTION list_version_immutable()")
    op.drop_table("list_version")
    op.drop_table("list_definition")
