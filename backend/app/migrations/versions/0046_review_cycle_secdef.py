"""SECURITY DEFINER functions for the review-cycle scheduler jobs
(scheduler.py: review_cycle_open, review_cycle_sweep) -- matching
auth.msp_role_users()/auth.expire_stale_invites()/auth.orgs_due_for_sprs_
reminder()'s exact existing precedent (0025, 0042, 0044): cross-org reads
no single app.current_org value can express, never a blanket RLS bypass.

auth.orgs_due_for_review_cycle_open() -- one row per org that either has
never had a cycle, or whose most recent cycle opened more than its own
review_cadence_months ago, AND has no cycle currently 'open' (never opens
a second cycle on top of an unfinished one).

auth.org_reviewer_candidates(p_org_id) -- MSP-side (msp_admin/
msp_engineer/consultant_admin) and client-side (customer_poc) active
users with org_membership in that org. Genuinely needs SECURITY DEFINER,
not just SET LOCAL app.current_org to p_org_id: "user"'s own RLS policy
(0015) is keyed to user.home_org_id, not org_membership.org_id -- an
msp_admin homed at the MSP's own org but granted membership into a
client org would be invisible under a plain per-org-scoped query once
the real runtime-role cutover lands, the same cross-org visibility gap
0025's own docstring already worked out for auth.msp_role_users().
consultant_admin IS included here, unlike auth.msp_staff_emails()'s
deployment-wide MSP-staff set (sprs_annual_reminder, migration 0044) --
that exclusion was about a *deployment-wide* notification a per-
engagement consultant has no general stake in; a consultant_admin who
actually holds membership in *this specific* org is exactly the person
this review is for.

auth.review_cycles_due_for_sweep() -- every 'open' review_cycle's
(cycle_id, org_id), for the reminder/force-close job to iterate. The
per-reviewer reminder-timing and close logic itself runs after SET LOCAL
app.current_org = that cycle's own org_id, against review_cycle* tables
whose RLS policies are keyed directly to org_id (not home_org_id) -- so
that part doesn't need a SECURITY DEFINER function, only this discovery
step does.

Revision ID: 0046_review_cycle_secdef
Revises: 0045_review_cycle
Create Date: 2026-09-13
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0046_review_cycle_secdef"
down_revision: str | None = "0045_review_cycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION auth.orgs_due_for_review_cycle_open()
        RETURNS TABLE (org_id UUID, cadence_months SMALLINT)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            WITH last_cycle AS (
                SELECT DISTINCT ON (rc.org_id)
                    rc.org_id, rc.opened_at, rc.status
                FROM public.review_cycle rc
                ORDER BY rc.org_id, rc.opened_at DESC
            ),
            currently_open AS (
                SELECT DISTINCT rc.org_id
                FROM public.review_cycle rc
                WHERE rc.status = 'open'
            )
            SELECT o.id AS org_id, o.review_cadence_months AS cadence_months
            FROM public.organization o
            LEFT JOIN last_cycle lc ON lc.org_id = o.id
            WHERE o.id NOT IN (SELECT org_id FROM currently_open)
              AND (
                  lc.opened_at IS NULL
                  OR lc.opened_at <= now() - (o.review_cadence_months || ' months')::interval
              );
        $$;
    """)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.orgs_due_for_review_cycle_open() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth.orgs_due_for_review_cycle_open() TO {_APP_ROLE}")

    op.execute("""
        CREATE FUNCTION auth.org_reviewer_candidates(p_org_id UUID)
        RETURNS TABLE (user_id UUID, email VARCHAR, display_name VARCHAR, reviewer_side VARCHAR)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT u.id, u.email, u.display_name,
                   CASE WHEN om.role = 'customer_poc' THEN 'client' ELSE 'msp' END
            FROM public.org_membership om
            JOIN public."user" u ON u.id = om.user_id
            WHERE om.org_id = p_org_id
              AND om.role IN ('msp_admin', 'msp_engineer', 'consultant_admin', 'customer_poc')
              AND u.is_active = TRUE;
        $$;
    """)
    op.execute(
        "REVOKE EXECUTE ON FUNCTION auth.org_reviewer_candidates(UUID) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION auth.org_reviewer_candidates(UUID) TO {_APP_ROLE}"
    )

    op.execute("""
        CREATE FUNCTION auth.review_cycles_due_for_sweep()
        RETURNS TABLE (cycle_id UUID, org_id UUID)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT id, org_id FROM public.review_cycle WHERE status = 'open';
        $$;
    """)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.review_cycles_due_for_sweep() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth.review_cycles_due_for_sweep() TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.review_cycles_due_for_sweep()")
    op.execute("DROP FUNCTION IF EXISTS auth.org_reviewer_candidates(UUID)")
    op.execute("DROP FUNCTION IF EXISTS auth.orgs_due_for_review_cycle_open()")
