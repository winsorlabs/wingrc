"""pytest fixtures for integration tests.

Integration tests require a running Postgres instance. They are skipped
automatically if WINGRC_DATABASE_URL is not set or the DB is not reachable.

Run in-container:
    docker compose exec backend pytest tests/test_magic_loop.py -q

Run locally (with compose DB):
    WINGRC_DATABASE_URL=postgresql+psycopg://wingrc:wingrc@localhost:5432/wingrc \
        pytest tests/test_magic_loop.py -q
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


def _db_url() -> str | None:
    return os.environ.get("WINGRC_DATABASE_URL") or os.environ.get(
        "WINGRC_TEST_DATABASE_URL"
    )


@pytest.fixture(scope="session")
def db_engine():
    url = _db_url()
    if not url:
        pytest.skip("No database URL — set WINGRC_DATABASE_URL to run integration tests")
    engine = create_engine(url, pool_pre_ping=True, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"Database not reachable ({exc})")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Function-scoped session that rolls back after each test.

    Uses an outer transaction + SAVEPOINT so the test sees its own writes
    but nothing is ever committed to the real database.
    """
    with db_engine.connect() as conn:
        with conn.begin():
            sess = Session(bind=conn, join_transaction_mode="create_savepoint")
            try:
                yield sess
            finally:
                sess.close()
        # outer transaction auto-rolls-back here (no commit)
