"""pytest fixtures for integration tests.

Integration tests require a Postgres instance dedicated to testing.
They MUST NOT run against the dev or production database.

Set WINGRC_TEST_DATABASE_URL to a test-only database URL:
    export WINGRC_TEST_DATABASE_URL=postgresql+psycopg://wingrc:wingrc@localhost:5432/wingrc_test

The test DB must already have the Alembic schema applied:
    WINGRC_DATABASE_URL=$WINGRC_TEST_DATABASE_URL alembic upgrade head

If WINGRC_TEST_DATABASE_URL is not set, integration tests are skipped.
Do NOT fall back to WINGRC_DATABASE_URL — that is the dev/prod database.

Run:
    WINGRC_TEST_DATABASE_URL=... pytest tests/ -m integration -q
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def _test_db_url() -> str | None:
    return os.environ.get("WINGRC_TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def db_engine():
    url = _test_db_url()
    if not url:
        pytest.skip(
            "WINGRC_TEST_DATABASE_URL not set — integration tests require a dedicated "
            "test database. Set this variable to a test-only DB URL and apply migrations."
        )
    engine = create_engine(url, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Test database not reachable ({exc})")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Function-scoped session that rolls back every write after the test.

    Uses SA 2.0's correct pattern: pass the Connection as a positional argument
    (not bind=) with join_transaction_mode="create_savepoint".  This makes
    session.commit() release a savepoint rather than commit the outer
    transaction, so all writes are rolled back when the outer transaction exits.

    The bind= keyword form is deprecated in SA 2.0 and does not reliably join
    the external transaction — it can grab a new pool connection and commit for
    real, which is the bug this fixture corrects.
    """
    with db_engine.connect() as conn:
        with conn.begin():
            sess = Session(conn, join_transaction_mode="create_savepoint")
            try:
                yield sess
            finally:
                sess.close()
        # conn.begin().__exit__ without commit → ROLLBACK — no writes reach the DB
