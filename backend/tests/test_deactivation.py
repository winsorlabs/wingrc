"""Integration tests: product deactivation + audit log.

Verifies:
  - OrgProduct → decommissioned with deactivated_at set
  - Auto-flipped (pending_evidence) control states → needs_review
  - Human-touched states (any other status) → sourced cleared, status preserved
  - Open evidence tasks → archived + closed (na)
  - Collected evidence tasks → archived, status preserved
  - Evidence-state links on auto-flipped states → archived with product pointer
  - Evidence-state links on human-touched states → untouched
  - SPRS drops after deactivation (needs_review ≠ met)
  - Deactivation writes audit entries: actor, action, before/after, context
  - Audit entries carry via="product_deactivation" context
  - PATCH /evidence-tasks rejects archived tasks (422)
  - Audit log rows are insert-only (no UPDATE/DELETE paths in audit.py)

Run in-container:
    docker compose exec backend pytest tests/test_deactivation.py -m integration -v
"""
from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.audit as audit_module
from app.db import get_session
from app.engine import activate_org_product, deactivate_org_product, start_assessment
from app.main import app
from app.models import (
    Assessment,
    AssessmentObjective,
    AuditLog,
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    ControlState,
    ControlStateHistory,
    Evidence,
    EvidenceStateLink,
    EvidenceTask,
    Framework,
    Organization,
    OrgProduct,
    Product,
)

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ref(db_session: Session) -> dict:
    """Minimal reference data: two controls, one product, one evidence spec.

    AC.L2-3.1.1 — shared (auto-flipped by magic loop)
    IA.L2-3.5.1 — customer_owns (never auto-flipped)
    """
    org = Organization(name=f"DeactivOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-deact-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ac = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC",
        title="Access control", requirement_text="Limit access", sprs_weight=5, sequence_order=1,
    )
    ia = Control(
        framework_id=fw.id, control_id="IA.L2-3.5.1", family="IA",
        title="Identify users", requirement_text="Identify users", sprs_weight=3, sequence_order=2,
    )
    db_session.add_all([ac, ia])
    db_session.flush()

    ac_obj = AssessmentObjective(control_id=ac.id, objective_key="a", text="AC[a]")
    ia_obj = AssessmentObjective(control_id=ia.id, objective_key="a", text="IA[a]")
    db_session.add_all([ac_obj, ia_obj])
    db_session.flush()

    product = Product(
        framework_id=fw.id, key=f"prod-{uuid.uuid4().hex[:6]}", name="Test Tool",
        provider="Test Inc", category="EDR", asset_type="SPA", role="Test product",
    )
    db_session.add(product)
    db_session.flush()

    bc_ac = BaselineControl(
        product_id=product.id, control_id=ac.id,
        objectives=["a"], classification="shared",
        candidate_state="pending_evidence",
    )
    bc_ia = BaselineControl(
        product_id=product.id, control_id=ia.id,
        objectives=["a"], classification="customer_owns",
        candidate_state="not_satisfied_by_product",
    )
    db_session.add_all([bc_ac, bc_ia])
    db_session.flush()

    spec = BaselineEvidenceSpec(
        baseline_control_id=bc_ac.id,
        artifact_description="User role export",
        evidence_type="export",
        kb_reference="How to export roles",
    )
    db_session.add(spec)
    db_session.flush()

    op = OrgProduct(org_id=org.id, product_id=product.id, status="candidate")
    db_session.add(op)
    db_session.flush()

    return {
        "org": org, "fw": fw, "ac": ac, "ia": ia,
        "ac_obj": ac_obj, "ia_obj": ia_obj,
        "product": product, "bc_ac": bc_ac, "spec": spec, "org_product": op,
    }


def _setup(db_session: Session, ref: dict) -> tuple[Assessment, dict]:
    """Start an assessment, activate the product, return assessment + activate result."""
    a = start_assessment(db_session, ref["org"].id, ref["fw"].id, "Deact Test")
    result = activate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)
    db_session.flush()
    return a, result


