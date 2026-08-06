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
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest
from alembic import command as alembic_cmd
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.auth import CurrentUser

# Non-superuser, non-owner role Phase 3 introduces so RLS is an enforced
# backstop rather than a no-op (superusers and table owners bypass RLS
# unconditionally). Created idempotently by migration 0016; see that
# migration for the GRANT/ALTER DEFAULT PRIVILEGES shape.
_APP_ROLE = "wingrc_app"

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
        mfa_enrolled=True,
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

    Runs as the connection's login role (`wingrc`, the table owner), which
    bypasses RLS entirely — this is the session test bodies use directly to
    scaffold fixtures (orgs, frameworks, controls, assessments) via raw
    SQLAlchemy, outside of any request/org context. Those inserts have no
    `app.current_org` to satisfy an RLS WITH CHECK clause, so scaffolding
    must stay privileged, exactly as it did before Phase 3. Only
    `_app_session` (below) — the override actually bound to the app's
    request-handling DB dependency — runs as the RLS-enforced `wingrc_app`
    role, and only for the duration of a single request.

    SA 2.0 caveat: `with conn.begin():` COMMITS on normal exit — its __exit__
    calls self.commit() when no exception is raised (type_ is None). Use
    explicit trans.rollback() in finally instead to guarantee rollback
    regardless of test outcome.

    `expire_on_commit=False` matches app/db.py's production `SessionLocal`.
    Without it, the default `expire_on_commit=True` marks every attribute
    on committed instances stale, so the next attribute access (e.g.
    `ev.id` right after `session.commit()`, a totally normal pattern in the
    routers) issues an implicit SELECT to refresh it. Under `_app_session`,
    that SELECT runs after `app.current_org` has been RESET for the commit
    it just followed, so RLS matches zero rows and SQLAlchemy raises
    ObjectDeletedError — a test-harness artifact, not a real bug: production
    never re-queries on attribute access here because it isn't expiring
    those attributes in the first place.
    """
    with db_engine.connect() as conn:
        trans = conn.begin()
        sess = Session(conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield sess
        finally:
            sess.close()
            trans.rollback()


def _authed(session: Session, user: CurrentUser):
    """Build a get_current_user override that also sets app.current_org.

    The real get_current_user (`_resolve_session`/`_resolve_api_token` in
    app/auth.py) sets `app.current_org` as a side effect of authenticating
    every request. Tests bypass that function entirely via
    `app.dependency_overrides[get_current_user] = ...` — so without this
    helper, `app.current_org` is never set at all, and every RLS-protected
    query would return zero rows once running under a real (non-bypassing)
    role, regardless of anything Phase 3 fixes. This closure is called once
    per request (FastAPI re-resolves dependencies per request), matching
    the real code's per-request SET LOCAL.
    """

    def _override() -> CurrentUser:
        # Postgres doesn't allow bind parameters in SET/SET LOCAL — mirrors
        # the literal-embedding in app/auth.py's _resolve_session /
        # _resolve_api_token. Safe here because org_id is a uuid.UUID, not
        # unsanitized input.
        session.execute(text(f"SET LOCAL app.current_org = '{user.org_id}'"))
        return user

    return _override


def _app_session(session: Session):
    """Build a get_session override that enforces RLS for a single request.

    `db_session` stays on the connection's default owner role so test-body
    scaffolding keeps bypassing RLS as it always has. This override shares
    that same connection/transaction but brackets *just* the request: it
    SET ROLEs to `wingrc_app` right before FastAPI hands the session to the
    endpoint, and RESETs the role in the `finally` once the request
    completes — before control returns to the test body. `SET ROLE` is a
    property of the underlying connection, not the ORM Session object, so
    without this bracketing a switch to `wingrc_app` would leak onto every
    later use of `db_session` in the same test (and vice versa, onto every
    earlier use, if applied at fixture-setup time instead of per-request).

    The commit-wrapping mirrors that same request-only scope:
    `join_transaction_mode="create_savepoint"` means an app-level
    `session.commit()` only releases a SAVEPOINT — it does not end the real
    transaction, so `SET LOCAL app.current_org` (scoped to the real
    transaction) would otherwise survive every commit, masking the exact
    class of bug Phase 3 is auditing for (a query after a commit running
    with stale/no org context). Wrapping `.commit` here to RESET the GUC
    makes a commit inside a request behave like a commit in production.
    """

    def _override() -> Iterator[Session]:
        conn = session.connection()
        conn.execute(text(f"SET ROLE {_APP_ROLE}"))
        real_commit = session.commit

        def _commit_and_clear_org(*args, **kwargs):
            real_commit(*args, **kwargs)
            session.execute(text("RESET app.current_org"))

        session.commit = _commit_and_clear_org

        try:
            yield session
        finally:
            session.commit = real_commit
            conn.execute(text("RESET ROLE"))

    return _override


def _set_cookie_value(response, name: str) -> str | None:
    """Pull a cookie's raw value directly out of the response's Set-Cookie
    headers, rather than relying on the client's cookie jar. The app scopes
    state cookies to path=/api/auth and the session cookie to path=/api
    (matching the deployed nginx-proxied layout, where the public path is
    /api/...), which doesn't match the bare /auth path TestClient hits
    directly against the FastAPI app — so jar-based auto-propagation across
    requests can't be relied on here.
    """
    for raw in response.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            first_segment = raw.split(";", 1)[0]
            value = first_segment.split("=", 1)[1]
            return value or None
    return None


def _is_cleared(response, name: str) -> bool:
    """True if the response clears `name` via an empty value + Max-Age=0,
    matching auth.clear_state_cookie()/clear_session_cookie().

    Python's http.cookies module (which Starlette's Response.set_cookie
    uses under the hood) renders an empty string value as a literal
    quoted `""`, not a bare trailing `=` — e.g. `name=""; ...; Max-Age=0`,
    not `name=; ...; Max-Age=0`. A real (non-empty) cookie value is never
    quoted this way, so checking for either the bare or quoted-empty form
    is sufficient and doesn't risk false-positiving on a real value.

    Checking Max-Age=0 (not just the value) is the point of this helper,
    not an extra precaution: an empty value with no expiry still persists
    in a real cookie jar, so a test — or a frontend redirect — that only
    checks "the value looks blank" would silently pass even if the
    endpoint forgot to actually expire the cookie.
    """
    for raw in response.headers.get_list("set-cookie"):
        if raw.startswith(f"{name}="):
            first_segment = raw.split(";", 1)[0]
            value = first_segment.split("=", 1)[1]
            return value in ("", '""') and "Max-Age=0" in raw
    return False
