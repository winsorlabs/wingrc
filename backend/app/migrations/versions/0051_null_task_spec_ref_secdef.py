"""auth.null_evidence_task_baseline_spec_refs(): SECURITY DEFINER cross-org
FK-null for seeds/baselines.py's evidence-spec diffing.

Found live, not by inspection: the structured-baseline-review-table slice's
own new regression test (test_reimport_with_changed_evidence_replaces_spec_
and_nulls_task_link) triggered a real `ForeignKeyViolation` deleting a
stale `baseline_evidence_spec` row still referenced by an `evidence_task`.
Root cause -- pre-existing, not introduced by that slice's evidence-diffing
change: `/admin/products/import/apply` runs under the RLS-enforced
`wingrc_app` role (see conftest.py's `_app_session` for the same role
switch this endpoint hits in production), with no `app.current_org` set --
admin_products.py's own module docstring is explicit that this screen is
deployment-wide, never org-scoped. `evidence_task` IS org-scoped and RLS-
protected, so a bare `UPDATE evidence_task SET baseline_spec_id = NULL
WHERE baseline_spec_id IN (...)` issued under that role silently matches
ZERO rows (RLS makes every other org's -- i.e. effectively all -- rows
invisible to an update with no current_org), while Postgres's FK
constraint check on the subsequent DELETE still sees the real, unfiltered
row and correctly rejects it. This was always latent in the original
delete-and-replace evidence code too (same bare UPDATE shape); it just
never surfaced because no existing test combined "a real EvidenceTask
exists" with "re-import that product through the actual admin endpoint"
in one case until now.

Matches auth.expire_stale_invites() (0042) / auth.mark_sprs_reminder_sent()
(0044)'s established precedent exactly -- the mechanism this codebase uses
whenever a deployment-wide operation must touch rows across every org at
once, which no single app.current_org value can express. Never a blanket
RLS bypass; one purpose-built, narrowly-scoped function instead.

Revision ID: 0051_null_task_spec_ref_secdef
Revises: 0050_liongard_env_unique
Create Date: 2026-09-16
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0051_null_task_spec_ref_secdef"
down_revision: str | None = "0050_liongard_env_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION auth.null_evidence_task_baseline_spec_refs(p_spec_ids UUID[])
        RETURNS INTEGER
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql AS $$
            WITH cleared AS (
                UPDATE public.evidence_task
                SET baseline_spec_id = NULL
                WHERE baseline_spec_id = ANY(p_spec_ids)
                RETURNING 1
            )
            SELECT count(*)::INTEGER FROM cleared;
        $$;
    """)
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "auth.null_evidence_task_baseline_spec_refs(UUID[]) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION "
        f"auth.null_evidence_task_baseline_spec_refs(UUID[]) TO {_APP_ROLE}"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS auth.null_evidence_task_baseline_spec_refs(UUID[])"
    )