@pytest.fixture
def client(db_session: Session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. OrgProduct decommissioned
# ---------------------------------------------------------------------------


def test_deactivate_sets_org_product_decommissioned(db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    op = db_session.scalars(
        select(OrgProduct).where(
            OrgProduct.org_id == ref["org"].id,
            OrgProduct.product_id == ref["product"].id,
        )
    ).first()
    assert op is not None
    assert op.status == "decommissioned"
    assert op.deactivated_at is not None


# ---------------------------------------------------------------------------
# 2. Auto-flipped control states → needs_review
# ---------------------------------------------------------------------------


def test_deactivate_flips_auto_controls_to_needs_review(db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    ac_state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == a.id,
            ControlState.objective_id == ref["ac_obj"].id,
        )
    ).first()
    assert ac_state is not None
    assert ac_state.status == "needs_review"
    assert ac_state.sourced_from_product_id is None


def test_deactivate_writes_history_for_auto_controls(db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    ac_state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == a.id,
            ControlState.objective_id == ref["ac_obj"].id,
        )
    ).first()
    history = db_session.scalars(
        select(ControlStateHistory).where(
            ControlStateHistory.control_state_id == ac_state.id,
            ControlStateHistory.new_status == "needs_review",
        )
    ).all()
    assert len(history) == 1
    assert history[0].previous_status == "pending_evidence"
    assert "Test Tool" in history[0].change_reason


def test_deactivate_does_not_touch_customer_owns(db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    ia_state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == a.id,
            ControlState.objective_id == ref["ia_obj"].id,
        )
    ).first()
    assert ia_state is not None
    assert ia_state.status == "not_met"


# ---------------------------------------------------------------------------
# 3. Human-touched states: sourced cleared, status preserved
# ---------------------------------------------------------------------------


def test_deactivate_preserves_manual_status(db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)

    # Simulate a human marking the auto-flipped state as met
    ac_state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == a.id,
            ControlState.objective_id == ref["ac_obj"].id,
        )
    ).first()
    assert ac_state is not None
    ac_state.status = "met"
    db_session.flush()

    result = deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    db_session.refresh(ac_state)
    assert ac_state.status == "met"
    assert ac_state.sourced_from_product_id is None
    assert result["controls_flagged"] == 0


# ---------------------------------------------------------------------------
# 4. Evidence tasks archived
# ---------------------------------------------------------------------------


def test_deactivate_archives_open_tasks(db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    tasks = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == a.id)
    ).all()
    assert len(tasks) == 1
    t = tasks[0]
    assert t.is_archived is True
    assert t.archived_at is not None
    assert t.status == "na"


def test_deactivate_archives_collected_tasks_preserving_status(
    db_session: Session, ref: dict
):
    a, _ = _setup(db_session, ref)

    task = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == a.id)
    ).first()
    assert task is not None
    task.status = "collected"
    db_session.flush()

    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    db_session.refresh(task)
    assert task.is_archived is True
    assert task.status == "collected"


# ---------------------------------------------------------------------------
# 5. Evidence-state links archived on auto-flipped states
# ---------------------------------------------------------------------------


def test_deactivate_archives_evidence_links_on_auto_states(
    db_session: Session, ref: dict
):
    a, _ = _setup(db_session, ref)

    # Attach a fake evidence link to the auto-flipped AC control state
    ac_state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == a.id,
            ControlState.objective_id == ref["ac_obj"].id,
        )
    ).first()
    ev = Evidence(
        org_id=ref["org"].id, title="Test export", artifact_type="export",
        kind="reference", reference_location="http://example.com/export",
        collected_at=datetime.now(UTC),
    )
    db_session.add(ev)
    db_session.flush()
    lnk = EvidenceStateLink(evidence_id=ev.id, control_state_id=ac_state.id)
    db_session.add(lnk)
    db_session.flush()

    result = deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    db_session.refresh(lnk)
    assert lnk.is_archived is True
    assert lnk.archived_at is not None
    assert lnk.archived_by_product == ref["product"].id
    assert result["evidence_links_archived"] == 1


def test_deactivate_preserves_evidence_links_on_manual_states(
    db_session: Session, ref: dict
):
    a, _ = _setup(db_session, ref)

    # Manually mark the state as met, then attach evidence
    ac_state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == a.id,
            ControlState.objective_id == ref["ac_obj"].id,
        )
    ).first()
    ac_state.status = "met"
    db_session.flush()

    ev = Evidence(
        org_id=ref["org"].id, title="Met evidence", artifact_type="export",
        kind="reference", reference_location="http://example.com/ev",
        collected_at=datetime.now(UTC),
    )
    db_session.add(ev)
    db_session.flush()
    lnk = EvidenceStateLink(evidence_id=ev.id, control_state_id=ac_state.id)
    db_session.add(lnk)
    db_session.flush()

    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    db_session.refresh(lnk)
    assert lnk.is_archived is False


# ---------------------------------------------------------------------------
# 6. SPRS recomputed after deactivation
# ---------------------------------------------------------------------------


