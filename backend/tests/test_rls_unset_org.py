"""A read of an RLS table with no org set raises WR001 and says why
(migration 0077).

Before it, such a read matched zero rows, or raised `invalid input syntax
for type uuid: ""` on whatever line ran first, depending on the pooled
connection's history. `/auth/set-password` failed that way on every
invite from the 2026-09-29 cutover to 2026-11-20 (docs/roadmap.md, "No
invite could be redeemed on live").
"""
from __future__ import annotations

import uuid

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from app.models import AuditLog, Contact, Organization

# Written out: the policies exempt from reading the org themselves, each
# with the parent table its policy reads and why that is enough. Every
# other policy on an RLS table must read the org through
# public.rls_current_org().
#
# Adding a table here is a decision, not a way to make the test below pass.
# The test checks the claim, not just the name: the policy must actually
# read the parent named, and the parent's own policy must read the org
# through the function, so an unset org still raises through it. A table
# that has an org_id of its own does not belong here; give its policy the
# function instead. Each reason says why the table has no org of its own.
TRANSITIVE: dict[str, tuple[str, str]] = {
    "contact_documentation_role": (
        "contact",
        "a role a contact holds; no org_id, the contact it names is the org's",
    ),
    "control_state_contributor": (
        "control_state",
        "which product contributed to one control state; no org_id, the state's is",
    ),
    "control_state_history": (
        "control_state",
        "one transition of one control state; no org_id, the state's is",
    ),
    "evidence_state_link": (
        "control_state",
        "links evidence to a control state; scoped by the state it links, which "
        "is the side an assessment reads",
    ),
    "evidence_task_state_link": (
        "control_state",
        "links a task to a control state; scoped by the state it links",
    ),
    "raci_assignment": (
        "control_state",
        "a RACI letter on one control state; no org_id, the state's is",
    ),
    "review_cycle_reminder_log": (
        "review_cycle",
        "one reminder sent for one cycle; no org_id, the cycle's is",
    ),
}


def _policies(db_session):
    """Each policy on an RLS table in public: its table, whether it reads
    the function, and the tables it reads (from pg_depend, not its text)."""
    return db_session.execute(text("""
        SELECT c.relname,
               EXISTS (SELECT 1 FROM pg_depend d
                       WHERE d.classid = 'pg_policy'::regclass AND d.objid = p.oid
                         AND d.refobjid = 'public.rls_current_org()'::regprocedure),
               ARRAY(SELECT DISTINCT r.relname FROM pg_depend d
                     JOIN pg_class r ON r.oid = d.refobjid
                     WHERE d.classid = 'pg_policy'::regclass AND d.objid = p.oid
                       AND d.refclassid = 'pg_class'::regclass AND r.oid <> c.oid)
        FROM pg_policy p
        JOIN pg_class c ON c.oid = p.polrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relrowsecurity
    """)).all()


@pytest.mark.integration
def test_every_org_policy_reads_the_org_through_the_raising_function(db_session):
    rows = _policies(db_session)
    assert len(rows) >= 50, "fixture must see every policy, or this test proves nothing"
    not_through_it = {table for table, through, _ in rows if not through}
    assert not_through_it == set(TRANSITIVE), (
        "a policy that does not read the org through public.rls_current_org() "
        "must be listed in TRANSITIVE with its parent and the reason it has no "
        "org of its own; a table with an org_id gets the function instead"
    )


def test_every_exemption_states_its_parent_and_a_reason():
    for table, (parent, reason) in TRANSITIVE.items():
        assert parent and parent != table, table
        assert len(reason.split()) >= 5, f"{table}: the reason must say why, not name it"


@pytest.mark.integration
def test_each_exemption_reads_its_parent_and_the_parent_reads_the_function(db_session):
    rows = {table: (through, reads) for table, through, reads in _policies(db_session)}
    for table, (parent, _reason) in TRANSITIVE.items():
        _, reads = rows[table]
        assert parent in reads, f"{table}'s policy does not read {parent}; it reads {reads}"
        assert rows[parent][0], (
            f"{table} is exempt because {parent} gates it, but {parent}'s policy does "
            "not read the org through public.rls_current_org()"
        )


@pytest.mark.integration
def test_an_exempt_table_has_no_org_column_of_its_own(db_session):
    """An org_id column means the table can carry the function itself."""
    with_org = set(db_session.execute(text("""
        SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name IN ('org_id', 'home_org_id')
    """)).scalars())
    assert not (with_org & set(TRANSITIVE)), with_org & set(TRANSITIVE)


def _as_app_with_no_org(db_session):
    db_session.flush()
    db_session.execute(text("SET LOCAL ROLE wingrc_app"))
    db_session.execute(text("SELECT set_config('app.current_org', '', true)"))


@pytest.fixture
def contact_row(db_session):
    org = Organization(name=f"Unset-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    contact = Contact(org_id=org.id, name="Somebody", email="somebody@example.com",
                      affiliation="customer")
    db_session.add(contact)
    db_session.flush()
    return contact


@pytest.mark.integration
def test_a_read_with_no_org_raises_wr001_naming_the_cause(db_session, contact_row):
    nested = db_session.begin_nested()
    _as_app_with_no_org(db_session)
    with pytest.raises(DBAPIError) as caught:
        db_session.execute(text("SELECT id FROM contact WHERE id = :id"), {"id": contact_row.id})
    nested.rollback()
    assert caught.value.orig.sqlstate == "WR001"
    assert "app.current_org is not set" in str(caught.value.orig)


@pytest.mark.integration
def test_a_read_with_the_org_set_still_returns_the_row(db_session, contact_row):
    nested = db_session.begin_nested()
    db_session.execute(text("SET LOCAL ROLE wingrc_app"))
    db_session.execute(text("SELECT set_config('app.current_org', :o, true)"),
                       {"o": str(contact_row.org_id)})
    found = db_session.execute(
        text("SELECT id FROM contact WHERE id = :id"), {"id": contact_row.id}
    ).scalar_one_or_none()
    nested.rollback()
    assert found == contact_row.id


@pytest.mark.integration
def test_an_audit_row_with_no_org_is_still_readable_with_no_org_set(db_session):
    """audit_log's policy lets every request see null-org rows; the
    function is reached only for a row with an org."""
    row = AuditLog(org_id=None, action="test.null_org", entity_type="test", entity_id=uuid.uuid4())
    db_session.add(row)
    nested = db_session.begin_nested()
    _as_app_with_no_org(db_session)
    found = db_session.execute(
        text("SELECT id FROM audit_log WHERE id = :id"), {"id": row.id}
    ).scalar_one_or_none()
    nested.rollback()
    assert found == row.id


@pytest.mark.integration
def test_a_connection_that_never_set_the_org_raises_too(db_engine):
    """NULL, not the empty string: the form a fresh pooled connection has."""
    fresh = create_engine(db_engine.url, poolclass=NullPool)
    with fresh.connect() as conn:
        assert conn.execute(
            text("SELECT current_setting('app.current_org', true)")
        ).scalar() is None, "fixture must be a connection that never set the org"
        with pytest.raises(DBAPIError) as caught:
            conn.execute(text("SELECT public.rls_current_org()"))
    assert isinstance(caught.value.orig, psycopg.Error)
    assert caught.value.orig.sqlstate == "WR001"
    fresh.dispose()
