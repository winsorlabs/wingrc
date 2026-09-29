"""The RLS org-context hook itself (app/rls.py).

Two jobs here, and they are deliberately separate because the rest of the
suite cannot do either.

**1. Prove the `after_begin` wiring against real commits.** Every other
test runs on `conftest.py:db_session`, which uses
`join_transaction_mode="create_savepoint"` so a test can roll back. Under
that mode an app-level `commit()` only releases a savepoint, the real
transaction never ends, and no new one begins — so `after_begin` never
fires and `_app_session` has to invoke `reapply_current_org` by hand to
model production. That is the right call for the suite, but it means the
suite alone would pass even if the event were registered on the wrong
hook, or not registered at all. These tests therefore use their own
session with real `COMMIT`s and clean up after themselves.

**2. Keep the chokepoint a chokepoint.** The hook re-applies whatever
`set_current_org()` recorded. Code that writes `app.current_org` directly
would leave the recorded value stale, and the hook would then confidently
re-apply the *wrong* org after a commit — worse than the bug it replaces.
A source guard is what makes that structural instead of remembered. It is
not hypothetical: converting the existing call sites turned up six raw
writers in `routers/auth.py` that a first grep had missed.
"""

from __future__ import annotations

import pathlib
import re
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Contact, Organization
from app.rls import clear_current_org, current_org, set_current_org

pytestmark = pytest.mark.integration

_APP_ROLE = "wingrc_app"
_APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


# ---------------------------------------------------------------------------
# 1. The hook, against real commits
# ---------------------------------------------------------------------------


@pytest.fixture
def real_session(db_engine):
    """A session with real transactions, not the savepoint harness.

    Rolls its own cleanup because nothing else will: the outer-transaction
    rollback `db_session` relies on is exactly what this fixture gives up
    in order to exercise a genuine COMMIT.
    """
    session = Session(db_engine, expire_on_commit=False)
    created_org_ids: list[uuid.UUID] = []
    try:
        yield session, created_org_ids
    finally:
        session.rollback()
        clear_current_org(session)
        if created_org_ids:
            session.execute(
                text("DELETE FROM contact WHERE org_id = ANY(:ids)"),
                {"ids": created_org_ids},
            )
            session.execute(
                text("DELETE FROM organization WHERE id = ANY(:ids)"),
                {"ids": created_org_ids},
            )
            session.commit()
        session.close()


def _seed_org(session: Session, created: list[uuid.UUID]) -> Organization:
    org = Organization(name=f"RlsCtxOrg-{uuid.uuid4().hex[:8]}")
    session.add(org)
    session.commit()
    created.append(org.id)
    return org


def test_org_context_survives_a_real_commit(real_session):
    """The core claim. Set the org, commit (ending the transaction the
    `SET LOCAL` was scoped to), then read — and get the row.
    """
    session, created = real_session
    org = _seed_org(session, created)
    contact = Contact(
        org_id=org.id, name="Ada Lovelace", email="ada@example.com", affiliation="customer"
    )
    session.add(contact)
    session.commit()

    session.connection().execute(text(f"SET ROLE {_APP_ROLE}"))
    try:
        set_current_org(session, org.id)
        assert session.scalars(select(Contact).where(Contact.org_id == org.id)).all()

        # The commit ends the transaction; app.current_org dies with it.
        # Everything after this line depends on the hook.
        session.commit()

        assert session.execute(
            text("SELECT current_setting('app.current_org', true)")
        ).scalar() == str(org.id), "after_begin did not re-apply the recorded org"

        rows = session.scalars(select(Contact).where(Contact.org_id == org.id)).all()
        assert len(rows) == 1, "post-commit read matched zero rows under RLS"
    finally:
        session.rollback()
        session.connection().execute(text("RESET ROLE"))
        session.commit()


def test_post_commit_refresh_returns_the_row(real_session):
    """The shape N.1 hit: `session.refresh()` after a commit raised
    ObjectDeletedError on a row that plainly existed.
    """
    session, created = real_session
    org = _seed_org(session, created)
    contact = Contact(
        org_id=org.id, name="Grace Hopper", email="grace@example.com", affiliation="msp"
    )
    session.add(contact)
    session.commit()

    session.connection().execute(text(f"SET ROLE {_APP_ROLE}"))
    try:
        set_current_org(session, org.id)
        loaded = session.scalars(select(Contact).where(Contact.id == contact.id)).one()
        loaded.name = "Grace B. Hopper"
        session.commit()

        session.refresh(loaded)
        assert loaded.name == "Grace B. Hopper"
    finally:
        session.rollback()
        session.connection().execute(text("RESET ROLE"))
        session.commit()


