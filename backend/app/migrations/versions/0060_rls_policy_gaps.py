"""Tenant-isolation policies for four tables that were missing them.

Found by enumerating `pg_class.relrowsecurity` / `pg_policy` against every
table carrying an `org_id`, rather than by reading migrations — the
definitive answer, and it disagreed with the reading pass that preceded it
in two directions at once.

Three tables carry `org_id` and had no policy at all:

  contact             — the RACI/CRM contact record.
  system_description  — the SSP Section 1 narrative, one row per org.
  audit_log           — the append-only compliance record.

The fourth, `raci_assignment`, was reported as "carries org_id but has no
policy". **It has no `org_id` column**; it is scoped transitively through
`control_state_id`. That correction is why its policy below is an EXISTS
over `control_state` rather than a column comparison — the isolation is
real either way, but the mechanism is not the one the other tables use.

**Why this is latent rather than a live hole today:** the application still
connects as `wingrc`, which is both the table owner and (in the compose dev
setup) a superuser, and PostgreSQL bypasses RLS unconditionally for both
unless `FORCE ROW LEVEL SECURITY` is set — which it is not, on any table in
this database (also verified by query, `relforcerowsecurity = false`
everywhere). So none of these policies change behaviour on the day they
land. They change what happens at the `wingrc_app` cutover, which is
exactly when a missing policy would stop being a second layer and start
being the only one.

`audit_log` is deliberately different, and getting it wrong would have
broken production at that cutover:

  `audit_log.org_id` is NULLABLE, on purpose — deployment-wide actions log
  with `org_id=None` (product baseline administration, credential-key
  rotation, practitioner notes). 45 of 317 rows on the live dev database
  are such rows, so this is real data, not a theoretical shape.

  A policy of the usual form would reject those inserts. A `FOR ALL`
  policy with no explicit `WITH CHECK` uses its `USING` expression as the
  check, and `NULL = <uuid>` is NULL, not true — so every deployment-wide
  audit write would start failing the moment the app stopped bypassing
  RLS. The `org_id IS NULL OR ...` clause below is what keeps them
  writable.

  It does not widen what anyone can read: `routers/audit_log.py` already
  filters `AuditLog.org_id == org_id` in its own query and is gated
  `msp_admin`-only, so NULL-org rows are invisible in the UI today and
  remain invisible after this. The policy is defence in depth behind that
  filter, not a replacement for it.

Not included, deliberately — six tables that are org-scoped only
transitively and have no `org_id` of their own:

  contact_documentation_role, control_state_contributor,
  control_state_history, evidence_state_link, evidence_task_state_link,
  review_cycle_reminder_log

Each is reachable only through a parent that is itself RLS-gated (and
`contact_documentation_role`'s parent becomes gated by this migration).
Each would need its own EXISTS policy and its own thought about the join
and the WITH CHECK, which is a coherent follow-up pass rather than
something to append here unexamined. `raci_assignment` is in this
migration and not that list only because it was specifically named; the
others are recorded in docs/roadmap.md so the set is not lost.

Revision ID: 0060_rls_policy_gaps
Revises: 0059_document_library
Create Date: 2026-09-28
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0060_rls_policy_gaps"
down_revision: str | None = "0059_document_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same expression every other tenant-isolation policy in this database
# uses (migrations 0001/0002/0045/0056/0059): NULLIF guards the
# unset-GUC case so an empty string does not blow up the ::uuid cast.
_ORG_MATCH = "org_id = NULLIF(current_setting('app.current_org', true), '')::uuid"


def _org_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY {table}_tenant_isolation ON {table} USING ({_ORG_MATCH})")


def upgrade() -> None:
    _org_policy("contact")
    _org_policy("system_description")

    # See this module's docstring: the IS NULL arm keeps deployment-wide
    # audit rows writable. Without it, every org_id=None log_event() call
    # fails once the app stops bypassing RLS.
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY audit_log_tenant_isolation ON audit_log "
        f"USING (org_id IS NULL OR {_ORG_MATCH})"
    )

    # raci_assignment has no org_id; isolation flows through control_state,
    # which is itself RLS-gated, so this EXISTS composes with that policy
    # rather than duplicating it. Indexed on control_state_id already
    # (ix_raci_assignment_control_state_id), so the subquery is a lookup.
    op.execute("ALTER TABLE raci_assignment ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY raci_assignment_tenant_isolation ON raci_assignment "
        "USING (EXISTS (SELECT 1 FROM control_state cs "
        "WHERE cs.id = raci_assignment.control_state_id))"
    )


def downgrade() -> None:
    for table in ("raci_assignment", "audit_log", "system_description", "contact"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
