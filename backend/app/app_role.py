"""The role the application connects as: its password, set as the backend
starts, and the reading `/health` reports.

**Why the password is set here, at the backend's start, and nowhere else.**
Migration 0016 creates `wingrc_app` with LOGIN and no password, because a
per-environment secret does not belong in a migration file. Until
something sets one, the role cannot authenticate. So a fresh install has
three constraints in a fixed order: the role exists only after
migrations run; only the owner can run `ALTER ROLE`; the app must not
serve until the role authenticates. The backend's start already runs
those in that order (`start.sh`: migrations as the owner, then this, then
uvicorn as the app role), and it already holds the owner's URL to run
migrations. Alternatives, rejected:

- **A migration that reads the variable.** A migration runs once per
  database, so a changed password in `.env` would never be applied, and a
  restored snapshot (the restore recreates the role with no password,
  `snapshots.py`) is already at head, so nothing would run. It would also
  make a migration's effect depend on the environment, and
  `alembic upgrade --sql` would write the secret into its output.
- **A documented command an operator runs.** A step a person can skip.
  Skipping it fails at the first connection, which is loud, but the same
  step is owed again after every restore and every rotation, and the
  guide already had one such step nobody ran: no page in `docs/` said how
  to set this password before 2026-11-23.
- **A one-shot compose service that holds the owner's URL** so the
  runtime never does. That is the right end state, and it is queued with
  "The app reaches MinIO as root" (`docs/roadmap.md`). It is a larger
  change: deploy.sh, the snapshot scripts and the CLI's owner override all
  read the owner's URL from the backend's environment today. When it
  lands, this module's `main` moves into that service unchanged.

The password is the one in `WINGRC_DATABASE_URL`, not a second variable,
so the role is always set to what the app will connect with. Compose
builds that URL from `WINGRC_APP_DB_PASSWORD` unless the URL is set
outright, as live's is.

**It writes only when the app role cannot authenticate.** It connects
as the app first; if that works it changes nothing. On live, which has
connected as `wingrc_app` since 2026-09-29, every start is that case.
It acts only on `wingrc_app`: a deployment that sets the URL to another
role is told so, and `/health` says what that role skips.

The password goes to the server as a SCRAM verifier computed by libpq
(`PQencryptPasswordConn`, what psql's `\\password` uses), so the
plaintext is never in a statement and never in a server log.
"""
from __future__ import annotations

from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool

APP_ROLE = "wingrc_app"


def _first_line(exc: OperationalError) -> str:
    orig = getattr(exc, "orig", None) or exc
    return str(orig).strip().splitlines()[0] if str(orig).strip() else type(orig).__name__


def _authenticates(url: str) -> OperationalError | None:
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return None
    except OperationalError as exc:
        return exc
    finally:
        engine.dispose()


def ensure_app_role_password(app_url: str, owner_url: str | None) -> str:
    """Make `wingrc_app` authenticate with the password in `app_url`.

    Returns what it did, for the start log. Raises SystemExit, naming the
    setting at fault, when the app role cannot be made to authenticate:
    the backend must not start serving on a connection that will fail.
    """
    app = make_url(app_url)
    if app.username != APP_ROLE:
        return (
            f"WINGRC_DATABASE_URL connects as {app.username!r}, not {APP_ROLE!r}: "
            "nothing to set. /health says whether that role skips row-level security."
        )
    if not app.password:
        raise SystemExit(
            f"WINGRC_DATABASE_URL names {APP_ROLE} with no password. Set "
            "WINGRC_APP_DB_PASSWORD in .env (deploy/generate-env.sh fills it)."
        )

    first = _authenticates(app_url)
    if first is None:
        return f"{APP_ROLE} authenticates: nothing set."

    if not owner_url or make_url(owner_url).username == APP_ROLE:
        raise SystemExit(
            f"{APP_ROLE} cannot connect ({_first_line(first)}), and there is no "
            "owner URL to set its password with. Set WINGRC_MIGRATION_DATABASE_URL "
            "to the owning role (docs/deployment.md, \"Database roles\")."
        )

    owner = create_engine(owner_url, poolclass=NullPool)
    try:
        with owner.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
            ).scalar()
            if not exists:
                raise SystemExit(
                    f"Role {APP_ROLE} does not exist. Migration 0016 creates it; "
                    "run migrations first (start.sh does)."
                )
            raw = conn.connection.driver_connection
            verifier = raw.pgconn.encrypt_password(
                app.password.encode(), APP_ROLE.encode(), b"scram-sha-256"
            ).decode()
            with raw.cursor() as cur:
                cur.execute(
                    sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                        sql.Identifier(APP_ROLE), sql.Literal(verifier)
                    )
                )
            conn.commit()
    finally:
        owner.dispose()

    after = _authenticates(app_url)
    if after is not None:
        raise SystemExit(
            f"Set {APP_ROLE}'s password, and it still cannot connect: "
            f"{_first_line(after)}"
        )
    return (
        f"{APP_ROLE} could not connect ({_first_line(first)}); "
        "set its password from WINGRC_DATABASE_URL."
    )


# What /health reports about the connection it is answering on. Read at
# runtime, never inferred from the compose file: live's role was set in
# .env, and for seven weeks nothing anywhere said which one it was.
#
# `owner_skips` counts tables with row-level security that this role
# skips as their owner: Postgres exempts a table's owner, and any role
# with the owner's privileges, unless FORCE ROW LEVEL SECURITY is set.
# A superuser or BYPASSRLS role skips every policy regardless.
ROLE_SQL = text(
    """
    SELECT current_user AS role, r.rolsuper AS superuser, r.rolbypassrls AS bypassrls,
           (SELECT count(*) FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
               AND c.relrowsecurity AND NOT c.relforcerowsecurity
               AND pg_has_role(current_user, c.relowner, 'USAGE')) AS owner_skips
      FROM pg_roles r WHERE r.rolname = current_user
    """
)


def row_level_security(role: str, superuser: bool, bypassrls: bool, owner_skips: int) -> dict:
    """The `/health` reading. `row_level_security` is "enforced" only when
    none of the three exemptions applies; otherwise it names the one that
    does."""
    reading = {
        "role": role,
        "superuser": bool(superuser),
        "bypassrls": bool(bypassrls),
        "owner_of_rls_tables": int(owner_skips),
    }
    if superuser:
        why = f"{role} is a superuser"
    elif bypassrls:
        why = f"{role} has BYPASSRLS"
    elif owner_skips:
        why = f"{role} owns {owner_skips} tables with row-level security, so their policies skip it"
    else:
        return {**reading, "row_level_security": "enforced"}
    return {
        **reading,
        "row_level_security": (
            f"bypassed: {why}. The database does not keep tenants apart on this "
            "deployment; only the application's checks do. See docs/deployment.md, "
            "\"Database roles\"."
        ),
    }


def main() -> None:
    from .config import get_settings

    s = get_settings()
    said = ensure_app_role_password(s.database_url, s.migration_database_url)
    print(f"app role: {said}", flush=True)


if __name__ == "__main__":
    main()
