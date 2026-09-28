"""Integration tests for routers/documents.py (roadmap N.1 and N.2):
the core versioned record shape, and the editing/diff/history surface
built on it.

N.2 made `base_version_id` a required field on every edit of a document
that already has a version (optimistic concurrency -- see
create_document_version's docstring). The N.1 scenarios below are
otherwise unchanged; they just now state which version each edit started
from, the same as the real editor does.

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
        json={"body": "Revised access control policy text.", "base_version_id": v1_id},
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
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions",
        json={"body": "v2", "base_version_id": v1_id},
    )

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
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions",
        json={"body": "draft edit", "base_version_id": approved_version_id},
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
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions",
        json={"body": "v2 text", "base_version_id": v1_id},
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


# ---------------------------------------------------------------------------
# N.2 -- concurrent editing (section 1)
# ---------------------------------------------------------------------------


def _new_version(client, org_id, doc_id, base_version_id, body):
    return client.post(
        f"/orgs/{org_id}/documents/{doc_id}/versions",
        json={"body": body, "base_version_id": base_version_id},
    )


def test_concurrent_edit_from_a_stale_base_is_refused_with_the_winner(
    client, db_session, fake_msp_admin
):
    """Two people editing one document is a real MSP scenario and a silent
    last-write-wins is the wrong answer. The second saver is told, and is
    told what won, so the UI can offer a diff instead of a shrug.
    """
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    v1_id = doc["current_version"]["id"]

    # Both editors opened v1.
    first = _new_version(client, d["org"].id, doc["id"], v1_id, "Editor A's rewrite.")
    assert first.status_code == 201, first.text
    v2_id = first.json()["id"]

    second = _new_version(client, d["org"].id, doc["id"], v1_id, "Editor B's rewrite.")
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert detail["base_version_id"] == v1_id
    assert detail["current_version_id"] == v2_id
    assert detail["current_version_number"] == 2

    # Nothing was lost and nothing was written: still exactly two versions.
    versions = client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}").json()["versions"]
    assert [v["version_number"] for v in versions] == [2, 1]
    assert [v["body"] for v in versions if v["version_number"] == 2] == ["Editor A's rewrite."]


def test_losing_editor_can_re_save_onto_the_new_base(client, db_session, fake_msp_admin):
    """The conflict never strands anyone -- which is the whole reason this
    is a conflict check and not a lock."""
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    v1_id = doc["current_version"]["id"]

    v2_id = _new_version(client, d["org"].id, doc["id"], v1_id, "A").json()["id"]
    assert _new_version(client, d["org"].id, doc["id"], v1_id, "B").status_code == 409

    retry = _new_version(client, d["org"].id, doc["id"], v2_id, "B, rebased")
    assert retry.status_code == 201
    assert retry.json()["version_number"] == 3


def test_edit_without_a_base_version_is_refused(client, db_session, fake_msp_admin):
    """A client that cannot say what it edited from must not be allowed to
    overwrite whatever happens to be current."""
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)

    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions", json={"body": "blind write"}
    )
    assert r.status_code == 422
    assert "base_version_id" in r.text


def test_editing_an_approved_document_leaves_it_byte_identical(
    client, db_session, fake_msp_admin
):
    """N.2 section 7's explicit ask: assert the negative. Editing an
    approved document produces a new draft and the approved version is
    unchanged in every field, not merely still present.
    """
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    approved_id = doc["current_version"]["id"]
    assert client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    ).status_code == 200

    before = client.get(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions/{approved_id}"
    ).json()
    assert before["status"] == "approved"

    r = _new_version(client, d["org"].id, doc["id"], approved_id, "# Rewritten\n\nNew text.")
    assert r.status_code == 201
    assert r.json()["status"] == "draft", "a new version always starts as draft"
    assert r.json()["id"] != approved_id

    after = client.get(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions/{approved_id}"
    ).json()
    assert after == before, "the approved version must be untouched, field for field"


# ---------------------------------------------------------------------------
# N.2 -- diffs (section 2)
# ---------------------------------------------------------------------------


def test_diff_of_version_one_is_not_a_broken_view(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)

    r = client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}/diff")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_version"] is None
    assert body["body"]["is_initial"] is True
    assert {row["op"] for row in body["body"]["rows"]} == {"insert"}


def test_diff_defaults_to_current_against_its_predecessor(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id, body="All users must authenticate with MFA.")
    v1_id = doc["current_version"]["id"]
    _new_version(
        client,
        d["org"].id,
        doc["id"],
        v1_id,
        "All users must authenticate with phishing-resistant MFA.",
    )

    body = client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}/diff").json()
    assert body["from_version"]["version_number"] == 1
    assert body["to_version"]["version_number"] == 2
    assert body["body"]["changed_lines"] == 1
    row = next(r for r in body["body"]["rows"] if r["op"] == "replace")
    highlighted = [row["new_text"][s["start"] : s["end"]] for s in row["new_spans"]]
    assert highlighted == ["phishing-resistant"]


def test_diff_between_a_superseded_version_and_the_current_one(
    client, db_session, fake_msp_admin
):
    """"What changed since we approved this" is the common case -- and
    after a republish the earlier approved version is superseded, so it
    must stay diffable."""
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id, body="Reviewed annually.")
    v1_id = doc["current_version"]["id"]
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    v2_id = _new_version(client, d["org"].id, doc["id"], v1_id, "Reviewed quarterly.").json()[
        "id"
    ]
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )

    r = client.get(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/diff",
        params={"from_version_id": v1_id, "to_version_id": v2_id},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_version"]["status"] == "superseded"
    assert body["to_version"]["status"] == "approved"
    assert body["body"]["changed_lines"] == 1


def test_diff_rejects_a_version_from_another_document(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc_a = _create_doc(client, d["org"].id)
    doc_b = _create_doc(client, d["org"].id, doc_id="AC-POL-002", title="Second")
    foreign = doc_b["current_version"]["id"]

    r = client.get(
        f"/orgs/{d['org'].id}/documents/{doc_a['id']}/diff",
        params={"from_version_id": foreign},
    )
    assert r.status_code == 404


def test_objective_set_change_is_reported_even_when_the_body_is_identical(
    client, db_session, fake_msp_admin
):
    """N.2 section 2's own point: a document whose objective tags changed
    affects the SSP even if the text did not."""
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id, body="Unchanged text.")
    v1_id = doc["current_version"]["id"]
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )

    # Same body, but retagged onto a second objective before republishing.
    v2_id = _new_version(client, d["org"].id, doc["id"], v1_id, "Unchanged text.").json()["id"]
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_b"].id)},
    )
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )

    body = client.get(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/diff",
        params={"from_version_id": v1_id, "to_version_id": v2_id},
    ).json()
    assert body["body"]["identical"] is True, "the text really is unchanged"
    assert body["objectives"]["changed"] is True, "but the objective coverage is not"
    assert str(d["obj_b"].id) in body["objectives"]["added"]


def test_published_version_objective_set_survives_being_superseded(
    client, db_session, fake_msp_admin
):
    """A republish archives the earlier version's evidence links. "What
    did v1 cover" must survive that -- so the lookup deliberately includes
    archived links.
    """
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    v1_id = doc["current_version"]["id"]
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    _new_version(client, d["org"].id, doc["id"], v1_id, "v2")
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )

    v1 = client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}/versions/{v1_id}").json()
    assert v1["status"] == "superseded"
    assert v1["objective_basis"] == "published"
    assert v1["objective_ids"] == [str(d["obj_a"].id)]


def test_draft_version_reports_a_current_objective_basis_not_a_published_one(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    v = client.get(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/versions/{doc['current_version']['id']}"
    ).json()
    assert v["status"] == "draft"
    assert v["objective_basis"] == "current"
    assert v["objective_ids"] == [str(d["obj_a"].id)]

    diff = client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}/diff").json()
    assert diff["objective_basis_note"] is not None, (
        "an unpublished version's tag set must be labelled, not presented as a record"
    )


# ---------------------------------------------------------------------------
# N.2 -- audit surfacing (section 4)
# ---------------------------------------------------------------------------


def test_history_returns_versions_and_the_audit_actions_beside_them(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    v1_id = doc["current_version"]["id"]
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    _new_version(client, d["org"].id, doc["id"], v1_id, "v2 text")
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )

    r = client.get(f"/orgs/{d['org'].id}/documents/{doc['id']}/history")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [v["version_number"] for v in body["versions"]] == [2, 1]

    actions = [e["action"] for e in body["events"]]
    for expected in (
        "document.create",
        "document.objective_tag.add",
        "document.version.create",
        "document.publish",
    ):
        assert expected in actions, f"{expected} missing from the document history"

    # Newest first, and the acting user is resolved to a person rather
    # than left as a bare GUID.
    assert actions[0] == "document.publish"
    publish_event = body["events"][0]
    assert publish_event["actor_user"] is not None
    assert publish_event["actor_user"]["id"] == str(fake_msp_admin.id)


def test_history_does_not_leak_another_documents_events(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc_a = _create_doc(client, d["org"].id)
    doc_b = _create_doc(client, d["org"].id, doc_id="AC-POL-002", title="Second")

    events = client.get(f"/orgs/{d['org'].id}/documents/{doc_a['id']}/history").json()["events"]
    ids = {e["entity_id"] for e in events}
    assert doc_b["id"] not in ids
    assert doc_b["current_version"]["id"] not in ids


# ---------------------------------------------------------------------------
# N.2 -- RLS and roles on the new endpoints
# ---------------------------------------------------------------------------


def test_new_endpoints_are_not_reachable_across_orgs(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id)
    version_id = doc["current_version"]["id"]

    other_org = Organization(name=f"OtherOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(other_org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=other_org.id)
    db_session.commit()

    for path in (
        f"/orgs/{other_org.id}/documents/{doc['id']}/versions/{version_id}",
        f"/orgs/{other_org.id}/documents/{doc['id']}/diff",
        f"/orgs/{other_org.id}/documents/{doc['id']}/history",
    ):
        assert client.get(path).status_code == 404, f"{path} resolved across orgs"


def test_c3pao_assessor_can_read_diffs_and_history_but_not_edit(db_session, fake_msp_admin):
    org = Organization(name=f"AssessorDiffOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)
    db_session.commit()

    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    admin_client = TestClient(app)
    doc = _create_doc(admin_client, org.id)
    v1_id = doc["current_version"]["id"]
    _new_version(admin_client, org.id, doc["id"], v1_id, "v2")
    app.dependency_overrides.clear()

    assessor = _make_fake_user(role="c3pao_assessor", email="assessor-diff@example.com")
    _grant(db_session, assessor, org_id=org.id)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, assessor)
    try:
        c = TestClient(app)
        assert c.get(f"/orgs/{org.id}/documents/{doc['id']}/diff").status_code == 200
        assert c.get(f"/orgs/{org.id}/documents/{doc['id']}/history").status_code == 200
        assert (
            c.get(f"/orgs/{org.id}/documents/{doc['id']}/versions/{v1_id}").status_code == 200
        )
        assert c.post(
            f"/orgs/{org.id}/documents/{doc['id']}/versions",
            json={"body": "nope", "base_version_id": v1_id},
        ).status_code == 403
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# N.2 -- rendering safety on the bundle/PDF path (section 3)
# ---------------------------------------------------------------------------


HOSTILE_POLICY_BODY = (
    "# Incident Response Policy\n\n"
    "<script>alert('xss')</script>\n\n"
    "<img src=x onerror=alert('xss')>\n\n"
    "[click](javascript:alert('xss'))\n\n"
    "<svg/onload=alert('xss')></svg>\n\n"
    "<iframe src=//evil.test></iframe>\n\n"
    "Legitimate **bold** text and a [real link](https://example.test).\n"
)


def test_hostile_document_body_renders_safely_into_the_bundle(
    client, db_session, fake_msp_admin
):
    """N.2 section 3/7: prove the bundle path, not just the browser.

    The body is stored verbatim -- nothing is stripped on save, because
    the author may legitimately be documenting markup -- and neutralised
    on render, which is the line svg_sanitize.py already draws.
    """
    from app.bundle_service import _documents_body, snapshot_bundle
    from app.storage import NullStorageClient

    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id, body=HOSTILE_POLICY_BODY)
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    assert client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    ).status_code == 200

    stored = db_session.scalars(
        select(DocumentVersion).where(DocumentVersion.id == uuid.UUID(doc["current_version"]["id"]))
    ).one()
    assert stored.body == HOSTILE_POLICY_BODY, "storage must be verbatim; sanitising is on render"

    snapshot = snapshot_bundle(
        db_session, org_id=d["org"].id, assessment_id=d["assessment"].id,
        storage=NullStorageClient(),
    )
    assert [s.doc_id for s in snapshot.documents] == ["AC-POL-001"]

    rendered = _documents_body(snapshot).lower()

    # Live markup is what must be absent. The escaped TEXT of the hostile
    # input is expected to be present and is asserted below -- an author
    # documenting a script tag in an appendix must still see it, and
    # checking for the substring "onerror" would fail on exactly that safe
    # output. render_html having returned at all is itself the allowlist
    # guarantee: it raises UnsafeRenderError rather than emitting an
    # unvetted tag or attribute.
    for live_tag in ("<script", "<img", "<iframe", "<svg", "<object", "<embed"):
        assert live_tag not in rendered, f"{live_tag!r} reached the bundle HTML as markup"
    assert "<a href=\"javascript:" not in rendered
    assert "&lt;script&gt;" in rendered, "neutralised, not silently dropped"
    assert "&lt;img src=x onerror=" in rendered, "neutralised, not silently dropped"
    assert "<strong>bold</strong>" in rendered
    assert "example.test" in rendered


def test_only_approved_versions_reach_the_bundle(client, db_session, fake_msp_admin):
    """A draft is not a compliance record and a superseded version is the
    previous answer -- neither belongs in a point-in-time bundle."""
    from app.bundle_service import snapshot_bundle
    from app.storage import NullStorageClient

    d = _seed(db_session, fake_msp_admin)
    doc = _create_doc(client, d["org"].id, body="Draft only.")
    v1_id = doc["current_version"]["id"]

    snapshot = snapshot_bundle(
        db_session, org_id=d["org"].id, assessment_id=d["assessment"].id,
        storage=NullStorageClient(),
    )
    assert snapshot.documents == [], "an unpublished draft must not appear in the bundle"

    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    _new_version(client, d["org"].id, doc["id"], v1_id, "Second answer.")
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )

    db_session.expire_all()
    snapshot = snapshot_bundle(
        db_session, org_id=d["org"].id, assessment_id=d["assessment"].id,
        storage=NullStorageClient(),
    )
    assert [s.body_markdown for s in snapshot.documents] == ["Second answer."]
    assert [s.version_number for s in snapshot.documents] == [2]
