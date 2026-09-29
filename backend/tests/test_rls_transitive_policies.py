"""Tenant isolation for the six transitively-scoped child tables
(migration 0061).

These carry no `org_id`; each is gated by an `EXISTS` over a parent that is
itself gated, so what is under test is that the *composition* works --
PostgreSQL applying the parent's policy to the reference inside the child's
policy expression.

**Both directions, per table.** A policy that hides other orgs' rows from
`SELECT` but happily lets you `INSERT` one is an easy and real mistake:
`FOR ALL` with no explicit `WITH CHECK` reuses `USING` as the check, which
for an EXISTS predicate means an INSERT is validated against a condition
written for a read. Migration 0061 spells both out; these assert both.

Everything here runs under an explicit `SET ROLE wingrc_app`. The app still
connects as the owner, which bypasses RLS unconditionally, so none of this
is enforced in production yet -- these test the state the cutover produces.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import ProgrammingError

from app.models import (
    Assessment,
    AssessmentObjective,
    Contact,
    ContactDocumentationRole,
    Control,
    ControlState,
    ControlStateHistory,
    Evidence,
    EvidenceStateLink,
    EvidenceTask,
    EvidenceTaskStateLink,
    Framework,
    Organization,
)
from app.rls import set_current_org

pytestmark = pytest.mark.integration

_APP_ROLE = "wingrc_app"


@pytest.fixture
def two_orgs(db_session):
    """Two orgs, each with one row in every table under test, seeded as the
    owner (RLS bypassed) so the body can read them back as `wingrc_app`.
    """
    fw = Framework(key=f"fw-tr-{uuid.uuid4().hex[:6]}", name="Transitive FW", version="r2")
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

    made: dict[str, dict] = {}
    for label in ("a", "b"):
        org = Organization(name=f"TransOrg-{label}-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
        db_session.flush()

        contact = Contact(
            org_id=org.id, name=f"Person {label}", email=f"{label}@example.com",
            affiliation="customer",
        )
        assessment = Assessment(
            org_id=org.id, framework_id=fw.id, name=f"Assessment {label}", status="in_progress"
        )
        db_session.add_all([contact, assessment])
        db_session.flush()

        cs = ControlState(
            assessment_id=assessment.id, org_id=org.id, objective_id=obj.id, status="not_met"
        )
        evidence = Evidence(
            org_id=org.id, kind="reference", title=f"Evidence {label}",
            artifact_type="document", reference_location=f"DOC-{label}",
        )
        task = EvidenceTask(
            org_id=org.id, assessment_id=assessment.id, title=f"Task {label}",
            artifact_type="document", status="open",
        )
        db_session.add_all([cs, evidence, task])
        db_session.flush()

        doc_role = ContactDocumentationRole(contact_id=contact.id, role="it_admin")
        history = ControlStateHistory(
            control_state_id=cs.id, previous_status="not_met", new_status="pending_evidence",
            change_reason=f"seed {label}",
        )
        ev_link = EvidenceStateLink(evidence_id=evidence.id, control_state_id=cs.id)
        task_link = EvidenceTaskStateLink(task_id=task.id, control_state_id=cs.id)
        db_session.add_all([doc_role, history, ev_link, task_link])
        db_session.flush()

        made[label] = {
            "org": org, "contact": contact, "control_state": cs, "evidence": evidence,
            "task": task, "doc_role": doc_role, "history": history,
            "ev_link": ev_link, "task_link": task_link,
        }

    db_session.commit()
    return made


def _as_app_role(db_session, org_id):
    db_session.connection().execute(text(f"SET ROLE {_APP_ROLE}"))
    set_current_org(db_session, org_id)


def _reset_role(db_session):
    db_session.connection().execute(text("RESET ROLE"))


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model,key",
    [
        (ContactDocumentationRole, "doc_role"),
        (ControlStateHistory, "history"),
        (EvidenceStateLink, "ev_link"),
        (EvidenceTaskStateLink, "task_link"),
    ],
)
def test_child_table_reads_are_isolated(two_orgs, db_session, model, key):
    a, b = two_orgs["a"], two_orgs["b"]
    _as_app_role(db_session, a["org"].id)
    try:
        ids = {row.id for row in db_session.scalars(select(model)).all()}
        assert a[key].id in ids, f"{model.__name__} in the current org must be visible"
        assert b[key].id not in ids, (
            f"{model.__name__} leaked across orgs -- the EXISTS policy is not "
            "composing with its parent's policy"
        )
    finally:
        _reset_role(db_session)


# ---------------------------------------------------------------------------
# Writes -- WITH CHECK, which USING alone would not give us
# ---------------------------------------------------------------------------


def test_cannot_insert_a_history_row_for_another_orgs_control_state(two_orgs, db_session):
    a, b = two_orgs["a"], two_orgs["b"]
    _as_app_role(db_session, a["org"].id)
    try:
        with pytest.raises(ProgrammingError):
            db_session.execute(
                text(
                    "INSERT INTO control_state_history "
                    "(id, control_state_id, previous_status, new_status, changed_at) "
                    "VALUES (gen_random_uuid(), :cs, 'not_met', 'met', now())"
                ),
                {"cs": str(b["control_state"].id)},
            )
    finally:
        db_session.rollback()
        _reset_role(db_session)


def test_cannot_link_evidence_to_another_orgs_control_state(two_orgs, db_session):
    """The cross-tenant write that would matter most: attaching evidence to
    a control state in an org you cannot see."""
    a, b = two_orgs["a"], two_orgs["b"]
    _as_app_role(db_session, a["org"].id)
    try:
        with pytest.raises(ProgrammingError):
            db_session.execute(
                text(
                    "INSERT INTO evidence_state_link "
                    "(id, evidence_id, control_state_id, is_archived) "
                    "VALUES (gen_random_uuid(), :ev, :cs, false)"
                ),
                {"ev": str(a["evidence"].id), "cs": str(b["control_state"].id)},
            )
    finally:
        db_session.rollback()
        _reset_role(db_session)


def test_cannot_add_a_documentation_role_to_another_orgs_contact(two_orgs, db_session):
    a, b = two_orgs["a"], two_orgs["b"]
    _as_app_role(db_session, a["org"].id)
    try:
        with pytest.raises(ProgrammingError):
            db_session.execute(
                text(
                    "INSERT INTO contact_documentation_role (id, contact_id, role) "
                    "VALUES (gen_random_uuid(), :c, 'security_officer')"
                ),
                {"c": str(b["contact"].id)},
            )
    finally:
        db_session.rollback()
        _reset_role(db_session)


def test_cannot_link_a_task_to_another_orgs_control_state(two_orgs, db_session):
    a, b = two_orgs["a"], two_orgs["b"]
    _as_app_role(db_session, a["org"].id)
    try:
        with pytest.raises(ProgrammingError):
            db_session.execute(
                text(
                    "INSERT INTO evidence_task_state_link (id, task_id, control_state_id) "
                    "VALUES (gen_random_uuid(), :t, :cs)"
                ),
                {"t": str(a["task"].id), "cs": str(b["control_state"].id)},
            )
    finally:
        db_session.rollback()
        _reset_role(db_session)


def test_writes_into_the_current_org_still_succeed(two_orgs, db_session):
    """The other half of every check above: the policies must not be so
    tight that ordinary in-org work is refused. A policy that blocks
    everything also passes an isolation test.
    """
    a = two_orgs["a"]
    _as_app_role(db_session, a["org"].id)
    try:
        db_session.execute(
            text(
                "INSERT INTO control_state_history "
                "(id, control_state_id, previous_status, new_status, changed_at) "
                "VALUES (gen_random_uuid(), :cs, 'not_met', 'met', now())"
            ),
            {"cs": str(a["control_state"].id)},
        )
        db_session.execute(
            text(
                "INSERT INTO contact_documentation_role (id, contact_id, role) "
                "VALUES (gen_random_uuid(), :c, 'security_officer')"
            ),
            {"c": str(a["contact"].id)},
        )
        rows = db_session.scalars(
            select(ControlStateHistory).where(
                ControlStateHistory.control_state_id == a["control_state"].id
            )
        ).all()
        assert len(rows) == 2
    finally:
        db_session.rollback()
        _reset_role(db_session)


# ---------------------------------------------------------------------------
# Post-commit reads on the newly-gated tables -- the section 0 check
# ---------------------------------------------------------------------------


def test_post_commit_read_on_a_newly_gated_child_table(two_orgs, db_session):
    """Adding a policy to a table changes every post-commit read of it.

    `routers/contacts.py:add_role` does exactly this on
    `contact_documentation_role`. Asserted here directly rather than
    inferred from the hook existing, because these six have never been
    gated before and the hook is new.
    """
    a = two_orgs["a"]
    _as_app_role(db_session, a["org"].id)
    try:
        role_row = db_session.scalars(
            select(ContactDocumentationRole).where(
                ContactDocumentationRole.contact_id == a["contact"].id
            )
        ).one()
        db_session.commit()  # ends the transaction app.current_org was scoped to

        db_session.refresh(role_row)
        assert role_row.role == "it_admin"

        assert db_session.scalars(
            select(EvidenceStateLink).where(
                EvidenceStateLink.control_state_id == a["control_state"].id
            )
        ).all(), "post-commit read on evidence_state_link returned nothing"
    finally:
        _reset_role(db_session)
