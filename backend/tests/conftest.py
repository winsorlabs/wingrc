"""pytest fixtures for integration tests.

Tests run against a dedicated wingrc_test database — never the dev DB.
The conftest auto-creates and migrates wingrc_test on session start (idempotent).

Default test DB (docker-compose): postgresql+psycopg://wingrc:wingrc@db:5432/wingrc_test
Override via WINGRC_TEST_DATABASE_URL.

Run in-container:
    docker compose exec backend pytest tests/ -q
"""
from __future__ import annotations

import os
import uuid as _uuid
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest
from alembic import command as alembic_cmd
from alembic.config import Config as AlembicConfig
from app.auth import CurrentUser
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_ALEMBIC_INI = str(Path(__file__).resolve().parents[1] / "alembic.ini")
_DEFAULT_DEV_URL = "postgresql+psycopg://wingrc:wingrc@db:5432/wingrc"


def _test_db_url() -> str | None:
    return os.environ.get("WINGRC_TEST_DATABASE_URL")


def _ensure_test_db(test_url: str) -> None:
    """Create the test DB if absent and apply Alembic migrations (idempotent).

    Skips (via pytest.skip) if the postgres host is unreachable — this keeps
    CI pipelines that have no DB service from erroring out.
    """
    from sqlalchemy.exc import OperationalError

    parsed = urlparse(test_url)
    db_name = parsed.path.lstrip("/")
    maintenance_url = urlunparse(parsed._replace(path="/postgres"))

    # CREATE DATABASE must run outside a transaction (AUTOCOMMIT).
    maint_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with maint_engine.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_name}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    except OperationalError as exc:
        pytest.skip(f"Test database not reachable — skipping integration tests ({exc})")
    finally:
        maint_engine.dispose()

    # env.py calls get_settings().database_url at import time and overrides the
    # alembic config URL.  Temporarily redirect WINGRC_DATABASE_URL to the test
    # DB so Alembic migrates the right target, then restore.
    from app.config import get_settings

    old_val = os.environ.get("WINGRC_DATABASE_URL")
    os.environ["WINGRC_DATABASE_URL"] = test_url
    get_settings.cache_clear()
    try:
        cfg = AlembicConfig(_ALEMBIC_INI)
        alembic_cmd.upgrade(cfg, "head")
    finally:
        if old_val is None:
            os.environ.pop("WINGRC_DATABASE_URL", None)
        else:
            os.environ["WINGRC_DATABASE_URL"] = old_val
        get_settings.cache_clear()


def _make_fake_user(**kwargs):
    """Return a CurrentUser for dependency_overrides in tests."""
    defaults = dict(
        id=_uuid.uuid4(),
        org_id=_uuid.uuid4(),
        email="test-admin@example.com",
        display_name="Test Admin",
        role="msp_admin",
        is_active=True,
        login_method="local",
    )
    defaults.update(kwargs)
    return CurrentUser(**defaults)


@pytest.fixture
def fake_msp_admin():
    return _make_fake_user()


@pytest.fixture(scope="session")
def db_engine():
    test_url = _test_db_url()
    if not test_url:
        pytest.skip(
            "WINGRC_TEST_DATABASE_URL not set — integration tests require a dedicated "
            "test database. Set this variable to a test-only DB URL."
        )

    dev_url = os.environ.get("WINGRC_DATABASE_URL", _DEFAULT_DEV_URL)
    if test_url == dev_url:
        pytest.fail(
            "WINGRC_TEST_DATABASE_URL must differ from WINGRC_DATABASE_URL — "
            "tests must never write to the dev/prod database."
        )

    _ensure_test_db(test_url)

    engine = create_engine(test_url, pool_pre_ping=True, future=True)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Function-scoped session that always rolls back after each test.

    SA 2.0 caveat: `with conn.begin():` COMMITS on normal exit — its __exit__
    calls self.commit() when no exception is raised (type_ is None). Use
    explicit trans.rollback() in finally instead to guarantee rollback
    regardless of test outcome.
    """
    with db_engine.connect() as conn:
        trans = conn.begin()
        sess = Session(conn, join_transaction_mode="create_savepoint")
        try:
            yield sess
        finally:
            sess.close()
            trans.rollback()
