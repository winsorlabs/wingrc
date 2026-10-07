"""Integration tests for roadmap N.3 -- document approval and review cadence.

The load-bearing assertions here are mostly *negatives*, because N.3's
design is largely a set of refusals:

* re-approving an unchanged document creates no version and mutates nothing
* the approved body stays byte-identical across a reaffirmation cycle
* N.2's diff history gains no entry, because nothing changed
* the scheduled digest changes no document state at all
* overdue flags and never invalidates -- evidence links stay untouched and
  the SPRS score does not move

A test that only checked the positive ("a reaffirmation row appeared")
would pass against an implementation that also quietly wrote a version,
archived evidence, or moved the score. Those are the failures that matter.

Run in-container:
    docker compose exec backend pytest tests/test_document_approval.py -m integration -v
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import scheduler
from app.auth import get_current_user
from app.config import get_settings
from app.db import get_session
from app.document_reviews import add_months
from app.email_service import EmailSendResult
from app.engine import start_assessment
from app.models import (
    Assessment,
    AssessmentObjective,
    Contact,
    ContactDocumentationRole,
    Control,
    ControlState,
    DocumentApproval,
    DocumentReviewNotification,
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
    app_ = _app()
    app_.dependency_overrides[get_session] = _app_session(db_session)
    app_.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app_)
    app_.dependency_overrides.clear()


def _app():
    from app.main import app

    return app


@pytest.fixture(autouse=True)
def _public_url(monkeypatch):
    monkeypatch.setenv("WINGRC_PUBLIC_URL", "https://wingrc.example.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def sent_emails(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def _fake_send(session, *, to, subject, body, template):
        calls.append((to, subject, body))
        return EmailSendResult(sent=True)

    monkeypatch.setattr(scheduler.email_service, "send", _fake_send)
    return calls


def _seed(db_session, fake_msp_admin, *, org_name: str | None = None) -> dict:
    """Org + framework/control/objectives + in_progress assessment + contact.

    sprs_weight=5 so the "SPRS unaffected" assertions compare a real,
    non-zero score rather than 0 == 0, which would pass no matter what.
    """
    org = Organization(name=org_name or f"N3Org-{uuid.uuid4().hex[:8]}")
    fw = Framework(key=f"fw-n3-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)

    ctrl = Control(
        framework_id=fw.id,
        control_id="AC.L2-3.1.1",
        family="AC",
        title="Access Control",
        requirement_text="Limit system access.",
        sprs_weight=5,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    obj_a = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Objective a.")
    db_session.add(obj_a)
    db_session.flush()

    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=fw.id, name="N3 Assessment"
    )
    db_session.flush()

    contact = Contact(
        org_id=org.id, name="Jane Approver", email="jane@example.com", affiliation="customer"
    )
    officer = Contact(
        org_id=org.id, name="Sam Officer", email="sam@example.com", affiliation="msp"
    )
    db_session.add_all([contact, officer])
    db_session.flush()
    # No org_id on this table -- it is one of the transitively-scoped ones
    # (migration 0061), org-gated through contact. Getting this wrong is a
    # NOT NULL error, not a silent scoping bug, but worth stating.
    db_session.add(
        ContactDocumentationRole(contact_id=officer.id, role="security_officer")
    )
    cs_a = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id,
            ControlState.objective_id == obj_a.id,
        )
    ).one()
    db_session.commit()
    return {
        "org": org,
        "assessment": assessment,
        "contact": contact,
        "officer": officer,
        "obj_a": obj_a,
        "cs_a": cs_a,
    }


BODY = "Our access control policy states that access is limited."


def _published_doc(client, db_session, d: dict, *, doc_id: str = "AC-POL-001") -> dict:
    """A document tagged to an objective and published, so it has an
    Evidence row and a live EvidenceStateLink to assert stay untouched."""
    org_id = d["org"].id
    r = client.post(
        f"/orgs/{org_id}/documents",
        json={"doc_id": doc_id, "doc_type": "policy", "title": "AC Policy", "body": BODY},
    )
    assert r.status_code == 201, r.text
    doc = r.json()
    r = client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/objective-tags",
        json={"objective_id": str(d["obj_a"].id)},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _backdate_approvals(db_session, document_id, *, days: int) -> None:
    """Move every approval for this document into the past.

    Backdating the *approval* rather than freezing a clock is deliberate:
    the cadence is derived from stored approval timestamps, so this
    exercises the real read path instead of a patched `now`.
    """
    for a in db_session.scalars(
        select(DocumentApproval).where(DocumentApproval.document_id == document_id)
    ).all():
        a.approved_at = a.approved_at - timedelta(days=days)
    db_session.commit()


# ---------------------------------------------------------------------------
# Publishing writes the authoritative record, and the N.1 columns mirror it
# ---------------------------------------------------------------------------


def test_publish_writes_an_initial_approval(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)

    approvals = db_session.scalars(
        select(DocumentApproval).where(DocumentApproval.document_id == uuid.UUID(doc["id"]))
    ).all()
    assert len(approvals) == 1
    assert approvals[0].approval_type == "initial"
    assert approvals[0].approved_by_contact_id == d["contact"].id
    assert approvals[0].approver_name == "Jane Approver"


def test_initial_approval_agrees_with_the_denormalized_version_columns(
    client, db_session, fake_msp_admin
):
    """Pins the mirror DocumentApproval's docstring promises.

    DocumentVersion.approved_at/approved_by_contact_id are documented as a
    snapshot of the initial approval. That is only safe if it is true, so
    it is asserted rather than trusted -- if publish ever sets one without
    the other, this fails instead of the two drifting silently.
    """
    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)

    version = db_session.get(DocumentVersion, uuid.UUID(doc["current_version"]["id"]))
    initial = db_session.scalars(
        select(DocumentApproval).where(
            DocumentApproval.document_version_id == version.id,
            DocumentApproval.approval_type == "initial",
        )
    ).one()
    assert version.approved_at == initial.approved_at
    assert version.approved_by_contact_id == initial.approved_by_contact_id


def test_only_one_initial_approval_per_version_is_possible(
    client, db_session, fake_msp_admin
):
    """The partial unique index, exercised rather than assumed."""
    from sqlalchemy.exc import IntegrityError

    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)
    version_id = uuid.UUID(doc["current_version"]["id"])

    db_session.add(
        DocumentApproval(
            id=uuid.uuid4(),
            document_id=uuid.UUID(doc["id"]),
            document_version_id=version_id,
            org_id=d["org"].id,
            approval_type="initial",
            approved_at=datetime.now(UTC),
            approved_by_contact_id=d["contact"].id,
            approver_name="Duplicate",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


# ---------------------------------------------------------------------------
# Reaffirmation: the central negative assertions (sec 0, sec 7)
# ---------------------------------------------------------------------------


def test_reaffirm_creates_no_new_version_and_a_durable_record(
    client, db_session, fake_msp_admin
):
    """Both halves of section 7's first bullet, in one place."""
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)
    doc_uuid = uuid.UUID(doc["id"])

    versions_before = db_session.scalars(
        select(DocumentVersion.id).where(DocumentVersion.document_id == doc_uuid)
    ).all()

    r = client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/reaffirm",
        json={"approved_by_contact_id": str(d["contact"].id), "note": "Annual review, no change"},
    )
    assert r.status_code == 200, r.text

    versions_after = db_session.scalars(
        select(DocumentVersion.id).where(DocumentVersion.document_id == doc_uuid)
    ).all()
    assert set(versions_after) == set(versions_before), "reaffirmation must not create a version"

    approvals = db_session.scalars(
        select(DocumentApproval)
        .where(DocumentApproval.document_id == doc_uuid)
        .order_by(DocumentApproval.approved_at)
    ).all()
    assert [a.approval_type for a in approvals] == ["initial", "reaffirmation"]
    assert approvals[-1].note == "Annual review, no change"
    assert approvals[-1].approver_name == "Jane Approver"


