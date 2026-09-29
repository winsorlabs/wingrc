"""Row-level-security org context: the one place it is set, and the hook
that makes it survive a commit.

**The problem this exists to solve.** RLS scoping is a PostgreSQL GUC,
`app.current_org`, set with `SET LOCAL`/`set_config(..., true)` — which is
*transaction*-scoped. `session.commit()` ends that transaction, so the
setting is gone. Any database read issued after a commit therefore runs
with no org context and, under an RLS-enforcing role, matches zero rows.

That is not a hypothetical failure mode and it is not always loud:

  - `routers/scope.py` carries two comments about it (its Liongard
    environment mapping sets `updated_at` in Python and deliberately skips
    a post-commit `refresh()`), and N.1 hit it a third time in
    `routers/documents.py` as an `ObjectDeletedError` on a row that
    plainly existed.
  - `routers/assessments.py:upsert_statements` hit it a fourth time and
    **failed silently** — a zero-row `select()` is not an error, so the
    endpoint answered 200 with `control_state_id: null` for every
    statement. No exception, no log line.

That last one is why this is a hook rather than a convention. A convention
only protects against mistakes someone can notice, and here the failure
looks exactly like success. It is also the same move the `repo.upsert`
guard made: put the invariant in the place every writer must pass, rather
than in a rule every writer must remember. This codebase has four
documented instances of an older writer routing around a newer guard;
none of them were caught by the rule holding.

**How it works.** `set_current_org()` is the single chokepoint: it issues
the `set_config` *and* records the value on `session.info`. The
`after_begin` hook below re-applies that recorded value every time a new
transaction begins on the session — which is exactly what happens on the
first statement after a commit. `after_begin` rather than `after_commit`
on purpose: SQLAlchemy documents `after_begin` as the place to apply
transaction-scoped settings and hands it the `Connection` to do it on,
whereas the Session is explicitly documented as not being able to emit SQL
from `after_commit`.

**How the hook knows the org, and when it refuses to guess.** Only from
`session.info`, only ever written by `set_current_org()`. There is no
fallback, no inference from the request, and no default: a session that
has never had an org set gets nothing, which is correct for the CLI and
for the owner-role scaffolding sessions in the test suite. A hook that set
the *wrong* org would be worse than the bug it replaces, so the recorded
value is the only source and `tests/test_rls_context.py` enforces that no
other code writes the GUC directly.

The value is always the right one to re-apply, including in the one place
it changes mid-session: `scheduler.py` loops over orgs and calls
`set_current_org()` per iteration, so the recorded value tracks the loop.
Every other caller (`auth.py`'s session/token resolution and
`require_org_access`, `manage.py`'s bootstrap) sets exactly one value for
the life of the request.

**The honest cost.** The hook is implicit. A future reader of
`upsert_statements` will not see why its post-commit read works. That is
mitigated with a comment here and one at that call site — not a comment at
every call site, which nobody maintains.
"""

from __future__ import annotations

import uuid

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

# Key under which the active org is recorded on `Session.info`. Namespaced
# because `Session.info` is a shared dict any library may write to.
_ORG_KEY = "wingrc_current_org"

# `set_config(setting, value, is_local=true)` is the parameterized
# equivalent of `SET LOCAL`. Plain `SET LOCAL` cannot take bind parameters
# at all — a real Postgres syntax error, caught live on the bench stack
# rather than a style preference — which is why the older call sites this
# module replaced embedded the uuid by f-string instead.
_SET_ORG_SQL = text("SELECT set_config('app.current_org', :org_id, true)")
_RESET_ORG_SQL = text("SELECT set_config('app.current_org', '', true)")


def _apply(executor: Session | Connection, org_id: uuid.UUID) -> None:
    executor.execute(_SET_ORG_SQL, {"org_id": str(org_id)})


def set_current_org(session: Session, org_id: uuid.UUID) -> None:
    """Scope this session's RLS context to `org_id`, durably across commits.

    The single supported way to set `app.current_org`. Records the value so
    the `after_begin` hook can re-apply it after a commit ends the
    transaction the `set_config` was scoped to.
    """
    session.info[_ORG_KEY] = org_id
    _apply(session, org_id)


def current_org(session: Session) -> uuid.UUID | None:
    """The org this session is scoped to, or None if it was never set."""
    return session.info.get(_ORG_KEY)


def clear_current_org(session: Session) -> None:
    """Drop the org context and stop re-applying it.

    Used by tests that need to prove a query really is RLS-gated. No
    production path calls this: a request's session is closed at the end of
    the request rather than being handed on to a different org.
    """
    session.info.pop(_ORG_KEY, None)
    session.execute(_RESET_ORG_SQL)


def reapply_current_org(session: Session, connection: Connection | None = None) -> bool:
    """Re-issue the recorded org onto a freshly-begun transaction.

    Returns True if an org was recorded and applied, False if the session
    has none — the CLI and the suite's owner-role scaffolding sessions are
    the normal False case, not an error.

    Exported (rather than staying private to the hook) so
    `tests/conftest.py` can invoke the *production* function at the point
    its savepoint-based harness suppresses the real transaction boundary,
    instead of reimplementing it. See that fixture's own comment.
    """
    org_id = session.info.get(_ORG_KEY)
    if org_id is None:
        return False
    _apply(connection if connection is not None else session, org_id)
    return True


@event.listens_for(Session, "after_begin")
def _reapply_org_on_new_transaction(
    session: Session, transaction: object, connection: Connection
) -> None:
    """Re-apply the session's org every time a transaction begins.

    Registered on the `Session` class itself, not on `db.py`'s
    `SessionLocal`, because the test suite constructs `Session(conn, ...)`
    directly and must be covered by the same mechanism the app uses.

    Fires on the *first* begin too, before any org has been set — that is
    the no-op case and is why `reapply_current_org` returns rather than
    raising when nothing is recorded.
    """
    reapply_current_org(session, connection)