def test_deactivate_recomputes_sprs(db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    score_before = db_session.get(Assessment, a.id).sprs_score

    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    score_after = db_session.get(Assessment, a.id).sprs_score
    # needs_review does not satisfy; score should be ≤ score before (or equal if
    # it was already deducted from pending_evidence, which also doesn't satisfy).
    # Both pending_evidence and needs_review deduct the same controls, so the
    # score may be the same numerically — but deactivation must not raise it.
    assert score_after is not None
    assert score_after <= (score_before or 110)


# ---------------------------------------------------------------------------
# 7. Audit entries produced by deactivation
# ---------------------------------------------------------------------------


def test_deactivate_produces_audit_entries(db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    entries = db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == ref["org"].id)
    ).all()
    assert len(entries) > 0

    actions = {e.action for e in entries}
    assert "org_product.deactivate" in actions
    assert "control_state.update" in actions
    assert "evidence_task.archive" in actions


def test_audit_entries_carry_deactivation_context(db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    deactivation_entries = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == ref["org"].id,
            AuditLog.action.in_(["control_state.update", "evidence_task.archive"]),
        )
    ).all()
    assert len(deactivation_entries) > 0

    for entry in deactivation_entries:
        assert entry.context is not None
        assert entry.context.get("via") == "product_deactivation", (
            f"Entry {entry.action} missing via=product_deactivation in context"
        )
        assert entry.context.get("product_name") == "Test Tool"
        assert entry.actor == "system"
        assert entry.actor_type == "system"
        assert entry.created_at is not None


def test_audit_entries_have_before_and_after_values(db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    deactivate_org_product(db_session, ref["org"].id, ref["product"].id, a.id)

    cs_update = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == ref["org"].id,
            AuditLog.action == "control_state.update",
        )
    ).first()
    assert cs_update is not None
    assert cs_update.before_value is not None
    assert cs_update.before_value.get("status") == "pending_evidence"
    assert cs_update.after_value is not None
    assert cs_update.after_value.get("status") == "needs_review"


# ---------------------------------------------------------------------------
# 8. Audit log append-only: no UPDATE/DELETE paths in audit.py
# ---------------------------------------------------------------------------


def test_audit_service_has_no_mutating_paths():
    """audit.py must not contain session.update() or session.delete() paths.

    This is an in-process source inspection check. DB-level hardening
    (REVOKE UPDATE, DELETE ON audit_log FROM <app_role>) is documented
    in migration 0010 and is a pending production deployment step.
    """
    source = inspect.getsource(audit_module)
    assert "session.execute(update" not in source, "audit.py must not call session.execute(update)"
    assert "session.execute(delete" not in source, "audit.py must not call session.execute(delete)"
    assert ".update(" not in source, "audit.py must not call .update() on ORM objects"
    assert ".delete(" not in source, "audit.py must not call .delete() on ORM objects"


def test_audit_log_creates_distinct_row_per_event(db_session: Session, ref: dict):
    """Calling log_event twice creates two rows — never upserts into one."""
    from app.audit import log_event

    org = ref["org"]
    product = ref["product"]

    e1 = log_event(
        db_session,
        org_id=org.id,
        action="test.event",
        entity_type="product",
        entity_id=product.id,
        context={"seq": 1},
    )
    e2 = log_event(
        db_session,
        org_id=org.id,
        action="test.event",
        entity_type="product",
        entity_id=product.id,
        context={"seq": 2},
    )
    db_session.flush()

    assert e1.id != e2.id
    rows = db_session.scalars(
        select(AuditLog).where(
            AuditLog.org_id == org.id,
            AuditLog.action == "test.event",
        )
    ).all()
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# 9. PATCH /evidence-tasks rejects archived tasks
# ---------------------------------------------------------------------------


def test_patch_archived_task_returns_422(client, db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    db_session.flush()

    task = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == a.id)
    ).first()
    assert task is not None

    # Archive it
    task.is_archived = True
    db_session.flush()

    r = client.patch(
        f"/orgs/{ref['org'].id}/assessments/{a.id}/evidence-tasks/{task.id}",
        json={"status": "collected"},
    )
    assert r.status_code == 422


def test_patch_active_task_updates_status(client, db_session: Session, ref: dict):
    a, _ = _setup(db_session, ref)
    db_session.flush()

    task = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == a.id)
    ).first()
    assert task is not None
    assert task.status == "open"

    r = client.patch(
        f"/orgs/{ref['org'].id}/assessments/{a.id}/evidence-tasks/{task.id}",
        json={"status": "collected"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "collected"
    assert r.json()["is_archived"] is False