def test_reaffirm_leaves_the_approved_version_byte_identical(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)
    version_id = uuid.UUID(doc["current_version"]["id"])

    before = db_session.get(DocumentVersion, version_id)
    snapshot = (
        before.body,
        before.status,
        before.version_number,
        before.approved_at,
        before.approved_by_contact_id,
    )

    client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/reaffirm",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    db_session.expire_all()
    after = db_session.get(DocumentVersion, version_id)
    assert (
        after.body,
        after.status,
        after.version_number,
        after.approved_at,
        after.approved_by_contact_id,
    ) == snapshot
    assert after.body == BODY
    assert after.status == "approved"


def test_reaffirm_does_not_change_the_diff_history(client, db_session, fake_msp_admin):
    """N.2's guarantee holds: nothing changed, so the diff says nothing did."""
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)

    before = client.get(f"/orgs/{org_id}/documents/{doc['id']}/history")
    assert before.status_code == 200, before.text
    versions_before = before.json()["versions"]

    client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/reaffirm",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )

    after = client.get(f"/orgs/{org_id}/documents/{doc['id']}/history")
    assert after.json()["versions"] == versions_before


def test_reaffirm_does_not_detach_evidence_or_move_sprs(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)

    links_before = {
        (link.id, link.is_archived)
        for link in db_session.scalars(select(EvidenceStateLink)).all()
    }
    assert any(not archived for _id, archived in links_before), "fixture must have a live link"
    score_before = db_session.get(Assessment, d["assessment"].id).sprs_score

    client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/reaffirm",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    db_session.expire_all()

    links_after = {
        (link.id, link.is_archived)
        for link in db_session.scalars(select(EvidenceStateLink)).all()
    }
    assert links_after == links_before
    assert db_session.get(Assessment, d["assessment"].id).sprs_score == score_before


