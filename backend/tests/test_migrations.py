"""Migration chain integration test.

Verifies that after `alembic upgrade head` the database revision matches the
declared head in the migration scripts.  Catches broken revision chains and
missing downgrade implementations.

Skipped automatically when no database is reachable (same as other integration
tests).

Run in-container:
    docker compose exec backend pytest tests/test_migrations.py -m integration -v
"""
from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine


@pytest.mark.integration
def test_migration_reaches_head(db_engine: Engine) -> None:
    """Current DB revision must equal the declared Alembic head after upgrade."""
    cfg = Config("alembic.ini")
    script = ScriptDirectory.from_config(cfg)
    expected = script.get_current_head()

    with db_engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        actual = ctx.get_current_revision()

    assert actual == expected, (
        f"DB is at revision {actual!r} but declared head is {expected!r}. "
        "Run 'alembic upgrade head'."
    )
