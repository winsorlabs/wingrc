"""Integration tests for routers/documents.py (roadmap N.1): the core,
versioned document-library record shape.

Run in-container:
    docker compose exec backend pytest tests/test_documents_api.py -m integration -v
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    Contact,
    Control,
    ControlState,
    DocumentVersion,
    Evidence,
    EvidenceStateLink,
    Framework,
    Organization,
)
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(db_session, fake_msp_admin) -> dict:
    """Org + a real framework/control/objective + an in_progress assessment
    (so ControlState rows exist to tag/publish against) + a Contact (for
    approved_by_contact_id). sprs_weight=5 so the SPRS-unaffected check has
    a real, non-zero score to compare.
    """
    org = Organization(name=f"DocOrg-{uuid.uuid4().hex[:8]}")
    fw = Framework(key=f"fw-doc-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)

    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC", title="Access Control",
        requirement_text="Limit system access.", sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj_a = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Objective a.")
    obj_b = AssessmentObjective(control_id=ctrl.id, objective_key="b", text="Objective b.")
    db_session.add_all([obj_a, obj_b])
    db_session.flush()

    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=fw.id, name="Test Assessment"
    )
    db_session.flush()

    contact = Contact(
        org_id=org.id, name="Jane Approver", email="jane@example.com", affiliation="customer"
    )
    db_session.add(contact)
    db_session.flush()

    cs_a = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id, ControlState.objective_id == obj_a.id
        )
    ).one()
    cs_b = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id, ControlState.objective_id == obj_b.id
        )
    ).one()
    db_session.commit()

    return {
        "org": org, "fw": fw, "ctrl": ctrl, "obj_a": obj_a, "obj_b": obj_b,
        "assessment": assessment, "contact": contact, "cs_a": cs_a, "cs_b": cs_b,
    }


def _create_doc(client, org_id, **overrides) -> dict:
    payload = {
        "doc_id": "AC-POL-001", "doc_type": "policy", "title": "Access Control Policy",
        "body": "Our access control policy states...",
    }
    payload.update(overrides)
    r = client.post(f"/orgs/{org_id}/documents", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Create, versioning, and "nothing mutates a version" (§1, §6)
# ---------------------------------------------------------------------------


def test_create_document_creates_version_one_as_draft(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    body = _create_doc(client, d["org"].id)

    assert body["doc_id"] == "AC-POL-001"
    assert body["doc_type"] == "policy"
    assert body["current_version"]["version_number"] == 1
    assert body["current_version"]["status"] == "draft"
    assert body["current_version"]["body"] == "Our access control policy states..."
    assert body["versions"] == [body["current_version"]]


def test_editing_creates_a_new_version_prior_version_intact(client, db_session, fake_msp_admin):
    """§6's own explicit ask: assert the negative -- nothing mutates an
    existing version."""
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    v1_id = doc["current_version"]["id"]

    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions",
        json={"body": "Revised access control policy text."},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()
    assert v2["version_number"] == 2
    assert v2["status"] == "draft"
    assert v2["id"] != v1_id

    detail = client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}").json()
    assert detail["current_version"]["id"] == v2["id"]
    assert len(detail["versions"]) == 2

    v1_after = next(v for v in detail["versions"] if v["id"] == v1_id)
    assert v1_after["body"] == "Our access control policy states...", (
        "the prior version's body must be untouched by creating a new one"
    )
    assert v1_after["status"] == "draft", (
        "creating a new version must not change an older one's status"
    )


# ---------------------------------------------------------------------------
# Status transitions (§2)
# ---------------------------------------------------------------------------


def test_manual_status_transition_draft_to_under_review_and_back(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    v_id = doc["current_version"]["id"]

    r = client.patch(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions/{v_id}",
        json={"status": "under_review"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "under_review"

    r = client.patch(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions/{v_id}", json={"status": "draft"}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "draft"


def test_cannot_manually_set_status_to_approved_or_superseded(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    v_id = doc["current_version"]["id"]

    for bad_status in ("approved", "superseded"):
        r = client.patch(
            f"/orgs/{d['org'].id}/documents/{doc['id']}/versions/{v_id}",
            json={"status": bad_status},
        )
        assert r.status_code == 422, r.text


def test_cannot_patch_status_of_a_non_current_version(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    v1_id = doc["current_version"]["id"]
    client.post(f"/orgs/{d['org'].id}/documents/{doc['id']}/versions", json={"body": "v2"})

    r = client.patch(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions/{v1_id}",
        json={"status": "under_review"},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Objective tagging
# ---------------------------------------------------------------------------


def test_tag_and_untag_objective(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)

    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    assert r.status_code == 201, r.text

    detail = client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}").json()
    assert detail["tagged_objective_ids"] == [str(d["obj_a"].id)]

    r = client.delete(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags/{d['obj_a'].id}"
    )
    assert r.status_code == 204

    detail = client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}").json()
    assert detail["tagged_objective_ids"] == []


def test_tagging_unknown_objective_422s(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(uuid.uuid4())},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Publish (§3, §6): evidence, control_state untouched, the approved version
# ---------------------------------------------------------------------------


def test_publish_attaches_evidence_for_every_tagged_objective_and_leaves_control_state_alone(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    for obj in (d["obj_a"], d["obj_b"]):
        client.post(
            f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
            json={"objective_id": str(obj.id)},
        )

    db_session.expire_all()
    status_before = (
        db_session.get(ControlState, d["cs_a"].id).status,
        db_session.get(ControlState, d["cs_b"].id).status,
    )

    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_version"]["status"] == "approved"
    assert body["current_version"]["approved_by_contact_id"] == str(d["contact"].id)
    assert body["current_version"]["approved_at"] is not None

    db_session.expire_all()
    status_after = (
        db_session.get(ControlState, d["cs_a"].id).status,
        db_session.get(ControlState, d["cs_b"].id).status,
    )
    assert status_after == status_before, "publish must never change control_state.status"

    links = db_session.scalars(
        select(EvidenceStateLink).where(
            EvidenceStateLink.control_state_id.in_([d["cs_a"].id, d["cs_b"].id])
        )
    ).all()
    assert len(links) == 2
    evidence_ids = {link.evidence_id for link in links}
    assert len(evidence_ids) == 1, "one Evidence row satisfies both tagged objectives"
    ev = db_session.get(Evidence, evidence_ids.pop())
    assert ev.kind == "reference"
    assert ev.reference_location == "AC-POL-001"
    assert ev.artifact_type == "document"


def test_evidence_resolves_to_the_approved_version_not_current(client, db_session, fake_msp_admin):
    """§3's own question, answered and asserted directly: the Evidence
    created by publish must keep pointing at the version that was
    actually approved, even after a later edit makes a different version
    current."""
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    approved_version_id = doc["current_version"]["id"]

    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    assert r.status_code == 200, r.text

    # A new edit makes a DIFFERENT version current -- the approved one is
    # no longer "current" at all.
    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions", json={"body": "draft edit"}
    )
    assert r.status_code == 201
    new_current_id = r.json()["id"]
    assert new_current_id != approved_version_id

    db_session.expire_all()
    ev = db_session.scalars(
        select(Evidence).where(Evidence.reference_location == "AC-POL-001")
    ).one()
    assert str(ev.source_document_version_id) == approved_version_id
    assert ev.source_document_version_id != new_current_id


def test_publish_with_no_tags_creates_unlinked_evidence(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    assert r.status_code == 200, r.text

    db_session.expire_all()
    ev = db_session.scalars(
        select(Evidence).where(Evidence.reference_location == "AC-POL-001")
    ).one()
    links = db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.evidence_id == ev.id)
    ).all()
    assert links == []


def test_cannot_publish_an_already_approved_version(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    assert r.status_code == 409


def test_publish_rejects_contact_from_another_org(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    other_org = Organization(name=f"OtherOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(other_org)
    db_session.flush()
    other_contact = Contact(
        org_id=other_org.id, name="Wrong Org Contact", email="wrong@example.com",
        affiliation="customer",
    )
    db_session.add(other_contact)
    db_session.commit()

    doc = _create_doc(client, d["org"].id)
    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(other_contact.id)},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Republish (§3): supersede, archive old links, keep old Evidence resolvable
# ---------------------------------------------------------------------------


def test_republish_supersedes_prior_version_and_archives_its_evidence_links(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    v1_id = doc["current_version"]["id"]

    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    db_session.expire_all()
    v1_evidence = db_session.scalars(
        select(Evidence).where(Evidence.source_document_version_id == uuid.UUID(v1_id))
    ).one()
    v1_link = db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.evidence_id == v1_evidence.id)
    ).one()
    assert v1_link.is_archived is False

    # Edit and republish.
    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions", json={"body": "v2 text"}
    )
    v2_id = r.json()["id"]
    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    assert r.status_code == 200, r.text

    db_session.expire_all()
    v1_after = db_session.get(DocumentVersion, uuid.UUID(v1_id))
    assert v1_after.status == "superseded"
    v2_after = db_session.get(DocumentVersion, uuid.UUID(v2_id))
    assert v2_after.status == "approved"

    # The OLD evidence row is untouched (still resolvable, never deleted) --
    # only its LINK is archived.
    db_session.refresh(v1_evidence)
    assert v1_evidence.source_document_version_id == uuid.UUID(v1_id)
    db_session.refresh(v1_link)
    assert v1_link.is_archived is True
    assert v1_link.archived_at is not None

    v2_evidence = db_session.scalars(
        select(Evidence).where(Evidence.source_document_version_id == uuid.UUID(v2_id))
    ).one()
    v2_link = db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.evidence_id == v2_evidence.id)
    ).one()
    assert v2_link.is_archived is False
    assert v2_evidence.id != v1_evidence.id, "republish creates a new Evidence row, not a mutation"


# ---------------------------------------------------------------------------
# Delete (§4/§5-shaped concerns): refused once evidence exists
# ---------------------------------------------------------------------------


def test_delete_document_with_no_evidence_succeeds(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    r = client.delete(f"/orgs/{d['org'].id}/documents/{doc['id']}")
    assert r.status_code == 204
    assert client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}").status_code == 404


def test_delete_document_refused_once_published(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    r = client.delete(f"/orgs/{d['org'].id}/documents/{doc['id']}")
    assert r.status_code == 409
    assert client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}").status_code == 200


# ---------------------------------------------------------------------------
# doc_id uniqueness and validation (§2, §4)
# ---------------------------------------------------------------------------


def test_doc_id_unique_per_org(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    _create_doc(client, d["org"].id)
    r = client.post(
        f"/orgs/{d['org'].id}/documents",
        json={"doc_id": "AC-POL-001", "doc_type": "policy", "title": "Duplicate"},
    )
    assert r.status_code == 409


def test_doc_id_collides_across_orgs_without_error(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    other_org = Organization(name=f"OtherOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(other_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=other_org.id)
    db_session.commit()

    _create_doc(client, d["org"].id)
    r = client.post(
        f"/orgs/{other_org.id}/documents",
        json={"doc_id": "AC-POL-001", "doc_type": "policy", "title": "Same ID, other org"},
    )
    assert r.status_code == 201, r.text


@pytest.mark.parametrize(
    "bad_doc_id",
    ["", "a", "-leading-hyphen", "trailing-hyphen-", "has/slash", "has\\backslash", "has..dots"],
)
def test_doc_id_validation_rejects_bad_values(client, db_session, fake_msp_admin, bad_doc_id):
    d = _seed(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{d['org'].id}/documents",
        json={"doc_id": bad_doc_id, "doc_type": "policy", "title": "Bad ID"},
    )
    assert r.status_code == 422, f"{bad_doc_id!r} should have been rejected"


# ---------------------------------------------------------------------------
# RLS (§4, §6): real HTTP, not a direct query
# ---------------------------------------------------------------------------


def test_rls_org_cannot_see_another_orgs_documents(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)

    other_org = Organization(name=f"OtherOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(other_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=other_org.id)
    db_session.commit()

    r = client.get(f"/orgs/{other_org.id}/documents")
    assert r.status_code == 200
    assert r.json() == []

    r = client.get(f"/orgs/{other_org.id}/documents/{doc['id']}")
    assert r.status_code == 404, "a document id from another org must not resolve, even by guess"


# ---------------------------------------------------------------------------
# c3pao_assessor: read-only (§4, §6)
# ---------------------------------------------------------------------------


def test_c3pao_assessor_can_read_but_not_mutate(db_session, fake_msp_admin):
    org = Organization(name=f"AssessorDocOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)
    db_session.commit()

    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    admin_client = TestClient(app)
    doc = _create_doc(admin_client, org.id)
    app.dependency_overrides.clear()

    assessor = _make_fake_user(role="c3pao_assessor", email="assessor-docs@example.com")
    _grant(db_session, assessor, org_id=org.id)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, assessor)
    try:
        c = TestClient(app)
        assert c.get(f"/orgs/{org.id}/documents").status_code == 200
        assert c.get(f"/orgs/{org.id}/documents/{doc['id']}").status_code == 200

        assert c.post(
            f"/orgs/{org.id}/documents",
            json={"doc_id": "IA-POL-001", "doc_type": "policy", "title": "Nope"},
        ).status_code == 403
        assert c.patch(
            f"/orgs/{org.id}/documents/{doc['id']}", json={"title": "Nope"}
        ).status_code == 403
        assert c.post(
            f"/orgs/{org.id}/documents/{doc['id']}/versions", json={"body": "nope"}
        ).status_code == 403
        assert c.post(
            f"/orgs/{org.id}/documents/{doc['id']}/publish",
            json={"approved_by_contact_id": str(uuid.uuid4())},
        ).status_code == 403
        assert c.delete(f"/orgs/{org.id}/documents/{doc['id']}").status_code == 403
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# SPRS unaffected (§6)
# ---------------------------------------------------------------------------


def test_sprs_unaffected_by_document_existence_or_publish(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    db_session.expire_all()
    before_score = db_session.get(type(d["assessment"]), d["assessment"].id).sprs_score

    doc = _create_doc(client, d["org"].id)
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )

    db_session.expire_all()
    after_score = db_session.get(type(d["assessment"]), d["assessment"].id).sprs_score
    assert after_score == before_score