def test_reaffirm_refused_when_there_is_no_approved_version(
    client, db_session, fake_msp_admin
):
    """A draft has nothing to reaffirm.

    Allowing it would grant approval outside publish_document -- the only
    path that attaches evidence -- so this is 409, not a convenience.
    """
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    r = client.post(
        f"/orgs/{org_id}/documents",
        json={"doc_id": "AC-POL-002", "doc_type": "policy", "title": "Draft", "body": "x"},
    )
    doc = r.json()
    r = client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/reaffirm",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    assert r.status_code == 409, r.text
    assert "no approved version" in r.json()["detail"]


def test_reaffirm_rejects_a_contact_from_another_org(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    other = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)
    r = client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/reaffirm",
        json={"approved_by_contact_id": str(other["contact"].id)},
    )
    assert r.status_code == 422, r.text


def test_reaffirm_writes_an_audit_event_naming_the_approver(
    client, db_session, fake_msp_admin
):
    """The approver and the actor are both recorded, separately.

    The approver is in the context; who was authenticated is resolved onto
    audit_log's own actor columns by log_event. Section 2's point is that
    these are usually different people, so the test asserts the named
    approver is present rather than assuming the actor covers it.
    """
    from app.models import AuditLog

    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)
    client.post(
        f"/orgs/{d['org'].id}/documents/{doc['id']}/reaffirm",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    row = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "document.reaffirm")
    ).one()
    assert row.context["approver_name"] == "Jane Approver"
    assert row.context["approved_by_contact_id"] == str(d["contact"].id)


# ---------------------------------------------------------------------------
# The derived cadence verdict (sec 4)
# ---------------------------------------------------------------------------


def test_review_state_is_current_right_after_publishing(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)
    assert doc["review"]["status"] == "current"
    assert doc["review"]["next_due_at"] is not None


def test_a_document_with_no_approval_reads_never_approved(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)
    r = client.post(
        f"/orgs/{d['org'].id}/documents",
        json={"doc_id": "AC-POL-003", "doc_type": "policy", "title": "Unapproved", "body": "x"},
    )
    assert r.json()["review"]["status"] == "never_approved"


