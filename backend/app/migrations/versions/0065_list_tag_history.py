"""List tags say who put them there, and keep a history -- roadmap L.2.2.

Tags are not version content: `list_control_tag` sits beside
`list_version`, so changing one writes no version. Two things follow, and
this migration is both:

- `list_control_tag.added_via` -- "template_import" or "api" (a person).
  Until now an import replaced a list's whole tag set with its source
  sheet's row 2, so the next import would silently remove any tag a person
  had added. An import now manages only the tags it added.
- `list_tag_event` -- every tag added or removed, with who and when.
  Append-only: a trigger rejects any UPDATE, as `list_version`'s does
  (migration 0064). DELETE stays possible only with the list itself.

Backfill: every existing tag came from an import (no API could add one
before this), so it gets `added_via = 'template_import'` by default and
one "added" event dated with its list, noted as recorded when tag history
began rather than presented as observed at the time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0065_list_tag_history"
down_revision: str | None = "0064_list_library"
branch_labels = None
depends_on = None

BACKFILL_NOTE = (
    "Recorded when tag history began (migration 0065): the template import had "
    "already tagged this list; the exact time is that of the list's creation."
)


def upgrade() -> None:
    op.add_column(
        "list_control_tag",
        sa.Column(
            "added_via", sa.String(20), nullable=False,
            server_default=sa.text("'template_import'"),
        ),
    )
    op.create_check_constraint(
        "ck_list_control_tag_added_via", "list_control_tag",
        "added_via IN ('template_import', 'api')",
    )
    op.create_table(
        "list_tag_event",
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
        sa.Column("control_key", sa.String(40), nullable=False),
        sa.Column("change", sa.String(10), nullable=False),
        sa.Column("via", sa.String(20), nullable=False),
        sa.Column("saved_by", sa.Text(), nullable=True),
        sa.Column("at_version", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("change IN ('added', 'removed')", name="ck_list_tag_event_change"),
        sa.CheckConstraint("via IN ('template_import', 'api')", name="ck_list_tag_event_via"),
    )
    op.execute("ALTER TABLE list_tag_event ENABLE ROW LEVEL SECURITY")
    op.execute(
        """CREATE POLICY list_tag_event_tenant_isolation ON list_tag_event
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )
    op.execute(
        "CREATE FUNCTION list_tag_event_immutable() RETURNS trigger LANGUAGE plpgsql AS "
        "$fn$ BEGIN RAISE EXCEPTION "
        "'list_tag_event is append-only: a tag change is recorded by a new event'; END $fn$"
    )
    op.execute(
        "CREATE TRIGGER list_tag_event_no_update BEFORE UPDATE ON list_tag_event "
        "FOR EACH ROW EXECUTE FUNCTION list_tag_event_immutable()"
    )
    op.execute(
        sa.text(
            """INSERT INTO list_tag_event
                 (list_id, org_id, control_key, change, via, at_version, note, created_at)
               SELECT t.list_id, t.org_id, t.control_key, 'added', 'template_import',
                      1, :note, d.created_at
               FROM list_control_tag t JOIN list_definition d ON d.id = t.list_id
               ORDER BY d.created_at, d.list_key, t.control_key"""
        ).bindparams(note=BACKFILL_NOTE)
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER list_tag_event_no_update ON list_tag_event")
    op.execute("DROP FUNCTION list_tag_event_immutable()")
    op.drop_table("list_tag_event")
    op.drop_constraint("ck_list_control_tag_added_via", "list_control_tag")
    op.drop_column("list_control_tag", "added_via")
