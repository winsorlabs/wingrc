"""SECURITY DEFINER functions for the annual SPRS-reminder scheduler job
(scheduler.py's second real job, after expire_stale_invites): a
cross-org read, a cross-org write, and a recipient lookup -- none of
which any single app.current_org value can express, matching
auth.msp_role_users()/auth.expire_stale_invites()'s exact existing
precedent (0025, 0042). Restricted to wingrc_app throughout, never a
blanket RLS bypass.

auth.orgs_due_for_sprs_reminder() -- one row per org whose CURRENT
(most recent non-voided) sprs_submission's 12-month anniversary has
passed and which has no sprs_reminder_log row for that submission yet.
12 months is CMMC's actual annual SPRS re-submission cadence -- a fixed
business rule, not runtime config, so it's baked into the SQL rather
than threaded through as a parameter. A single reminder, not a ramp
(60/30/7-day warnings): erring toward fewer notifications for a
compliance tool that must not become something people learn to ignore.

auth.mark_sprs_reminder_sent(p_org_id, p_submission_id) -- records that
a reminder was sent for one org's current submission, ON CONFLICT DO
NOTHING against sprs_reminder_log's UNIQUE(submission_id) (belt-and-
suspenders idempotency on top of the job's own due-check).

auth.msp_staff_emails() -- every active msp_admin/msp_engineer's email,
deployment-wide. NOT a reuse/extension of auth.msp_role_users() (0025,
returns id+role for provisioning) -- a new function with a narrower,
purpose-built shape (this is the only thing in this codebase that emails
"every MSP staff member" today) rather than widening an existing
function's contract for a caller it wasn't written for.
consultant_admin is deliberately excluded, matching msp_role_users()'s
existing role set -- an external, per-engagement consultant is not "MSP
staff" for a deployment-wide compliance-deadline notification. Inactive
users are excluded (msp_role_users() doesn't filter on this, but that
function enumerates for access provisioning, where an inactive user
still needs correct role bookkeeping; this one sends mail, where
notifying a deactivated account is pure waste).

Revision ID: 0044_sprs_reminder_secdef
Revises: 0043_sprs_submission
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0044_sprs_reminder_secdef"
down_revision: str | None = "0043_sprs_submission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION auth.orgs_due_for_sprs_reminder()
        RETURNS TABLE (org_id UUID, submission_id UUID)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            WITH current_submission AS (
                SELECT DISTINCT ON (s.org_id)
                    s.org_id, s.id AS submission_id, s.submitted_date
                FROM public.sprs_submission s
                WHERE s.voided_at IS NULL
                ORDER BY s.org_id, s.submitted_date DESC, s.created_at DESC
            )
            SELECT cs.org_id, cs.submission_id
            FROM current_submission cs
            WHERE cs.submitted_date <= (now() - interval '12 months')::date
              AND NOT EXISTS (
                  SELECT 1 FROM public.sprs_reminder_log r
                  WHERE r.submission_id = cs.submission_id
              );
        $$;
    """)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.orgs_due_for_sprs_reminder() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth.orgs_due_for_sprs_reminder() TO {_APP_ROLE}")

    op.execute("""
        CREATE FUNCTION auth.mark_sprs_reminder_sent(p_org_id UUID, p_submission_id UUID)
        RETURNS UUID
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql AS $$
            INSERT INTO public.sprs_reminder_log (id, org_id, submission_id)
            VALUES (gen_random_uuid(), p_org_id, p_submission_id)
            ON CONFLICT (submission_id) DO NOTHING
            RETURNING id;
        $$;
    """)
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "auth.mark_sprs_reminder_sent(UUID, UUID) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION "
        f"auth.mark_sprs_reminder_sent(UUID, UUID) TO {_APP_ROLE}"
    )

    op.execute("""
        CREATE FUNCTION auth.msp_staff_emails()
        RETURNS TABLE (email VARCHAR)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            SELECT email FROM public."user"
            WHERE role IN ('msp_admin', 'msp_engineer') AND is_active = TRUE;
        $$;
    """)
    op.execute("REVOKE EXECUTE ON FUNCTION auth.msp_staff_emails() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION auth.msp_staff_emails() TO {_APP_ROLE}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.msp_staff_emails()")
    op.execute("DROP FUNCTION IF EXISTS auth.mark_sprs_reminder_sent(UUID, UUID)")
    op.execute("DROP FUNCTION IF EXISTS auth.orgs_due_for_sprs_reminder()")