def test_overdue_appears_in_the_library_list_and_detail(
    client, db_session, fake_msp_admin
):
    """Section 4's "make overdue visible", asserted where an operator looks."""
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)

    detail = client.get(f"/orgs/{org_id}/documents/{doc['id']}").json()
    assert detail["review"]["status"] == "overdue"
    assert detail["review"]["days_until_due"] < 0

    listed = client.get(f"/orgs/{org_id}/documents").json()
    assert [x["review"]["status"] for x in listed] == ["overdue"]

    filtered = client.get(f"/orgs/{org_id}/documents?review_status=needs_attention").json()
    assert [x["id"] for x in filtered] == [doc["id"]]
    assert client.get(f"/orgs/{org_id}/documents?review_status=current").json() == []


def test_review_status_filter_rejects_an_unknown_value(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    r = client.get(f"/orgs/{d['org'].id}/documents?review_status=nonsense")
    assert r.status_code == 422


def test_overdue_does_not_detach_evidence_or_move_sprs(
    client, db_session, fake_msp_admin
):
    """Overdue flags; it never invalidates.

    The whole of section 4. Asserted after the document has actually gone
    overdue and been read as overdue, so this is not vacuous.
    """
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)

    links_before = {
        (link.id, link.is_archived, link.evidence_id, link.control_state_id)
        for link in db_session.scalars(select(EvidenceStateLink)).all()
    }
    evidence_before = {e.id for e in db_session.scalars(select(Evidence)).all()}
    score_before = db_session.get(Assessment, d["assessment"].id).sprs_score
    status_before = db_session.get(ControlState, d["cs_a"].id).status

    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)
    assert (
        client.get(f"/orgs/{org_id}/documents/{doc['id']}").json()["review"]["status"]
        == "overdue"
    )
    db_session.expire_all()

    assert {
        (link.id, link.is_archived, link.evidence_id, link.control_state_id)
        for link in db_session.scalars(select(EvidenceStateLink)).all()
    } == links_before
    assert {e.id for e in db_session.scalars(select(Evidence)).all()} == evidence_before
    assert db_session.get(Assessment, d["assessment"].id).sprs_score == score_before
    assert db_session.get(ControlState, d["cs_a"].id).status == status_before


def test_reaffirming_an_overdue_document_makes_it_current_again(
    client, db_session, fake_msp_admin
):
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)
    assert (
        client.get(f"/orgs/{org_id}/documents/{doc['id']}").json()["review"]["status"]
        == "overdue"
    )

    r = client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/reaffirm",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["review"]["status"] == "current"


def test_cadence_change_moves_the_due_date_without_a_new_approval(
    client, db_session, fake_msp_admin
):
    """Why there is no stored next_due_at.

    Same approval, shorter cadence, different verdict -- computed on read.
    A stored due date would still report current here.
    """
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=200)
    assert (
        client.get(f"/orgs/{org_id}/documents/{doc['id']}").json()["review"]["status"]
        == "current"
    )

    r = client.patch(f"/orgs/{org_id}/documents/{doc['id']}", json={"cadence_months": 3})
    assert r.status_code == 200, r.text
    assert r.json()["review"]["status"] == "overdue"


def test_approval_history_endpoint_lists_newest_first(client, db_session, fake_msp_admin):
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)
    client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/reaffirm",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    rows = client.get(f"/orgs/{org_id}/documents/{doc['id']}/approvals").json()
    assert [x["approval_type"] for x in rows] == ["reaffirmation", "initial"]
    assert all(x["version_number"] == 1 for x in rows)


# ---------------------------------------------------------------------------
# The scheduled digest (sec 3)
# ---------------------------------------------------------------------------