def test_org_context_tracks_the_latest_value_across_commits(real_session):
    """The scheduler's shape: a loop that changes org per iteration and
    commits in between. The hook must re-apply the *current* org, not the
    first one it ever saw.
    """
    session, created = real_session
    org_a = _seed_org(session, created)
    org_b = _seed_org(session, created)

    session.connection().execute(text(f"SET ROLE {_APP_ROLE}"))
    try:
        set_current_org(session, org_a.id)
        session.commit()
        assert current_org(session) == org_a.id

        set_current_org(session, org_b.id)
        session.commit()
        assert session.execute(
            text("SELECT current_setting('app.current_org', true)")
        ).scalar() == str(org_b.id), "the hook re-applied a stale org"
    finally:
        session.rollback()
        session.connection().execute(text("RESET ROLE"))
        session.commit()


def test_hook_is_a_no_op_when_no_org_was_ever_set(real_session):
    """The CLI and the suite's owner-role scaffolding sessions never set an
    org. The hook must leave them alone rather than guessing one.
    """
    session, _created = real_session
    assert current_org(session) is None
    session.execute(text("SELECT 1"))
    session.commit()
    assert session.execute(text("SELECT current_setting('app.current_org', true)")).scalar() in (
        "",
        None,
    )


def test_clear_current_org_stops_the_hook_re_applying(real_session):
    session, created = real_session
    org = _seed_org(session, created)
    set_current_org(session, org.id)
    session.commit()

    clear_current_org(session)
    session.commit()
    assert current_org(session) is None
    assert session.execute(text("SELECT current_setting('app.current_org', true)")).scalar() in (
        "",
        None,
    )


# ---------------------------------------------------------------------------
# 2. The chokepoint guard
# ---------------------------------------------------------------------------

# Matches an actual write to the GUC, not a mention of it in prose.
_RAW_WRITE = re.compile(
    r"""(SET\s+LOCAL\s+app\.current_org|set_config\(\s*['"]app\.current_org['"])""",
    re.IGNORECASE,
)


def _source_files() -> list[pathlib.Path]:
    return [
        p
        for p in _APP_DIR.rglob("*.py")
        # Migrations legitimately contain the string inside policy DDL and
        # in SECURITY DEFINER function bodies; they are schema, not request
        # code, and are not reading session.info.
        if "migrations" not in p.parts and p.name != "rls.py"
    ]


def test_no_module_writes_app_current_org_outside_rls():
    """`rls.py` must be the only writer.

    A direct write elsewhere would leave `session.info` stale, and the hook
    would then re-apply the wrong org after the next commit — a silent
    cross-tenant read, which is strictly worse than the silent empty result
    this whole change exists to remove.
    """
    offenders: list[str] = []
    for path in _source_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or not _RAW_WRITE.search(line):
                continue
            # A docstring/comment mention is fine; an executable statement
            # is not. Require the line to look like a call.
            if ".execute(" in line or "op.execute(" in line:
                offenders.append(f"{path.relative_to(_APP_DIR.parent)}:{lineno}: {stripped}")

    assert not offenders, (
        "app.current_org must only be written through rls.set_current_org() -- "
        "a direct write leaves session.info stale and the after_begin hook would "
        "then re-apply the wrong org. Offenders:\n  " + "\n  ".join(offenders)
    )


def test_rls_module_registers_the_hook_on_the_session_class():
    """Registered on `Session` itself, not on `db.py:SessionLocal` — the
    test suite builds `Session(conn, ...)` directly and must be covered by
    the same mechanism production uses.
    """
    from sqlalchemy import event

    assert event.contains(Session, "after_begin", _hook()), (
        "rls.py's after_begin listener is not registered on the Session class"
    )


def _hook():
    from app.rls import _reapply_org_on_new_transaction

    return _reapply_org_on_new_transaction
