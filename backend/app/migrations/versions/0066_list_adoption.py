"""A tenant org adopts a template: a copy that knows its source -- roadmap L.7.

Templates stay org-scoped: they are rows in the MSP's own org, confined by
RLS, and nothing here moves them. Deployment scope (like the product
catalog) would be simpler for one MSP and wrong the first time two MSPs
share a deployment. So adoption is a copy into the destination org, made
by someone who holds membership in both, and this migration is what the
copy needs:

- `list_definition.template_list_id` -- the template this list is a copy
  of. With `template_list_version_id` (reserved since 0064) it answers
  "which template, at which version". One copy per template per org.
- `template_adoption` as a tag origin: an adopted copy manages the tags
  its template gave it the way an import manages its own, and never a
  person's.
- Two SECURITY DEFINER reads, each scoped to `app.current_org` and
  returning version numbers, never content:
  * `auth.list_template_heads()` -- for the current org's copies, where
    their templates stand now, so a copy can say "version 3 is
    available" to a tenant who cannot read the MSP's org.
  * `auth.list_template_adoptions()` -- for the current org's templates,
    how many copies sit at which version, without saying whose: the
    MSP's view of what it has distributed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0066_list_adoption"
down_revision: str | None = "0065_list_tag_history"
branch_labels = None
depends_on = None

VIAS = "('template_import', 'api', 'template_adoption')"


def upgrade() -> None:
    op.add_column(
        "list_definition",
        sa.Column(
            "template_list_id", UUID(as_uuid=True),
            sa.ForeignKey("list_definition.id", name="fk_list_definition_template_list"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_list_definition_adopted_once", "list_definition", ["org_id", "template_list_id"],
        unique=True, postgresql_where=sa.text("template_list_id IS NOT NULL"),
    )
    # A copy is never itself a template, and it always knows the version it
    # is based on; a list that is not a copy names no template version.
    op.create_check_constraint(
        "ck_list_definition_copy_shape", "list_definition",
        "(template_list_id IS NULL AND template_list_version_id IS NULL) OR "
        "(template_list_id IS NOT NULL AND template_list_version_id IS NOT NULL "
        "AND NOT is_template)",
    )
    op.drop_constraint("ck_list_control_tag_added_via", "list_control_tag")
    op.create_check_constraint(
        "ck_list_control_tag_added_via", "list_control_tag", f"added_via IN {VIAS}"
    )
    op.drop_constraint("ck_list_tag_event_via", "list_tag_event")
    op.create_check_constraint("ck_list_tag_event_via", "list_tag_event", f"via IN {VIAS}")

    op.execute(
        """
        CREATE FUNCTION auth.list_template_heads()
        RETURNS TABLE (
            list_id UUID, template_list_id UUID, template_org_id UUID,
            based_on_version_number INT, head_version_id UUID,
            head_version_number INT, head_created_at TIMESTAMPTZ
        )
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT c.id, t.id, t.org_id, b.version_number, h.id, h.version_number, h.created_at
            FROM public.list_definition c
            JOIN public.list_definition t ON t.id = c.template_list_id
            JOIN public.list_version b ON b.id = c.template_list_version_id
            JOIN public.list_version h ON h.id = t.current_version_id
            WHERE c.org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
            ORDER BY c.id;
        $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION auth.list_template_adoptions()
        RETURNS TABLE (
            template_list_id UUID, version_number INT, this_org BOOLEAN, copies BIGINT
        )
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT t.id, b.version_number, c.org_id = t.org_id, count(*)
            FROM public.list_definition t
            JOIN public.list_definition c ON c.template_list_id = t.id
            JOIN public.list_version b ON b.id = c.template_list_version_id
            WHERE t.org_id = NULLIF(current_setting('app.current_org', true), '')::uuid
            GROUP BY t.id, b.version_number, c.org_id = t.org_id
            ORDER BY t.id, b.version_number, 3;
        $$;
        """
    )
    for fn in ("list_template_heads()", "list_template_adoptions()"):
        op.execute(f"REVOKE EXECUTE ON FUNCTION auth.{fn} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION auth.{fn} TO wingrc_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION auth.list_template_adoptions()")
    op.execute("DROP FUNCTION auth.list_template_heads()")
    old = "('template_import', 'api')"
    op.drop_constraint("ck_list_tag_event_via", "list_tag_event")
    op.create_check_constraint("ck_list_tag_event_via", "list_tag_event", f"via IN {old}")
    op.drop_constraint("ck_list_control_tag_added_via", "list_control_tag")
    op.create_check_constraint(
        "ck_list_control_tag_added_via", "list_control_tag", f"added_via IN {old}"
    )
    op.drop_constraint("ck_list_definition_copy_shape", "list_definition")
    op.drop_index("uq_list_definition_adopted_once", "list_definition")
    op.drop_column("list_definition", "template_list_id")