def test_digest_notifies_and_changes_no_document_state(
    client, db_session, fake_msp_admin, sent_emails
):
    """Section 7's "assert the negative" for the cadence job.

    Everything a clock must not touch is snapshotted before the run and
    compared after: version rows and their bodies/statuses, approval rows,
    evidence, evidence links, control state, and the SPRS score.
    """
    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)

    versions_before = {
        (v.id, v.body, v.status, v.approved_at)
        for v in db_session.scalars(select(DocumentVersion)).all()
    }
    approvals_before = {a.id for a in db_session.scalars(select(DocumentApproval)).all()}
    links_before = {
        (link.id, link.is_archived) for link in db_session.scalars(select(EvidenceStateLink)).all()
    }
    evidence_before = {e.id for e in db_session.scalars(select(Evidence)).all()}
    score_before = db_session.get(Assessment, d["assessment"].id).sprs_score
    cs_before = db_session.get(ControlState, d["cs_a"].id).status

    result = scheduler._document_review_digest(db_session)

    assert result["orgs_with_due"] >= 1
    assert result["documents_due"] >= 1
    assert result["notifications_sent"] >= 1
    assert len(sent_emails) >= 1

    db_session.expire_all()
    assert {
        (v.id, v.body, v.status, v.approved_at)
        for v in db_session.scalars(select(DocumentVersion)).all()
    } == versions_before
    assert {
        a.id for a in db_session.scalars(select(DocumentApproval)).all()
    } == approvals_before
    assert {
        (link.id, link.is_archived)
        for link in db_session.scalars(select(EvidenceStateLink)).all()
    } == links_before
    assert {e.id for e in db_session.scalars(select(Evidence)).all()} == evidence_before
    assert db_session.get(Assessment, d["assessment"].id).sprs_score == score_before
    assert db_session.get(ControlState, d["cs_a"].id).status == cs_before


def test_digest_body_names_no_document_or_count(
    client, db_session, fake_msp_admin, sent_emails
):
    """email_service.py's content rule: a nudge and a link, nothing more.

    Mail leaves the deployment's trust boundary, so the body must not name
    the org, the document, or how many are due. The count is recorded on
    the notification row instead, which this also checks.
    """
    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d, doc_id="AC-POL-010")
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)

    scheduler._document_review_digest(db_session)

    assert sent_emails, "expected at least one email"
    for _to, subject, body in sent_emails:
        assert "AC-POL-010" not in body and "AC-POL-010" not in subject
        assert "AC Policy" not in body
        assert d["org"].name not in body
        assert "https://wingrc.example.com" in body

    notification = db_session.scalars(select(DocumentReviewNotification)).first()
    assert notification is not None
    assert notification.document_count >= 1
    assert notification.notified_at is not None
    assert notification.notification_error is None


def test_digest_is_one_email_per_recipient_not_one_per_document(
    client, db_session, fake_msp_admin, sent_emails
):
    d = _seed(db_session, fake_msp_admin)
    for n in range(3):
        doc = _published_doc(client, db_session, d, doc_id=f"AC-POL-10{n}")
        _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)

    result = scheduler._document_review_digest(db_session)
    assert result["documents_due"] == 3

    recipients = [to for to, _s, _b in sent_emails]
    assert len(recipients) == len(set(recipients)), "one email per recipient, not per document"
    notifications = db_session.scalars(select(DocumentReviewNotification)).all()
    assert all(n.document_count == 3 for n in notifications)


def test_digest_notifies_the_named_approver_and_the_role_holder(
    client, db_session, fake_msp_admin, sent_emails
):
    """Both routing sources, section 3.

    The approver is told because the cadence is satisfied by their
    decision; the role holder is told because that is D.3's precedent and
    the reason a missing approver does not mean silence.
    """
    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)

    scheduler._document_review_digest(db_session)
    recipients = {to for to, _s, _b in sent_emails}
    assert "jane@example.com" in recipients, "the named approver must be told"
    assert "sam@example.com" in recipients, "the security_officer must be told"


def test_digest_reports_when_nobody_can_be_notified(
    client, db_session, fake_msp_admin, sent_emails
):
    """Section 7: "no contact holds that role" is reported, not swallowed.

    The approver contact is deleted and the role removed, so there is
    genuinely nobody -- and the job must count it rather than skipping the
    org silently. That exact bug has been fixed twice in this codebase.
    """
    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)

    db_session.execute(
        ContactDocumentationRole.__table__.delete().where(
            ContactDocumentationRole.contact_id.in_(
                [d["officer"].id, d["contact"].id]
            )
        )
    )
    # Deleting the approver contact sets document_approval.approved_by_contact_id
    # to NULL (ON DELETE SET NULL) while approver_name survives -- which is
    # the point of denormalizing it.
    db_session.delete(db_session.get(Contact, d["contact"].id))
    db_session.delete(db_session.get(Contact, d["officer"].id))
    db_session.commit()

    result = scheduler._document_review_digest(db_session)
    assert result["orgs_with_no_contact"] == 1
    assert result["notifications_sent"] == 0
    assert sent_emails == []

    approval = db_session.scalars(select(DocumentApproval)).first()
    assert approval.approved_by_contact_id is None
    assert approval.approver_name == "Jane Approver"


