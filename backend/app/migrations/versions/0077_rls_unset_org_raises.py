"""Reading an RLS table with no org set raises, and says so.

Until this, a policy read `app.current_org` itself, in one of two forms:

- `NULLIF(current_setting('app.current_org', true), '')::uuid` (38
  tables). An unset org matched zero rows, silently.
- `current_setting('app.current_org', true)::uuid` (the five auth
  tables). An unset org matched zero rows if the GUC had never been set
  on that pooled connection (NULL), and raised `invalid input syntax for
  type uuid: ""` once it had been (the empty string a transaction-local
  setting reverts to).

So a read made before `rls.set_current_org` answered "not found" or a
500, depending on the connection's history, and the error named a cast
on whatever line ran first. `/auth/set-password` read the user before
setting the org and failed this way on every invite from the 2026-09-29
cutover to 2026-11-20 (docs/roadmap.md, "No invite could be redeemed on
live").

Now every policy that reads the org reads it through
`public.rls_current_org()`, which raises SQLSTATE `WR001` with a message
naming the cause when it is unset (NULL or empty). It is called as
`(SELECT public.rls_current_org())`, an InitPlan, so it runs once per
query, and only when a row reaches the qual: a read of an empty table, or
an `audit_log` row whose `org_id` is null, does not raise. The seven
transitive policies read a parent table and are gated by its policy, so
they raise through it.

The table owner is exempt from RLS, so the migration role, the bench and
a deployment still running the app as the owner never evaluate these
(docs/roadmap.md, "A fresh install runs the app as a superuser").

`tests/test_rls_unset_org.py` holds the policies to this: every policy on
an RLS table depends on the function, except the transitive ones written
out there, and a read with the org unset raises WR001.

Revision ID: 0077_rls_unset_org_raises
Revises: 0076_user_deactivated_at
Create Date: 2026-11-20
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0077_rls_unset_org_raises"
down_revision: str | None = "0076_user_deactivated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORG = "(SELECT public.rls_current_org())"
OLD_NULLIF = "NULLIF(current_setting('app.current_org', true), '')::uuid"
OLD_CAST = "current_setting('app.current_org', true)::uuid"

# Written out from live's pg_policies on 2026-11-20: (table, policy,
# has WITH CHECK). Each reads `org_id = <org>` today through NULLIF.
NULLIF_ORG_ID = [
    ("assessment", "assessment_tenant_isolation", False),
    ("asset_approval", "asset_approval_tenant_isolation", False),
    ("asset_approval_checklist_item", "asset_approval_checklist_item_tenant_isolation", False),
    ("contact", "contact_tenant_isolation", False),
    ("control_state", "control_state_tenant_isolation", False),
    ("document", "document_tenant_isolation", False),
    ("document_approval", "document_approval_tenant_isolation", True),
    ("document_objective_tag", "document_objective_tag_tenant_isolation", False),
    ("document_review_notification", "document_review_notification_tenant_isolation", True),
    ("document_version", "document_version_tenant_isolation", False),
    ("evidence", "evidence_tenant_isolation", False),
    ("evidence_task", "evidence_task_tenant_isolation", False),
    ("finding", "finding_tenant_isolation", False),
    ("implementation_statement", "implementation_statement_tenant_isolation", False),
    ("liongard_sync_notification", "liongard_sync_notification_tenant_isolation", False),
    ("liongard_sync_result", "liongard_sync_result_tenant_isolation", False),
    ("liongard_sync_result_change", "liongard_sync_result_change_tenant_isolation", False),
    ("list_control_tag", "list_control_tag_tenant_isolation", False),
    ("list_definition", "list_definition_tenant_isolation", False),
    ("list_link_event", "list_link_event_tenant_isolation", False),
    ("list_tag_event", "list_tag_event_tenant_isolation", False),
    ("list_version", "list_version_tenant_isolation", False),
    ("org_liongard_environment", "org_liongard_environment_tenant_isolation", False),
    ("org_membership", "org_membership_tenant_isolation", False),
    ("org_product", "org_product_tenant_isolation", False),
    ("poa_m_item", "poa_m_item_tenant_isolation", False),
    ("review_cycle", "review_cycle_tenant_isolation", False),
    ("review_cycle_flag", "review_cycle_flag_tenant_isolation", False),
    ("review_cycle_item", "review_cycle_item_tenant_isolation", False),
    ("review_cycle_reviewer", "review_cycle_reviewer_tenant_isolation", False),
    ("scope_entity", "scope_entity_tenant_isolation", False),
    ("sprs_reminder_log", "sprs_reminder_log_tenant_isolation", False),
    ("sprs_snapshot", "sprs_snapshot_tenant_isolation", False),
    ("sprs_submission", "sprs_submission_tenant_isolation", False),
    ("ssp_import_row", "ssp_import_row_tenant_isolation", False),
    ("ssp_import_session", "ssp_import_session_tenant_isolation", False),
    ("system_description", "system_description_tenant_isolation", False),
]

# (table, policy, USING with the org as {org}). The auth tables, which
# cast directly, and audit_log, whose null-org rows every request sees.
OTHER = [
    ("api_token", "token_org", "org_id = {org}", OLD_CAST),
    ("user_session", "session_org", "org_id = {org}", OLD_CAST),
    ('"user"', "user_org", "home_org_id = {org}", OLD_CAST),
    ("mfa_backup_code", "mfa_code_org",
     'user_id IN (SELECT "user".id FROM "user" WHERE "user".home_org_id = {org})', OLD_CAST),
    ("password_history", "password_history_org",
     'user_id IN (SELECT "user".id FROM "user" WHERE "user".home_org_id = {org})', OLD_CAST),
    ("audit_log", "audit_log_tenant_isolation", "org_id IS NULL OR org_id = {org}", OLD_NULLIF),
]

FUNCTION = """
CREATE FUNCTION public.rls_current_org() RETURNS uuid
LANGUAGE plpgsql STABLE
SET search_path = pg_catalog
AS $$
DECLARE
    v text := current_setting('app.current_org', true);
BEGIN
    IF v IS NULL OR v = '' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'WR001',
            MESSAGE = 'app.current_org is not set: a row-level-security table '
                      'was read before rls.set_current_org',
            HINT = 'Set the org (rls.set_current_org) before the first read of '
                   'an org-scoped table, or read it through an auth. function.';
    END IF;
    RETURN v::uuid;
END
$$;
"""


def _alter(table: str, policy: str, using: str, check: bool) -> str:
    sql = f"ALTER POLICY {policy} ON {table} USING ({using})"
    if check:
        sql += f" WITH CHECK ({using})"
    return sql


def upgrade() -> None:
    op.execute(FUNCTION)
    for table, policy, check in NULLIF_ORG_ID:
        op.execute(_alter(table, policy, f"org_id = {ORG}", check))
    for table, policy, using, _old in OTHER:
        op.execute(_alter(table, policy, using.format(org=ORG), False))


def downgrade() -> None:
    for table, policy, check in NULLIF_ORG_ID:
        op.execute(_alter(table, policy, f"org_id = {OLD_NULLIF}", check))
    for table, policy, using, old in OTHER:
        op.execute(_alter(table, policy, using.format(org=old), False))
    op.execute("DROP FUNCTION public.rls_current_org()")
