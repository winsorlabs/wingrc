"""Phase 3: create the wingrc_app runtime role (RLS enforcement backstop).

Today the app connects as `wingrc`, which is both the table owner and (in
the docker-compose dev setup) a PostgreSQL superuser — Postgres
unconditionally bypasses RLS for both, so every RLS policy created in
0001/0002/0015 has never actually been enforced. This migration creates a
second, narrow role — not a superuser, not a table owner, no BYPASSRLS —
that the running app will eventually connect as instead, so RLS becomes a
real defense-in-depth backstop behind the app-layer require_org_access
check rather than a no-op.

This migration is deliberately inert on its own: nothing connects as
wingrc_app until the runtime cutover (a later Phase 3 step flips the app's
connection string). Landing it now, early, lets the test suite start
exercising real RLS enforcement via `SET ROLE wingrc_app` immediately,
without waiting on that cutover — see backend/tests/conftest.py.

Privileges granted:
  - USAGE on schema public and schema auth (needed to resolve objects in
    either schema at all).
  - SELECT/INSERT/UPDATE/DELETE on every current table in schema public.
  - ALTER DEFAULT PRIVILEGES so future tables created by future migrations
    (which run as the owner role) are automatically granted to wingrc_app
    too — the same "structural fix, not a remembered convention" principle
    behind require_org_access itself; nobody has to remember to add a GRANT
    to the next migration that creates a table.

No password is set here — CREATE ROLE ... LOGIN with no password can never
successfully authenticate until an operator sets one via ALTER ROLE, which
is deliberate: a real per-environment secret has no business in a
migration file checked into git. See docs/deployment.md (added in a later
Phase 3 step) for the per-environment provisioning runbook.

Not needed here: EXECUTE on the four SECURITY DEFINER functions in the
auth schema (auth.resolve_session, auth.find_user_for_login,
auth.find_user_for_invite, auth.resolve_api_token, from 0015). Postgres
grants EXECUTE on newly created functions to PUBLIC by default, and
nothing revokes it — confirmed during the Phase 3 investigation, not
assumed. Those functions run as their owner (whoever runs migrations,
currently `wingrc`) regardless of caller, by design (SECURITY DEFINER) —
so they keep working once the app connects as wingrc_app with zero
changes, as long as they stay owned by the migration role.

Revision ID: 0016_app_role
Revises: 0015_auth_users
Create Date: 2026-07-22
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016_app_role"
down_revision: str | None = "0015_auth_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE = "wingrc_app"


def upgrade() -> None:
    # CREATE ROLE has no IF NOT EXISTS in Postgres — guard manually so this
    # migration is safe to run against a database where the role already
    # exists (e.g. re-applied in a fresh environment some other way).
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{_ROLE}') THEN
                CREATE ROLE {_ROLE} WITH
                    LOGIN
                    NOSUPERUSER
                    NOCREATEDB
                    NOCREATEROLE
                    NOREPLICATION
                    NOBYPASSRLS
                    INHERIT;
            END IF;
        END
        $$;
        """
    )

    op.execute(f"GRANT USAGE ON SCHEMA public TO {_ROLE}")
    op.execute(f"GRANT USAGE ON SCHEMA auth TO {_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {_ROLE}"
    )
    # FOR ROLE is omitted deliberately: it defaults to the role executing
    # this statement, which is whichever role runs migrations (currently
    # wingrc) — the same role that will CREATE every future table.
    op.execute(
        f"""
        ALTER DEFAULT PRIVILEGES IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {_ROLE}
        """
    )


def downgrade() -> None:
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE"
        f" ON TABLES FROM {_ROLE}"
    )
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA auth FROM {_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_ROLE}")
    op.execute(f"DROP ROLE IF EXISTS {_ROLE}")