def test_digest_records_a_delivery_failure_rather_than_reporting_success(
    client, db_session, fake_msp_admin, monkeypatch
):
    """A failed send must leave a trace -- the wl-util-1 lesson, not
    re-learned."""

    def _failing_send(session, *, to, subject, body, template):
        return EmailSendResult(sent=False, error="SMTP unavailable")

    monkeypatch.setattr(scheduler.email_service, "send", _failing_send)

    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)

    result = scheduler._document_review_digest(db_session)
    assert result["notifications_sent"] == 0
    assert result["errors"] >= 1
    rows = db_session.scalars(select(DocumentReviewNotification)).all()
    assert rows
    assert all(r.notified_at is None for r in rows)
    assert all(r.notification_error == "SMTP unavailable" for r in rows)


def test_digest_skips_orgs_with_nothing_due(
    client, db_session, fake_msp_admin, sent_emails
):
    d = _seed(db_session, fake_msp_admin)
    _published_doc(client, db_session, d)  # current, not due

    result = scheduler._document_review_digest(db_session)
    assert result["orgs_with_due"] == 0
    assert sent_emails == []


def test_digest_job_is_registered(client, db_session, fake_msp_admin):
    """Registered, not just defined -- an unregistered job never runs."""
    assert "document_review_digest" in scheduler.JOB_REGISTRY
    assert scheduler.JOB_REGISTRY["document_review_digest"].interval == timedelta(hours=24)


# ---------------------------------------------------------------------------
# Roles and tenant isolation (sec 7)
# ---------------------------------------------------------------------------


