"""Tests for app/cli.py::_reset_dev() -- the dev-DB cleanup utility.

Regression coverage for the FK-ordering gaps found while reconciling this
function against the current schema (see docs/roadmap.md's now-closed
Known defects entry): `_reset_dev()` was written before the audit/diagram/
evidence-task-linking tables existed and never deleted (or reordered
around) them, so it raised a foreign-key violation the moment a realistic
org -- one with audit history, a network diagram, or a finding with a
POA&M item -- got swept up in a reset.

Run in-container:
    docker compose exec backend pytest tests/test_cli_reset_dev.py -m integration -v
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import _reset_dev
from app.engine import start_assessment
from app.models import (
    AssessmentObjective,
    AuditLog,
    Control,
    ControlState,
    Evidence,
    EvidenceTask,
    EvidenceTaskStateLink,
    Finding,
    Framework,
    Organization,
    PoamItem,
    SystemDescription,
)

pytestmark = pytest.mark.integration


def _seed_full_graph_org(db_session: Session) -> Organization:
    """Build a throwaway org exercising every FK path `_reset_dev()` has to
    walk safely: an audit_log row, a system_description diagram pointer
    into evidence, an evidence_task_state_link row, and a poa_m_item that
    references a finding -- the four gaps this fix closes.
    """
    org = Organization(name=f"ResetDevTestOrg-{uuid.uuid4().hex[:8]}")
    fw = Framework(key=f"test-fw-{uuid.uuid4().hex[:8]}", name="Test FW", version="1")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id="AC.L2-3.1.1",
        family="AC",
        title="Test control",
        requirement_text="Test requirement",
        sprs_weight=1,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="a")
    db_session.add(obj)
    db_session.flush()

    assessment = start_assessment(db_session, org.id, fw.id, "Reset-dev test")
    cs = db_session.scalars(
        select(ControlState).where(ControlState.assessment_id == assessment.id)
    ).first()
    assert cs is not None

    # audit_log — the originally-documented defect.
    db_session.add(
        AuditLog(
            org_id=org.id,
            actor="tester",
            actor_type="user",
            action="scope_entity.create",
            entity_type="scope_entity",
            entity_id=uuid.uuid4(),
        )
    )

    # system_description's diagram pointer into evidence.
    ev = Evidence(
        org_id=org.id,
        title="Network diagram",
        artifact_type="network_diagram",
        collected_at=datetime.now(UTC),
    )
    db_session.add(ev)
    db_session.flush()
    db_session.add(
        SystemDescription(
            org_id=org.id,
            system_name="Test system",
            system_type="minor_application",
            operational_status="operational",
            network_diagram_evidence_id=ev.id,
        )
    )

    # evidence_task_state_link — links a task to the control_state.
    task = EvidenceTask(org_id=org.id, title="Collect export", artifact_type="export")
    db_session.add(task)
    db_session.flush()
    db_session.add(EvidenceTaskStateLink(task_id=task.id, control_state_id=cs.id))

    # poa_m_item -> finding.
    finding = Finding(
        assessment_id=assessment.id,
        org_id=org.id,
        control_state_id=cs.id,
        title="Test finding",
        description="Test description",
        severity="low",
        finding_type="gap",
        status="open",
    )
    db_session.add(finding)
    db_session.flush()
    db_session.add(
        PoamItem(
            org_id=org.id,
            finding_id=finding.id,
            title="Remediate",
            description="Remediate the finding",
        )
    )

    db_session.flush()
    return org


@pytest.mark.integration
def test_reset_dev_succeeds_against_full_realistic_graph(db_session: Session):
    """The exact case that was broken: a test org with audit_log rows (plus
    the diagram/evidence-task-link/POA&M paths found alongside it) must not
    raise a foreign-key violation."""
    org = _seed_full_graph_org(db_session)

    _reset_dev(db_session)  # must not raise

    # _reset_dev() runs raw SQL DELETEs, which don't update the ORM's
    # identity map. session.get() on a PK it already has an (now-stale)
    # instance for raises ObjectDeletedError rather than returning None on
    # a miss -- a plain select() doesn't have that footgun, so use that for
    # every "is this row still there" check below, not .get().
    remaining_org = db_session.scalars(
        select(Organization).where(Organization.id == org.id)
    ).first()
    assert remaining_org is None
    remaining_audit = db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == org.id)
    ).first()
    assert remaining_audit is None


@pytest.mark.integration
def test_reset_dev_preserves_acme_msp(db_session: Session):
    acme = Organization(name="Acme MSP")
    db_session.add(acme)
    db_session.flush()
    acme_id = acme.id

    other = _seed_full_graph_org(db_session)

    _reset_dev(db_session)

    remaining_acme = db_session.scalars(
        select(Organization).where(Organization.id == acme_id)
    ).first()
    assert remaining_acme is not None
    remaining_other = db_session.scalars(
        select(Organization).where(Organization.id == other.id)
    ).first()
    assert remaining_other is None
