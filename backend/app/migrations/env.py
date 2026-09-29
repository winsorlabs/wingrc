"""Alembic migration environment.

**Which connection migrations run on, and why it is not simply
`WINGRC_DATABASE_URL`.**

DDL needs the table owner. The running application should eventually
connect as `wingrc_app`, which deliberately has neither DDL rights nor the
ability to bypass RLS. Those are different privileges, so once the app
cuts over they cannot be the same connection string --
`alembic upgrade head` would fail on the first `CREATE TABLE`.

`WINGRC_MIGRATION_DATABASE_URL` is that second string, consumed here and
nowhere else. It is optional, and unset is the correct configuration today
because `WINGRC_DATABASE_URL` still points at the owner.

Two guards make a wrong configuration loud rather than quiet, which is the
point -- a migration silently running as the wrong role, or against the
wrong database, is how a cutover gets undone without anyone noticing:

  1. **Same database.** If the migration URL names a different host/port/
     database than the app URL, refuse. Upgrading one database while the
     app serves another is the failure that otherwise looks exactly like
     success.
  2. **Can actually do DDL.** Before running anything, check the connected
     role holds CREATE on the schema. This is what turns the post-cutover
     mistake ("forgot to set the migration URL") from a confusing
     permission error partway through a migration into a single message
     naming the variable to set.

Neither guard falls back to the app connection. Falling back is the
behaviour they exist to prevent.
"""

from __future__ import annotations

from logging.config import fileConfig
from urllib.parse import urlsplit

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_settings = get_settings()


def _target(url: str) -> tuple[str | None, int | None, str]:
    """(host, port, database) -- the parts that must match. User and
    password are exactly what is expected to differ between the two."""
    parts = urlsplit(url)
    return parts.hostname, parts.port, parts.path.lstrip("/")


def _resolve_migration_url() -> str:
    app_url = _settings.database_url
    migration_url = _settings.migration_database_url
    if not migration_url:
        return app_url

    if _target(migration_url) != _target(app_url):
        raise SystemExit(
            "WINGRC_MIGRATION_DATABASE_URL points at a different database than "
            "WINGRC_DATABASE_URL.\n"
            f"  migrations: {_target(migration_url)}\n"
            f"  app:        {_target(app_url)}\n"
            "They must name the same host, port and database and differ only in "
            "the role. Refusing to migrate a database the application is not "
            "serving."
        )
    return migration_url


config.set_main_option("sqlalchemy.url", _resolve_migration_url())
target_metadata = Base.metadata


def _assert_can_run_ddl(connection) -> None:
    """Fail by name if the connected role cannot create objects.

    Without this, forgetting `WINGRC_MIGRATION_DATABASE_URL` after the
    `wingrc_app` cutover surfaces as a permission error from whichever
    statement happens to run first -- possibly several migrations in, with
    a message that names a table rather than the setting at fault.
    """
    role = connection.execute(text("SELECT current_user")).scalar()
    can_create = connection.execute(
        text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
    ).scalar()
    if not can_create:
        raise SystemExit(
            f"Database role {role!r} cannot create objects in schema public, so "
            "migrations cannot run as it.\n"
            "Set WINGRC_MIGRATION_DATABASE_URL to a connection string for the "
            "owning role (the same host/port/database as WINGRC_DATABASE_URL, "
            "different user). This is expected once the application itself "
            "connects as wingrc_app -- see docs/deployment.md."
        )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _assert_can_run_ddl(connection)
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
