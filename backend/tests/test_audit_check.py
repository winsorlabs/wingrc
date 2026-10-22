"""The audit check itself (tests/audit_check.py): it must actually check."""
from __future__ import annotations

import pytest

from tests import audit_check
from tests.audit_check import RequestWrites, Write


def _observe(*statements):
    audit_check.begin("POST", "/x")
    try:
        for sql, params in statements:
            audit_check.before_cursor_execute(None, None, sql, params, None, False)
        audit_check.committed()
        return audit_check._current
    finally:
        audit_check._current = None


@pytest.mark.parametrize("sql, write", [
    ("INSERT INTO evidence (id) VALUES (%(id)s)", Write("INSERT", "evidence")),
    ('UPDATE "control_state" SET status=%(status)s, updated_at=now() WHERE id=%(id)s',
     Write("UPDATE", "control_state", frozenset({"status", "updated_at"}))),
    ("DELETE FROM public.evidence_state_link WHERE id=%(id)s",
     Write("DELETE", "evidence_state_link")),
    ("UPDATE user SET locked_until=coalesce(%(a)s, now()), failed=1 RETURNING id",
     Write("UPDATE", "user", frozenset({"locked_until", "failed"}))),
])
def test_it_reads_what_a_statement_writes(sql, write):
    assert _observe((sql, {})).committed == [write]


@pytest.mark.parametrize("sql", [
    "SELECT * FROM evidence",
    "SET ROLE wingrc_app",
    "SELECT set_config('app.current_org', 'x', true)",
])
def test_a_read_is_not_a_write(sql):
    assert _observe((sql, {})).committed == []


def test_it_reads_the_entity_type_an_audit_row_names():
    cur = _observe(
        ("INSERT INTO audit_log (entity_type) VALUES (%(entity_type)s)",
         {"entity_type": "evidence"}),
        ("INSERT INTO audit_log (entity_type) VALUES (%(entity_type__0)s), (%(entity_type__1)s)",
         {"entity_type__0": "evidence_task", "entity_type__1": "evidence_task"}),
    )
    assert cur.audited == {"evidence", "evidence_task"}


def test_a_write_with_no_audit_row_is_unaccounted():
    status = Write("UPDATE", "control_state", frozenset({"status"}))
    w = RequestWrites("PATCH", "/cs", committed=[status])
    assert w.unaccounted() == {"control_state": ["UPDATE(status)"]}


def test_an_audit_row_for_something_else_does_not_account_for_a_write():
    """An assessment's creation once passed on its RACI copy-forward row:
    "some audit row exists" is satisfied by something irrelevant."""
    w = RequestWrites("POST", "/assessments", committed=[Write("INSERT", "assessment")],
                      audited={"raci_assignment"})
    assert w.unaccounted() == {"assessment": ["INSERT"]}


def test_an_audit_row_naming_the_table_accounts_for_it():
    w = RequestWrites("POST", "/e", committed=[Write("INSERT", "evidence")], audited={"evidence"})
    assert w.unaccounted() == {}


def test_a_rolled_back_write_is_not_counted():
    cur = _observe()
    audit_check._current = cur
    try:
        audit_check.before_cursor_execute(
            None, None, "INSERT INTO evidence (id) VALUES (1)", {}, None, False
        )
        audit_check.rolled_back()
        audit_check.committed()
        assert cur.committed == []
        assert cur.unaccounted() == {}
    finally:
        audit_check._current = None


def test_a_request_that_writes_nothing_needs_no_exemption():
    assert RequestWrites("POST", "/dry-run").unaccounted() == {}


def test_a_column_rule_never_covers_another_column(monkeypatch):
    monkeypatch.setattr(audit_check, "RULES", [
        audit_check._r("user", "UPDATE", None, "test", columns={"last_login_at"}),
    ])
    login = Write("UPDATE", "user", frozenset({"last_login_at"}))
    role_too = Write("UPDATE", "user", frozenset({"last_login_at", "role"}))
    assert RequestWrites("POST", "/login", committed=[login]).unaccounted() == {}
    assert RequestWrites("PATCH", "/u", committed=[role_too]).unaccounted() == {
        "user": ["UPDATE(last_login_at,role)"]}
    assert RequestWrites("POST", "/u", committed=[Write("INSERT", "user")]).unaccounted() == {
        "user": ["INSERT"]}


def test_a_derived_write_needs_its_parent_audit_row_in_the_same_request():
    """sprs_snapshot beside a mark-met row is accounted for; written alone,
    or beside an unrelated audit row, it is not."""
    snap = Write("INSERT", "sprs_snapshot")
    beside = RequestWrites("PATCH", "/cs", committed=[snap], audited={"control_state"})
    alone = RequestWrites("PATCH", "/cs", committed=[snap])
    unrelated = RequestWrites("PATCH", "/cs", committed=[snap], audited={"contact"})
    assert beside.unaccounted() == {}
    assert alone.unaccounted() == {"sprs_snapshot": ["INSERT"]}
    assert unrelated.unaccounted() == {"sprs_snapshot": ["INSERT"]}


