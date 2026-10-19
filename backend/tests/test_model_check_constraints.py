"""models.py's CHECK constraints are the database's (roadmap item Y).

The tests run against the migrated schema, so a model's copy of a CHECK is
never exercised: `ck_evidence_artifact_type` lacked 'attestation' for five
weeks after migration 0045 and nothing noticed. Alembic's autogenerate does
not compare CHECKs either.

The comparison: build the models into a scratch schema with
`Base.metadata.create_all` (through `schema_translate_map`), then read both
schemas' CHECKs back with `pg_get_constraintdef`, keyed by (table, name),
and require the same names on both sides and identical definitions.
Comparing two strings Postgres produced avoids normalising expressions by
hand (it rewrites `IN (...)` as `= ANY (ARRAY[...])` and adds casts).
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, text

from app.models import Base

_SCRATCH = "ck_drift_scratch"

_CHECKS = text("""
    SELECT t.relname, c.conname, pg_get_constraintdef(c.oid)
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE c.contype = 'c' AND n.nspname = :schema
    ORDER BY t.relname, c.conname
""")


def test_every_model_check_constraint_is_named():
    """An unnamed CHECK gets a generated name on each side, so the
    comparison below could not pair it."""
    unnamed = sorted(
        f"{table.name}: {c.sqltext}"
        for table in Base.metadata.tables.values()
        for c in table.constraints
        if isinstance(c, CheckConstraint) and not c.name
    )
    assert unnamed == [], "name every CheckConstraint in models.py:\n" + "\n".join(unnamed)


def _checks(conn, schema: str, tables: set[str]) -> dict[tuple[str, str], str]:
    return {
        (table, name): definition
        for table, name, definition in conn.execute(_CHECKS, {"schema": schema})
        if table in tables
    }


@pytest.mark.integration
def test_model_check_constraints_match_the_migrated_database(db_session):
    conn = db_session.connection()
    tables = set(Base.metadata.tables)
    conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCRATCH} CASCADE"))
    conn.execute(text(f"CREATE SCHEMA {_SCRATCH}"))
    try:
        Base.metadata.create_all(
            conn.execution_options(schema_translate_map={None: _SCRATCH})
        )
        model = _checks(conn, _SCRATCH, tables)
        migrated = _checks(conn, "public", tables)
    finally:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCRATCH} CASCADE"))
    assert model, "fixture: create_all wrote no CHECK into the scratch schema; nothing compared"

    problems = []
    for key in sorted(set(model) | set(migrated)):
        table, name = key
        if key not in migrated:
            problems.append(f"{table}.{name}: in models.py only\n  model: {model[key]}")
        elif key not in model:
            problems.append(f"{table}.{name}: in the database only\n  database: {migrated[key]}")
        elif model[key] != migrated[key]:
            problems.append(
                f"{table}.{name}: definitions differ\n"
                f"  database: {migrated[key]}\n  model:    {model[key]}"
            )
    assert problems == [], (
        f"{len(problems)} CHECK constraint(s) differ between models.py and the migrated "
        "database. Fix each on its own (roadmap item Y):\n" + "\n".join(problems)
    )
