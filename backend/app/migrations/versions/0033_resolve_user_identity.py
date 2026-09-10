"""SECURITY DEFINER function so practitioner-notes editor resolution can
cross org boundaries, same root cause 0025 already fixed once.

Found while verifying migration 0032 on the bench stack: routers/
objectives.py's _resolve_editor() does a plain `session.get(User, user_id)`
to resolve who last edited a practitioner note. practitioner_notes_edited_by
is explicitly NOT an org-scoped fact (see that function's own docstring --
this deployment-wide catalog table has no org_id at all), but the
underlying `user` table row-level security policy (0015) is:
    home_org_id = current_setting('app.current_org', true)::uuid
"app.current_org" is set once per request to the *viewer's* org, not the
edited-by user's -- so an msp_admin viewing from one org who resolves a
note edited by an msp_admin whose home org differs gets silently
RLS-filtered to zero rows, misreporting a very much active editor as
"deleted". Worse, exactly like 0025's incident: on a connection where
app.current_org was set earlier and has since gone through a ROLLBACK
without being touched again, Postgres reverts the custom GUC to its
boot_val '' rather than "unset" -- current_setting(..., true) then
returns '' and the ::uuid cast in the policy itself raises
InvalidTextRepresentation, a hard 500 instead of a silent misreport.
Caught here by the bench-stack integration run, not by code review.

Same fix as 0025: a narrow SECURITY DEFINER function, EXECUTE restricted
to wingrc_app (not PUBLIC -- it discloses one user's display_name/email
regardless of caller's org, so it stays as narrowly grantable as
auth.msp_role_users() already is), read-only, single row by id. This is
NOT M.7's deployment-wide user *directory* (docs/PLAN-gui-restructure.md)
-- no listing, no search, no grant/revoke -- just the one
lookup-by-id-across-orgs _resolve_editor already needed and was missing.

Revision ID: 0033_resolve_user_identity
Revises: 0032_practitioner_notes_edit
Create Date: 2026-09-10
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0033_resolve_user_identity"
down_revision: str | None = "0032_practitioner_notes_edit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION auth.resolve_user_identity(p_user_id UUID)
        RETURNS TABLE (id UUID, display_name VARCHAR, email VARCHAR, deleted_at TIMESTAMPTZ)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            -- Deliberately unscoped by app.current_org -- the whole point
            -- is resolving an identity that may belong to a different org
            -- than the caller's current context. Read-only, but still
            -- bypasses RLS and discloses display_name/email -- EXECUTE is
            -- restricted below, not left at Postgres's PUBLIC default.
            SELECT id, display_name, email, deleted_at
            FROM public."user"
            WHERE id = p_user_id;
        $$;
    """)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.resolve_user_identity(UUID) FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION auth.resolve_user_identity(UUID) TO {_APP_ROLE}"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.resolve_user_identity(UUID)")
