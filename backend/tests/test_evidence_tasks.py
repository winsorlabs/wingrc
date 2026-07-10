"""Integration tests for evidence-task generation (magic loop).

Verifies:
  - Tasks are created with status='open' for provider_satisfies/shared controls
  - Tasks link to ALL covered objectives, not just the first
  - customer_owns controls produce no tasks
  - Artifact-key dedup: same (title, type) across controls → one task, multiple links
  - collection_session comes from kb_reference when present
  - Re-activation is idempotent: no duplicate tasks or links
  - Re-activation preserves existing task status (collected tasks stay collected)
  - GET /evidence-tasks returns tasks with linked_states
  - Task creation never changes control_state.status

Run in-container:
    docker compose exec backend pytest tests/test_evidence_tasks.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import get_session
from app.engine import _seed_control_states, activate_org_product
from app.main import app
from app.models import (
    Assessment,
    AssessmentObjective,
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    ControlState,
    EvidenceTask,
    EvidenceTaskStateLink,
    Organization,
    Product,
)
from app.seeds.baselines import seed_baselines
from app.seeds.catalog import seed_catalog

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db_session):
    catalog = seed_catalog(db_session)
    seed_baselines(db_session)
    db_session.flush()
    return catalog


@pytest.fixture
def scenario(db_session, seeded):
    """Org + assessment + rocketcyber product, NOT yet activated."""
    fw_id = seeded["framework_id"]
    product = db_session.scalars(
        select(Product).where(Product.key == "rocketcyber")
    ).first()
    assert product is not None, "rocketcyber baseline not seeded"

    org = Organization(name=f"EvTaskOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()

    assessment = Assessment(org_id=org.id, framework_id=fw_id, name="Ev Task Test")
    db_session.add(assessment)
    db_session.flush()

    _seed_control_states(db_session, org.id, fw_id, assessment.id)
    db_session.flush()

    return {"org": org, "assessment": assessment, "product": product}


def _activate(db_session, scenario):
    return activate_org_product(
        db_session,
        org_id=scenario["org"].id,
        product_id=scenario["product"].id,
        assessment_id=scenario["assessment"].id,
    )


def _task_count(db_session, assessment_id):
    return db_session.scalar(
        select(func.count(EvidenceTask.id)).where(EvidenceTask.assessment_id == assessment_id)
    )


def _link_count(db_session, assessment_id):
    return db_session.scalar(
        select(func.count(EvidenceTaskStateLink.id))
        .join(EvidenceTask, EvidenceTaskStateLink.task_id == EvidenceTask.id)
        .where(EvidenceTask.assessment_id == assessment_id)
    )


# ---------------------------------------------------------------------------
# Test 1: tasks created with status=open
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_activate_creates_open_tasks(db_session, scenario):
    result = _activate(db_session, scenario)
    assert result["tasks_created"] > 0

    tasks = db_session.scalars(
        select(EvidenceTask).where(
            EvidenceTask.assessment_id == scenario["assessment"].id
        )
    ).all()
    assert len(tasks) == result["tasks_created"]
    assert all(t.status == "open" for t in tasks), (
        f"Found non-open task statuses: {[t.status for t in tasks if t.status != 'open']}"
    )


# ---------------------------------------------------------------------------
# Test 2: tasks link to ALL covered objectives (not just the first)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_tasks_link_all_covered_objectives(db_session, scenario):
    _activate(db_session, scenario)

    # AU.L2-3.3.1 is provider_satisfies with objectives [a,b,c,d,e,f].
    # Its two evidence specs should collectively link to all 6 objectives.
    # Find the control_states for AU.L2-3.3.1
    au_states = db_session.execute(
        select(ControlState.id, ControlState.objective_id)
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(
            ControlState.assessment_id == scenario["assessment"].id,
            Control.control_id == "AU.L2-3.3.1",
        )
    ).all()
    assert len(au_states) == 6, f"Expected 6 AU.L2-3.3.1 objectives, got {len(au_states)}"

    au_state_ids = {row.id for row in au_states}
    linked_ids = {
        lnk.control_state_id
        for lnk in db_session.scalars(
            select(EvidenceTaskStateLink).where(
                EvidenceTaskStateLink.control_state_id.in_(list(au_state_ids))
            )
        ).all()
    }
    assert linked_ids == au_state_ids, (
        f"Not all AU.L2-3.3.1 objectives are linked. Missing: {au_state_ids - linked_ids}"
    )


# ---------------------------------------------------------------------------
# Test 3: customer_owns controls generate no tasks
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_customer_owns_controls_generate_no_tasks(db_session, scenario):
    _activate(db_session, scenario)

    # IA family is entirely customer_owns in the rocketcyber baseline
    ia_state_ids = db_session.scalars(
        select(ControlState.id)
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .where(
            ControlState.assessment_id == scenario["assessment"].id,
            Control.family == "IA",
        )
    ).all()
    assert len(ia_state_ids) > 0, "IA control states should exist"

    ia_links = db_session.scalar(
        select(func.count(EvidenceTaskStateLink.id)).where(
            EvidenceTaskStateLink.control_state_id.in_(ia_state_ids)
        )
    )
    assert ia_links == 0, f"Expected 0 task links for IA (customer_owns), got {ia_links}"


# ---------------------------------------------------------------------------
# Test 4: artifact-key dedup — same (title, type) → one task, multiple links
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_artifact_dedup_same_key_creates_one_task(db_session, seeded):
    """Two baseline_controls sharing an identical evidence spec → one task, two links."""
    fw_id = seeded["framework_id"]
    org = Organization(name=f"DedupOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()

    assessment = Assessment(org_id=org.id, framework_id=fw_id, name="Dedup Test")
    db_session.add(assessment)
    db_session.flush()
    _seed_control_states(db_session, org.id, fw_id, assessment.id)
    db_session.flush()

    product = db_session.scalars(
        select(Product).where(Product.key == "rocketcyber")
    ).first()

    # Find two different controls that the product covers — attach identical evidence specs
    bcs = db_session.scalars(
        select(BaselineControl)
        .where(
            BaselineControl.product_id == product.id,
            BaselineControl.classification == "provider_satisfies",
        )
        .limit(2)
    ).all()
    assert len(bcs) >= 2, "Need at least 2 provider_satisfies baseline_controls"

    # Temporarily add duplicate-artifact specs to the second BC
    shared_artifact = "shared-dedup-artifact"
    shared_type = "screenshot"
    spec1 = BaselineEvidenceSpec(
        baseline_control_id=bcs[0].id,
        artifact_description=shared_artifact,
        evidence_type=shared_type,
        kb_reference="DeupSession",
    )
    spec2 = BaselineEvidenceSpec(
        baseline_control_id=bcs[1].id,
        artifact_description=shared_artifact,
        evidence_type=shared_type,
        kb_reference="DeupSession",
    )
    db_session.add_all([spec1, spec2])
    db_session.flush()

    activate_org_product(
        db_session, org_id=org.id, product_id=product.id, assessment_id=assessment.id
    )

    # Only one task should exist for the shared artifact key
    matching_tasks = db_session.scalars(
        select(EvidenceTask).where(
            EvidenceTask.assessment_id == assessment.id,
            EvidenceTask.title == shared_artifact,
        )
    ).all()
    assert len(matching_tasks) == 1, (
        f"Expected 1 task for shared artifact, got {len(matching_tasks)}"
    )

    # That one task should link to both baseline_controls' objective control_states
    task = matching_tasks[0]
    links = db_session.scalars(
        select(EvidenceTaskStateLink).where(EvidenceTaskStateLink.task_id == task.id)
    ).all()
    # Each BC may cover multiple objectives; at minimum both BCs have at least one objective
    linked_cs_ids = {lnk.control_state_id for lnk in links}

    # Confirm the linked states belong to different controls (proving cross-BC linking)
    cs_rows = db_session.execute(
        select(ControlState.id, AssessmentObjective.control_id)
        .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
        .where(ControlState.id.in_(list(linked_cs_ids)))
    ).all()

    control_ids_linked = {row.control_id for row in cs_rows}
    assert len(control_ids_linked) >= 2, (
        "Shared-artifact task should link to objectives from at least 2 controls"
    )


# ---------------------------------------------------------------------------
# Test 5: collection_session comes from kb_reference
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_collection_session_from_kb_reference(db_session, scenario):
    _activate(db_session, scenario)

    tasks = db_session.scalars(
        select(EvidenceTask).where(
            EvidenceTask.assessment_id == scenario["assessment"].id,
            EvidenceTask.collection_session == "Identity and Access Management",
        )
    ).all()
    # The rocketcyber baseline has several specs with kb: "Identity and Access Management"
    assert len(tasks) > 0, (
        "Expected tasks with collection_session='Identity and Access Management'"
    )
    assert all(t.collection_session == "Identity and Access Management" for t in tasks)


# ---------------------------------------------------------------------------
# Test 6: re-activation creates no duplicate tasks or links
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_reactivate_no_duplicate_tasks(db_session, scenario):
    _activate(db_session, scenario)
    count_after_first = _task_count(db_session, scenario["assessment"].id)
    links_after_first = _link_count(db_session, scenario["assessment"].id)

    result2 = _activate(db_session, scenario)

    assert result2["tasks_created"] == 0, (
        f"Re-activation should create 0 new tasks, got {result2['tasks_created']}"
    )
    assert _task_count(db_session, scenario["assessment"].id) == count_after_first
    assert _link_count(db_session, scenario["assessment"].id) == links_after_first


# ---------------------------------------------------------------------------
# Test 7: re-activation preserves collected task status (the key invariant)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_reactivate_preserves_collected_status(db_session, scenario):
    _activate(db_session, scenario)
    count_after_first = _task_count(db_session, scenario["assessment"].id)

    # Mark one task as collected
    task = db_session.scalars(
        select(EvidenceTask)
        .where(EvidenceTask.assessment_id == scenario["assessment"].id)
        .limit(1)
    ).first()
    assert task is not None
    collected_id = task.id
    task.status = "collected"
    db_session.flush()

    # Re-activate
    result2 = _activate(db_session, scenario)
    assert result2["tasks_created"] == 0

    # Collected task must still be collected
    refreshed = db_session.get(EvidenceTask, collected_id)
    assert refreshed is not None
    assert refreshed.status == "collected", (
        f"Expected status='collected' after re-activation, got '{refreshed.status}'"
    )

    # Total task count unchanged
    assert _task_count(db_session, scenario["assessment"].id) == count_after_first


# ---------------------------------------------------------------------------
# Test 8: GET /evidence-tasks returns tasks with linked_states
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_evidence_tasks_api(client, db_session, scenario):
    _activate(db_session, scenario)
    db_session.flush()

    org_id = scenario["org"].id
    assessment_id = scenario["assessment"].id

    r = client.get(f"/orgs/{org_id}/assessments/{assessment_id}/evidence-tasks")
    assert r.status_code == 200

    tasks = r.json()
    assert len(tasks) > 0

    for t in tasks:
        assert t["status"] == "open"
        assert t["artifact_type"] in ("screenshot", "export", "document", "link", "policy")
        assert "linked_states" in t
        # Every task must link to at least one control_state
        assert len(t["linked_states"]) > 0, f"Task {t['id']} has no linked_states"
        for ls in t["linked_states"]:
            assert "control_id" in ls
            assert "objective_key" in ls

    # source_product populated for tasks from the rocketcyber baseline
    tasks_with_product = [t for t in tasks if t["source_product_key"] is not None]
    assert len(tasks_with_product) > 0


# ---------------------------------------------------------------------------
# Test 9: task creation does not change control_state.status
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_task_creation_does_not_change_control_state_status(db_session, scenario):
    # Capture all statuses before activation
    before = {
        cs.id: cs.status
        for cs in db_session.scalars(
            select(ControlState).where(
                ControlState.assessment_id == scenario["assessment"].id
            )
        ).all()
    }

    _activate(db_session, scenario)

    after = {
        cs.id: cs.status
        for cs in db_session.scalars(
            select(ControlState).where(
                ControlState.assessment_id == scenario["assessment"].id
            )
        ).all()
    }

    # The magic loop DOES change control_state status (not_met → pending_evidence) —
    # that's expected. What must NOT happen: a task-seeding side-effect changing
    # a status that the loop itself didn't touch (i.e., customer_owns states).
    ia_state_ids = set(
        db_session.execute(
            select(ControlState.id)
            .join(AssessmentObjective, ControlState.objective_id == AssessmentObjective.id)
            .join(Control, AssessmentObjective.control_id == Control.id)
            .where(
                ControlState.assessment_id == scenario["assessment"].id,
                Control.family == "IA",
            )
        ).scalars().all()
    )
    for sid in ia_state_ids:
        assert after[sid] == before[sid], (
            f"IA control state {sid} changed from '{before[sid]}' to '{after[sid]}' "
            "— task seeding must not modify customer_owns states"
        )
