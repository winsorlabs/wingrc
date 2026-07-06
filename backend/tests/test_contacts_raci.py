"""Integration tests for Contact and RaciAssignment models (migration 0004).

Run in-container:
    docker compose exec backend pytest tests/test_contacts_raci.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Assessment,
    AssessmentObjective,
    Contact,
    Control,
    ControlState,
    Framework,
    Organization,
    RaciAssignment,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _org(db_session) -> Organization:
    org = Organization(name=f"Org-{uuid.uuid4().hex}")
    db_session.add(org)
    db_session.flush()
    return org


def _contact(db_session, org: Organization, **kwargs) -> Contact:
    c = Contact(
        org_id=org.id,
        name=kwargs.get("name", "Alice Smith"),
        email=kwargs.get("email", f"{uuid.uuid4().hex}@example.com"),
        affiliation=kwargs.get("affiliation", "msp"),
    )
    db_session.add(c)
    db_session.flush()
    return c


def _control_state(db_session, org: Organization) -> ControlState:
    """Seed the minimal chain needed for a ControlState row."""
    fw = Framework(key=f"fw-{uuid.uuid4().hex}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id=f"AC.L2-{uuid.uuid4().hex[:6]}",
        family="AC",
        title="Test control",
        requirement_text="Test",
        sprs_weight=1,
        sequence_order=0,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(
        control_id=ctrl.id,
        objective_key="a",
        text="Test objective",
    )
    db_session.add(obj)
    db_session.flush()

    assessment = Assessment(
        org_id=org.id,
        framework_id=fw.id,
        name="Test Assessment",
    )
    db_session.add(assessment)
    db_session.flush()

    cs = ControlState(
        assessment_id=assessment.id,
        org_id=org.id,
        objective_id=obj.id,
    )
    db_session.add(cs)
    db_session.flush()
    return cs


# ---------------------------------------------------------------------------
# Contact tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_create_contact_minimal(db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    assert c.id is not None
    assert c.created_at is not None


@pytest.mark.integration
def test_create_contact_all_fields(db_session):
    org = _org(db_session)
    c = Contact(
        org_id=org.id,
        name="Bob Jones",
        email="bob@example.com",
        phone="555-1234",
        affiliation="customer",
        role_title="IT Director",
        contract_ref="MSA-2026-001",
    )
    db_session.add(c)
    db_session.flush()
    assert c.id is not None
    assert c.phone == "555-1234"
    assert c.contract_ref == "MSA-2026-001"


@pytest.mark.integration
def test_contact_email_unique_per_org(db_session):
    org = _org(db_session)
    email = "dupe@example.com"
    db_session.add(Contact(org_id=org.id, name="A", email=email, affiliation="msp"))
    db_session.flush()
    db_session.add(Contact(org_id=org.id, name="B", email=email, affiliation="customer"))
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.integration
def test_contact_same_email_ok_across_orgs(db_session):
    org1 = _org(db_session)
    org2 = _org(db_session)
    email = f"shared-{uuid.uuid4().hex}@example.com"
    db_session.add(Contact(org_id=org1.id, name="A", email=email, affiliation="msp"))
    db_session.add(Contact(org_id=org2.id, name="B", email=email, affiliation="msp"))
    db_session.flush()  # no error


@pytest.mark.integration
def test_contact_invalid_affiliation(db_session):
    org = _org(db_session)
    db_session.add(
        Contact(org_id=org.id, name="X", email="x@x.com", affiliation="vendor")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


# ---------------------------------------------------------------------------
# RaciAssignment tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_raci_valid_letters(db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    cs = _control_state(db_session, org)
    for letter in ("A", "R", "C", "I"):
        ra = RaciAssignment(
            control_state_id=cs.id, contact_id=c.id, raci_letter=letter
        )
        db_session.add(ra)
    db_session.flush()  # all four letters accepted


@pytest.mark.integration
def test_raci_invalid_letter(db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    cs = _control_state(db_session, org)
    db_session.add(
        RaciAssignment(control_state_id=cs.id, contact_id=c.id, raci_letter="X")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.integration
def test_raci_duplicate_assignment_rejected(db_session):
    org = _org(db_session)
    c = _contact(db_session, org)
    cs = _control_state(db_session, org)
    db_session.add(
        RaciAssignment(control_state_id=cs.id, contact_id=c.id, raci_letter="R")
    )
    db_session.flush()
    db_session.add(
        RaciAssignment(control_state_id=cs.id, contact_id=c.id, raci_letter="R")
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


@pytest.mark.integration
def test_raci_same_contact_r_and_a_allowed(db_session):
    """A contact may hold both R and A on the same objective (valid RACI)."""
    org = _org(db_session)
    c = _contact(db_session, org)
    cs = _control_state(db_session, org)
    db_session.add(
        RaciAssignment(control_state_id=cs.id, contact_id=c.id, raci_letter="R")
    )
    db_session.add(
        RaciAssignment(control_state_id=cs.id, contact_id=c.id, raci_letter="A")
    )
    db_session.flush()  # two different letters on same (cs, contact) — allowed
