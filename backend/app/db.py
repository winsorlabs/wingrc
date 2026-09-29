"""Database engine and session factory."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_settings = get_settings()


def build_connect_args() -> dict[str, int]:
    """libpq options every connection is made with.

    A function rather than a literal so it is testable without a database:
    `connect_args` passed to `create_engine` is captured in the pool's
    creator closure and cannot be read back off the engine.

    `connect_timeout` is the load-bearing one. See
    config.py:db_connect_timeout for what its absence measured.
    """
    return {"connect_timeout": _settings.db_connect_timeout}


# pool_size/max_overflow: see the reasoning in config.py's db_pool_size docstring.
engine = create_engine(
    _settings.database_url,
    pool_pre_ping=True,
    pool_size=_settings.db_pool_size,
    max_overflow=_settings.db_max_overflow,
    # Without this, an unreachable Postgres makes every connection attempt --
    # including /health's readiness check -- hang for minutes. See
    # config.py:db_connect_timeout for the measurements.
    connect_args=build_connect_args(),
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a session and always closes it."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
