"""Add a uniqueness constraint on org_liongard_environment.liongard_environment_id
-- one Liongard Environment maps to at most one WinGRC org.

Decided 2026-09-16: a Liongard Environment already represents one client's
infrastructure. Nothing stopped two orgs from independently mapping to the
same one and each pulling the same devices/identities into two separate
scope graphs -- for a compliance tool, that's one client's environment
silently leaking into another org's CUI-boundary denominator, not just an
asset-management inconvenience. No legitimate case for intentional sharing
was found, so this is enforced rather than left permissive.
routers/scope.py's PUT endpoint checks this before the DB does, for a real
error message naming which org already holds the environment; this
constraint is meant as the backstop against a direct write or a race.

**Written defensively, not as a plain ADD CONSTRAINT, because a real
collision was found live on wl-util-1 before this migration was written**
(checked, not assumed -- see docs/roadmap.md's Done entry for this slice):
two orgs ("Acme MSP", "Test Customer A") both already mapped to the same
Liongard Environment (5912, WinsorLabs). A plain unique index would fail
this migration outright -- and since backend's own startup command is
`alembic upgrade head && exec uvicorn ...`, a failed migration means the
backend never starts serving traffic AT ALL, not just this one feature,
over a pre-existing data collision that isn't this migration's call to
resolve unilaterally (deciding which org keeps a shared environment is a
tenant-data decision -- the same reasoning migration 0049 already applied
to colliding contact emails, choosing to skip rather than merge).

So: check first. If liongard_environment_id has no duplicates, add the
constraint. If it does, DO NOT add it and DO NOT fail the migration --
print the exact colliding rows (visible in `docker logs <backend>` per
docs/deployment.md 7c's own "confirm which migrations ran from the logs"
step) so a human sees it immediately, and let the deploy proceed without
the DB-level guarantee. The application-level check in routers/scope.py's
PUT endpoint still protects the normal UI-driven flow either way; only a
direct-DB write or a race would slip past an unset constraint. Re-run
`ALTER TABLE org_liongard_environment ADD CONSTRAINT
uq_org_liongard_environment_environment_id UNIQUE (liongard_environment_id)`
by hand (or a future migration) once every collision is resolved -- this
migration does not retry itself.

Also adds `auth.liongard_environment_holder(p_environment_id INT)`, a
narrow SECURITY DEFINER function (same ADR 0009 "system-level cross-org
read" pattern as migration 0037's `product_deployment_footprint`) so
routers/scope.py's PUT endpoint can check "does any OTHER org already
hold this environment" for a real 409 message -- org_liongard_environment
carries RLS scoped to the requesting org's own `app.current_org`, which
by design cannot see another org's row, so this check needs the same
bypass mechanism every other cross-org read in this codebase already uses.
EXECUTE restricted to wingrc_app, not PUBLIC, matching every other
function this pattern has added.

Revision ID: 0050_liongard_env_unique
Revises: 0049_contact_provenance
Create Date: 2026-09-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_liongard_env_unique"
down_revision: str | None = "0049_contact_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT_NAME = "uq_org_liongard_environment_environment_id"
_APP_ROLE = "wingrc_app"


def upgrade() -> None:
    # Created unconditionally, independent of whether the constraint below
    # ends up applied -- this is what makes the application-level check in
    # routers/scope.py's PUT endpoint possible at all (the constraint is
    # only the backstop; this function is the primary, user-facing check).
    # Correct to create even while a collision currently exists: it simply
    # returns more than one row for that environment until it's resolved,
    # which is exactly the information the router needs to report either way.
    op.execute("""
        CREATE FUNCTION auth.liongard_environment_holder(p_environment_id INTEGER)
        RETURNS TABLE (org_id UUID, org_name VARCHAR)
        SECURITY DEFINER
        SET search_path = public, pg_catalog
        LANGUAGE sql STABLE AS $$
            -- Deliberately unscoped by app.current_org: the requesting
            -- org's own RLS context can never see another org's mapping
            -- row, which is exactly the row this check needs to find.
            SELECT ole.org_id, o.name
            FROM public.org_liongard_environment ole
            JOIN public.organization o ON o.id = ole.org_id
            WHERE ole.liongard_environment_id = p_environment_id;
        $$;
    """)
    op.execute(
        "REVOKE EXECUTE ON FUNCTION auth.liongard_environment_holder(INTEGER) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION auth.liongard_environment_holder(INTEGER) TO {_APP_ROLE}"
    )

    bind = op.get_bind()
    dupes = bind.execute(
        sa.text(
            """
            SELECT liongard_environment_id, array_agg(org_id::text ORDER BY org_id)
            FROM org_liongard_environment
            GROUP BY liongard_environment_id
            HAVING count(*) > 1
            """
        )
    ).all()
    if dupes:
        for env_id, org_ids in dupes:
            print(  # noqa: T201 -- deliberately visible in `docker logs <backend>`, not a debug leftover
                f"0050_liongard_env_unique: SKIPPING the unique constraint -- "
                f"environment {env_id} is already mapped to {len(org_ids)} orgs: {org_ids}. "
                "Resolve (unmap all but one via DELETE /orgs/{org_id}/integrations/liongard/"
                "environment) then add the constraint by hand -- see this migration's own "
                "docstring."
            )
        return
    op.create_unique_constraint(
        _CONSTRAINT_NAME, "org_liongard_environment", ["liongard_environment_id"]
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth.liongard_environment_holder(INTEGER)")
    # No-op if upgrade() skipped creating the constraint (duplicates
    # present at the time) -- drop_constraint would otherwise fail on a
    # constraint that was never actually added.
    bind = op.get_bind()
    exists = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_constraint WHERE conname = :name"
        ),
        {"name": _CONSTRAINT_NAME},
    ).first()
    if exists:
        op.drop_constraint(_CONSTRAINT_NAME, "org_liongard_environment", type_="unique")