def test_c3pao_assessor_can_read_approvals_and_cadence_but_cannot_approve(
    db_session, fake_msp_admin
):
    """One test, because the pair is the point: read yes, write no."""
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id

    app_ = _app()
    app_.dependency_overrides[get_session] = _app_session(db_session)
    app_.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    admin = TestClient(app_)
    doc = _published_doc(admin, db_session, d)
    app_.dependency_overrides.clear()

    assessor = _make_fake_user(role="c3pao_assessor", email="assessor@example.com")
    _grant(db_session, assessor, org_id=org_id, role="c3pao_assessor")
    db_session.commit()

    app_.dependency_overrides[get_session] = _app_session(db_session)
    app_.dependency_overrides[get_current_user] = _authed(db_session, assessor)
    client = TestClient(app_)
    try:
        r = client.get(f"/orgs/{org_id}/documents/{doc['id']}/approvals")
        assert r.status_code == 200, r.text
        assert [x["approval_type"] for x in r.json()] == ["initial"]

        detail = client.get(f"/orgs/{org_id}/documents/{doc['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["review"]["status"] == "current"

        blocked = client.post(
            f"/orgs/{org_id}/documents/{doc['id']}/reaffirm",
            json={"approved_by_contact_id": str(d["contact"].id)},
        )
        assert blocked.status_code == 403, blocked.text

        blocked_publish = client.post(
            f"/orgs/{org_id}/documents/{doc['id']}/publish",
            json={"approved_by_contact_id": str(d["contact"].id)},
        )
        assert blocked_publish.status_code == 403, blocked_publish.text
    finally:
        app_.dependency_overrides.clear()


def test_no_cross_org_document_or_approval_is_reachable(db_session, fake_msp_admin):
    """RLS through real HTTP, not a direct query.

    A user with membership in org A must not reach org B's document or its
    approvals even by guessing the id -- and the check is the *response*,
    not the presence of a policy.
    """
    owner = _make_fake_user(email="owner-a@example.com")
    d_a = _seed(db_session, owner)
    d_b = _seed(db_session, fake_msp_admin)

    app_ = _app()
    app_.dependency_overrides[get_session] = _app_session(db_session)
    app_.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    client_b = TestClient(app_)
    doc_b = _published_doc(client_b, db_session, d_b)
    app_.dependency_overrides.clear()

    app_.dependency_overrides[get_session] = _app_session(db_session)
    app_.dependency_overrides[get_current_user] = _authed(db_session, owner)
    client_a = TestClient(app_)
    try:
        # Org A's own id in the path, org B's document id -- the shape that
        # would leak if scoping were only by path org_id.
        r = client_a.get(f"/orgs/{d_a['org'].id}/documents/{doc_b['id']}")
        assert r.status_code == 404, r.text
        r = client_a.get(f"/orgs/{d_a['org'].id}/documents/{doc_b['id']}/approvals")
        assert r.status_code == 404, r.text
        r = client_a.post(
            f"/orgs/{d_a['org'].id}/documents/{doc_b['id']}/reaffirm",
            json={"approved_by_contact_id": str(d_a["contact"].id)},
        )
        assert r.status_code == 404, r.text

        # And org B's own path is refused outright -- no membership.
        r = client_a.get(f"/orgs/{d_b['org'].id}/documents/{doc_b['id']}")
        assert r.status_code == 403, r.text

        listed = client_a.get(f"/orgs/{d_a['org'].id}/documents").json()
        assert doc_b["id"] not in [x["id"] for x in listed]
    finally:
        app_.dependency_overrides.clear()


def test_dashboard_surfaces_overdue_documents(client, db_session, fake_msp_admin):
    """Section 4's "somewhere an engineer running an assessment will see it"."""
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)

    r = client.get(f"/orgs/{org_id}/assessments/{d['assessment'].id}/dashboard")
    assert r.status_code == 200, r.text
    widget = r.json()["document_reviews"]
    assert widget["overdue_count"] == 1
    assert widget["due_soon_count"] == 0
    assert [i["doc_id"] for i in widget["items"]] == ["AC-POL-001"]
    assert widget["items"][0]["status"] == "overdue"


def test_publishing_a_new_version_resets_the_cadence(client, db_session, fake_msp_admin):
    """A new approved version is a fresh approval, so the clock restarts.

    Also checks the superseded version keeps its own initial approval --
    approval attaches to a version, and history is not rewritten when a
    later version supersedes it.
    """
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    doc = _published_doc(client, db_session, d)
    v1_id = uuid.UUID(doc["current_version"]["id"])
    _backdate_approvals(db_session, uuid.UUID(doc["id"]), days=400)

    r = client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/versions",
        json={"body": BODY + " Revised.", "base_version_id": str(v1_id)},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        f"/orgs/{org_id}/documents/{doc['id']}/publish",
        json={"approved_by_contact_id": str(d["contact"].id)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["review"]["status"] == "current"

    approvals = db_session.scalars(
        select(DocumentApproval).where(
            DocumentApproval.document_id == uuid.UUID(doc["id"])
        )
    ).all()
    assert sorted(a.approval_type for a in approvals) == ["initial", "initial"]
    assert {a.document_version_id for a in approvals} >= {v1_id}
    assert db_session.get(DocumentVersion, v1_id).status == "superseded"


def test_add_months_is_the_same_function_the_api_uses(client, db_session, fake_msp_admin):
    """The API's next_due_at agrees with the pure helper.

    Cheap, but it is what stops the router growing its own date arithmetic
    later -- which is how two implementations of "when is this due" begin.
    """
    d = _seed(db_session, fake_msp_admin)
    doc = _published_doc(client, db_session, d)
    version = db_session.get(DocumentVersion, uuid.UUID(doc["current_version"]["id"]))
    expected = add_months(version.approved_at, doc["cadence_months"])
    assert doc["review"]["next_due_at"].startswith(expected.strftime("%Y-%m-%dT%H:%M"))
