"""Tenant-isolation policies for the six transitively-scoped child tables.

Completes the set migration 0060 started. These six carry no `org_id` of
their own -- they are reachable only through a parent that is already
RLS-gated -- so each policy is an `EXISTS` over that parent rather than a
column comparison, the same shape 0060 used for `raci_assignment`.

**Why an EXISTS over the parent composes correctly.** For a non-owner
role, PostgreSQL applies a table's own policies to references of that table
*inside another policy's expression*. So `EXISTS (SELECT 1 FROM
control_state cs WHERE cs.id = ...)` is true only when that `control_state`
row is visible under `control_state_tenant_isolation`, which is the org
check. The policies below therefore inherit their parent's scoping instead
of restating it -- one definition of "which org", not seven. 0060's
`raci_assignment` policy already proved this composition works under
`SET ROLE wingrc_app`.

**`USING` and `WITH CHECK` are identical for all six, deliberately.** A
`FOR ALL` policy with no explicit `WITH CHECK` silently reuses `USING` as
the check, which is what made `audit_log`'s nullable `org_id` a latent
production break in 0060. Both are spelled out here even though they match,
so the next reader sees a decision rather than an omission. They match
because the rule is genuinely symmetric: a row may be read iff its parent
is visible, and may be written iff its parent is visible. There is no case
where you may create a link to a control state you cannot see.

**No nullable join columns**, checked rather than assumed: every FK column
used below is `NOT NULL` in the schema (`pg_attribute.attnotnull`), so the
`NULL = <uuid>` -> NULL trap that shaped `audit_log`'s policy cannot arise
here. That check is the reason these policies are plain rather than
carrying an `IS NULL` arm.

**Join column choice where a table has two gated parents.**
`evidence_state_link` and `evidence_task_state_link` each reference both a
`control_state` and an evidence-side parent; `review_cycle_reminder_log`
references both a cycle and a reviewer. Both sides are org-gated and, by
construction, in the same org -- so either would isolate correctly. Each
policy below joins through the parent whose FK column has its own dedicated
index (verified: `ix_evidence_state_link_control_state_id`,
`ix_evidence_task_state_link_control_state_id`,
`ix_review_cycle_reminder_log_cycle_id`), and using `control_state`
consistently for the two link tables keeps one join shape rather than two.

**The consequence to hold in mind when reading this diff:** adding a policy
to a table changes every post-commit read of that table, because
`app.current_org` dies with the committing transaction. That is what
`app/rls.py`'s `after_begin` hook now handles, and it is why this migration
could not have landed before it -- `routers/contacts.py:add_role` does a
post-commit `refresh()` on `contact_documentation_role`, one of the six.

Revision ID: 0061_rls_transitive_policies
Revises: 0060_rls_policy_gaps
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0061_rls_transitive_policies"
down_revision: str | None = "0060_rls_policy_gaps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, fk column on that table, parent table) -- the parent supplies the
# org scoping via its own already-installed policy.
_TRANSITIVE: tuple[tuple[str, str, str], ...] = (
    ("contact_documentation_role", "contact_id", "contact"),
    ("control_state_contributor", "control_state_id", "control_state"),
    ("control_state_history", "control_state_id", "control_state"),
    ("evidence_state_link", "control_state_id", "control_state"),
    ("evidence_task_state_link", "control_state_id", "control_state"),
    ("review_cycle_reminder_log", "cycle_id", "review_cycle"),
)


def upgrade() -> None:
    for table, fk_column, parent in _TRANSITIVE:
        predicate = (
            f"EXISTS (SELECT 1 FROM {parent} p WHERE p.id = {table}.{fk_column})"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )


def downgrade() -> None:
    for table, _fk_column, _parent in reversed(_TRANSITIVE):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