def test_a_broad_parent_accounts_only_through_the_actions_measured_making_the_write():
    """An evidence row beside a list link's `control_state` row is accounted
    for; beside a mark-met row, which is a `control_state` row too, it is
    not. POST /assessments once passed on a row that named the right entity
    and described something else."""
    ev = Write("INSERT", "evidence")
    link = Write("INSERT", "evidence_state_link")
    listed = RequestWrites("POST", "/lists", committed=[ev, link], audited={"control_state"},
                           actions={"evidence.list_link"})
    marked = RequestWrites("PATCH", "/cs", committed=[ev, link], audited={"control_state"},
                           actions={"control_state.update"})
    assert listed.unaccounted() == {}
    assert marked.unaccounted() == {"evidence": ["INSERT"], "evidence_state_link": ["INSERT"]}


def test_the_broad_parent_rules_pin_their_actions():
    """The two rules whose parents include `control_state` keep an action
    list: removing it reopens the gap the test above closes."""
    pinned = {(r.table, r.op) for r in audit_check.RULES if r.actions}
    assert {("evidence", "INSERT"), ("evidence_state_link", None)} <= pinned


def test_it_reads_the_action_an_audit_row_names():
    cur = _observe(
        ("INSERT INTO audit_log (entity_type, action) VALUES (%(entity_type)s, %(action)s)",
         {"entity_type": "control_state", "action": "evidence.list_link"}),
    )
    assert cur.actions == {"evidence.list_link"}


def test_every_rule_says_why():
    """An exemption list is only as strong as the friction of adding to it."""
    empty = [(r.table, r.op) for r in audit_check.RULES if not r.reason.strip()]
    assert not empty, f"rules with no reason: {empty}"


def test_every_rule_names_a_real_table_and_column():
    from app.models import Base
    tables = Base.metadata.tables
    for r in audit_check.RULES:
        assert r.table in tables, f"not a table: {r.table}"
        missing = (r.columns or frozenset()) - {c.name for c in tables[r.table].columns}
        assert not missing, f"{r.table} has no column {sorted(missing)}"
        assert r.op in (None, "INSERT", "UPDATE", "DELETE"), r


def test_a_call_to_a_definer_that_writes_counts_as_its_write():
    cur = _observe(("SELECT auth.grant_org_membership(%(u)s, %(o)s, %(r)s)", {}))
    assert cur.committed == [Write("INSERT", "org_membership")]


def test_every_auth_function_the_app_calls_is_classified():
    """A SECURITY DEFINER function called by SELECT writes where no statement
    shows it. Each one the app calls is a writer, naming what it writes, or a
    reader; a new one is neither until someone reads its body."""
    import pathlib
    import re
    app = pathlib.Path(__file__).resolve().parents[1] / "app"
    sql_call = re.compile(
        r"\b(?:SELECT|FROM)\s+(?:[\w\s,]*?\bFROM\s+)?auth\.(\w+)\s*\(", re.IGNORECASE
    )
    called = set()
    for f in app.rglob("*.py"):
        if "migrations" in f.parts:
            continue
        called |= set(sql_call.findall(f.read_text(encoding="utf-8")))
    assert "grant_org_membership" in called, "the scan must find a known call or it proves nothing"
    unclassified = called - set(audit_check.DEFINER_WRITES) - audit_check.DEFINER_READS
    assert not unclassified, (
        f"classify as a writer or a reader in tests/audit_check.py: {sorted(unclassified)}"
    )


def test_the_companion_names_a_route_no_test_drives_to_a_write(monkeypatch):
    monkeypatch.setattr(audit_check, "routes_writing", {("POST", "/a")})
    monkeypatch.setattr(audit_check, "NOT_REACHING_A_WRITE", {("POST", "/dry"): "writes nothing"})
    assert audit_check.coverage_problems({("POST", "/a"), ("POST", "/dry")}) == []
    assert audit_check.coverage_problems({("POST", "/a"), ("POST", "/dry"), ("PATCH", "/b")}) == [
        "no test reaches a write: PATCH /b"]


def test_the_companion_fails_a_listed_route_that_now_reaches_a_write(monkeypatch):
    monkeypatch.setattr(audit_check, "routes_writing", {("POST", "/dry")})
    monkeypatch.setattr(audit_check, "NOT_REACHING_A_WRITE", {("POST", "/dry"): "writes nothing"})
    assert audit_check.coverage_problems({("POST", "/dry")}) == [
        "listed as not reaching a write, but a test now does: POST /dry"]


def test_every_route_not_reaching_a_write_says_why():
    empty = [k for k, why in audit_check.NOT_REACHING_A_WRITE.items() if not why.strip()]
    assert not empty, f"listed with no reason: {empty}"
