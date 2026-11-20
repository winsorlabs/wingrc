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

# Written out: the policies that read a parent table rather than the org,
# and are gated by the parent's policy. Every other policy on an RLS table
# must read the org through public.rls_current_org().
TRANSITIVE = {
    "contact_documentation_role",
    "control_state_contributor",
    "control_state_history",
    "evidence_state_link",
    "evidence_task_state_link",
    "raci_assignment",
    "review_cycle_reminder_log",
}


@pytest.mark.integration
def test_every_org_policy_reads_the_org_through_the_raising_function(db_session):
    rows = db_session.execute(text("""
        SELECT c.relname,
               EXISTS (SELECT 1 FROM pg_depend d
                       WHERE d.classid = 'pg_policy'::regclass AND d.objid = p.oid
                         AND d.refobjid = 'public.rls_current_org()'::regprocedure)
        FROM pg_policy p
        JOIN pg_class c ON c.oid = p.polrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relrowsecurity
    """)).all()
    assert len(rows) >= 50, "fixture must see every policy, or this test proves nothing"
    not_through_it = {table for table, through in rows if not through}
    assert not_through_it == TRANSITIVE


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
