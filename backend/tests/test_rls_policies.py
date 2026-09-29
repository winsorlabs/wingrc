"""Tenant isolation for the four tables migration 0060 added policies to.

These assert isolation by *querying across orgs under `wingrc_app`*, not by
inspecting the catalog. A policy that exists but does not isolate is the
failure worth catching, and `\\d` cannot tell you that.

Why these four, and not the rest: enumerating `pg_policy` against every
table carrying an `org_id` found three with no policy at all (`contact`,
`system_description`, `audit_log`) plus `raci_assignment`, which turned out
to carry no `org_id` at all and to be scoped transitively through
`control_state`. See migration 0060's own docstring for the full reasoning,
including why `audit_log`'s policy is shaped differently.

Today none of this changes behaviour: the app connects as the table owner,
which bypasses RLS unconditionally (`FORCE ROW LEVEL SECURITY` is not set
anywhere). These tests run under an explicit `SET ROLE wingrc_app`, which
is what the cutover will make permanent -- so they are testing the state
the cutover produces, deliberately, before it happens.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app.models import (
    Assessment,
    AssessmentObjective,
    AuditLog,
    Contact,
    Control,
    ControlState,
    Framework,
    Organization,
    RaciAssignment,
    SystemDescription,
)
from app.rls import set_current_org

pytestmark = pytest.mark.integration

_APP_ROLE = "wingrc_app"


@pytest.fixture
def two_orgs(db_session):
    """Two orgs, each with a contact, a system description, an audit row and
    a RACI assignment, seeded as the owner role (RLS bypassed) so the test
    body can then read them back under `wingrc_app`.
    """
    fw = Framework(key=f"fw-rls-{uuid.uuid4().hex[:6]}", name="RLS FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC", title="Access Control",
        requirement_text="Limit access.", sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Objective a.")
    db_session.add(obj)
    db_session.flush()

    made = {}
    for label in ("a", "b"):
        org = Organization(name=f"RlsOrg-{label}-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        contact = Contact(
            org_id=org.id, name=f"Person {label}", email=f"{label}@example.com",
            affiliation="customer",
        )
        sysdesc = SystemDescription(
            org_id=org.id, system_name=f"System {label}", system_type="major_application",
            operational_status="operational",
        )
        assessment = Assessment(
            org_id=org.id, framework_id=fw.id, name=f"Assessment {label}", status="in_progress"
        )
        db_session.add_all([contact, sysdesc, assessment])
        db_session.flush()

        cs = ControlState(
            assessment_id=assessment.id, org_id=org.id, objective_id=obj.id, status="not_met"
        )
        db_session.add(cs)
        db_session.flush()

        raci = RaciAssignment(control_state_id=cs.id, contact_id=contact.id, raci_letter="R")
        audit = AuditLog(
            org_id=org.id, actor="system", actor_type="system", action="test.event",
            entity_type="organization", entity_id=org.id,
        )
        db_session.add_all([raci, audit])
        db_session.flush()

        made[label] = {
            "org": org, "contact": contact, "sysdesc": sysdesc,
            "control_state": cs, "raci": raci, "audit": audit,
        }

    # A deployment-wide audit row: org_id is NULL on purpose. Migration
    # 0060's policy must keep these writable and readable, or admin product
    # management and credential rotation break at the cutover.
    db_session.add(
        AuditLog(
            org_id=None, actor="system", actor_type="system", action="admin.deployment_wide",
            entity_type="product", entity_id=uuid.uuid4(),
        )
    )
    db_session.commit()
    return made


def _as_app_role(db_session, org_id):
    db_session.connection().execute(text(f"SET ROLE {_APP_ROLE}"))
    set_current_org(db_session, org_id)


def _reset_role(db_session):
    db_session.connection().execute(text("RESET ROLE"))


@pytest.mark.parametrize(
    "model,attr",
    [
        (Contact, "contact"),
        (SystemDescription, "sysdesc"),
    ],
)
def test_org_scoped_table_is_isolated_across_orgs(two_orgs, db_session, model, attr):
    a, b = two_orgs["a"], two_orgs["b"]
    _as_app_role(db_session, a["org"].id)
    try:
        visible = db_session.scalars(select(model)).all()
        ids = {row.id for row in visible}
        assert a[attr].id in ids, f"{model.__name__} in the current org must be visible"
        assert b[attr].id not in ids, (
            f"{model.__name__} leaked across orgs under {_APP_ROLE} -- the policy is "
            "missing or does not isolate"
        )
    finally:
        _reset_role(db_session)


def test_raci_assignment_is_isolated_through_its_control_state(two_orgs, db_session):
    """raci_assignment has no org_id; its policy is an EXISTS over
    control_state, which is itself gated. This proves the composition
    works, which a column-comparison policy would not have needed.
    """
    a, b = two_orgs["a"], two_orgs["b"]
    _as_app_role(db_session, a["org"].id)
    try:
        ids = {row.id for row in db_session.scalars(select(RaciAssignment)).all()}
        assert a["raci"].id in ids
        assert b["raci"].id not in ids, "raci_assignment leaked across orgs"
    finally:
        _reset_role(db_session)


def test_audit_log_is_isolated_but_keeps_deployment_wide_rows(two_orgs, db_session):
    """The asymmetric case. Org-scoped rows isolate; NULL-org rows stay
    visible, because they are deployment-wide facts and because the policy
    that hides them would also refuse to let them be written.
    """
    a, b = two_orgs["a"], two_orgs["b"]
    _as_app_role(db_session, a["org"].id)
    try:
        rows = db_session.scalars(select(AuditLog)).all()
        ids = {r.id for r in rows}
        assert a["audit"].id in ids
        assert b["audit"].id not in ids, "audit_log leaked across orgs"
        assert any(r.org_id is None for r in rows), (
            "deployment-wide audit rows (org_id IS NULL) must remain visible -- a "
            "policy that hides them also rejects writing them"
        )
    finally:
        _reset_role(db_session)


def test_deployment_wide_audit_rows_are_still_writable_under_rls(two_orgs, db_session):
    """The failure this would have caused at cutover, asserted directly.

    A `FOR ALL` policy with no explicit `WITH CHECK` uses its `USING`
    expression as the check. `NULL = <uuid>` is NULL, not true, so the
    usual policy shape would have made every `log_event(org_id=None)` call
    fail the moment the app stopped bypassing RLS -- silently breaking
    admin product management and credential-key rotation.
    """
    a = two_orgs["a"]
    _as_app_role(db_session, a["org"].id)
    try:
        db_session.execute(
            text(
                "INSERT INTO audit_log (id, org_id, actor, actor_type, action, "
                "entity_type, entity_id, created_at) VALUES "
                "(gen_random_uuid(), NULL, 'system', 'system', 'admin.deployment_wide', "
                "'product', gen_random_uuid(), now())"
            )
        )
        count = db_session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.org_id.is_(None))
        )
        assert count >= 2
    finally:
        db_session.rollback()
        _reset_role(db_session)


def test_writing_into_another_org_is_refused(two_orgs, db_session):
    """WITH CHECK, not just USING: the policy must stop a row being created
    for an org the session is not scoped to, not merely hide it afterwards.
    """
    from sqlalchemy.exc import ProgrammingError

    a, b = two_orgs["a"], two_orgs["b"]
    _as_app_role(db_session, a["org"].id)
    try:
        with pytest.raises(ProgrammingError):
            db_session.execute(
                text(
                    "INSERT INTO contact (id, org_id, name, email, affiliation, created_at) "
                    "VALUES (gen_random_uuid(), :org, 'Intruder', 'x@example.com', "
                    "'customer', now())"
                ),
                {"org": str(b["org"].id)},
            )
    finally:
        db_session.rollback()
        _reset_role(db_session)
