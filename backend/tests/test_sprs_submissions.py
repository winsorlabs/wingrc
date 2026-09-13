"""Integration tests for SPRS submission record-keeping (§1/§2/§5/§6 of
the "record of SPRS submissions" task): the router
(routers/sprs_submissions.py), the DB adapter (sprs_submissions.py), and
the bundle-export/recompute interactions the design explicitly calls out.

Covers:
  - Recording, listing (newest first), and reading "current"
  - "current" returns None for an org with no submission -- never an error
  - Voiding: one-way, the row survives, "current" falls back correctly
  - Contact deletion does NOT destroy the submission record (the central
    contact-lifecycle invariant this table exists to get right)
  - A later SPRS recompute does not alter a recorded submission's score
  - RBAC: read-only role can read, cannot record/void
  - Score range validation
  - The submission appears in a bundle snapshot, captured at export time

Run in-container:
    docker compose exec backend pytest tests/test_sprs_submissions.py -m integration -v
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user
from app.bundle_service import snapshot_bundle
from app.db import get_session
from app.engine import recompute_sprs, start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    Contact,
    Control,
    Framework,
    Organization,
    SprsSubmission,
)
from app.storage import NullStorageClient
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_msp_admin():
    return _make_fake_user()


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _client_as(db_session, role: str, *, org_id: uuid.UUID | None = None):
    user = _make_fake_user(role=role)
    if org_id is not None:
        _grant(db_session, user, org_id=org_id)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


def _seed_org(db_session, fake_msp_admin) -> Organization:
    org = Organization(id=fake_msp_admin.org_id, name=f"SprsSubOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    _grant(db_session, fake_msp_admin)
    return org


def _seed_contact(db_session, org_id: uuid.UUID, **overrides) -> Contact:
    defaults = dict(
        org_id=org_id,
        name="Jane Filer",
        email=f"jane-{uuid.uuid4().hex[:6]}@example.com",
        affiliation="customer",
    )
    defaults.update(overrides)
    contact = Contact(**defaults)
    db_session.add(contact)
    db_session.flush()
    return contact


def _url(org_id: uuid.UUID, *parts: str) -> str:
    return "/".join([f"/orgs/{org_id}/sprs-submissions", *parts])


# ---------------------------------------------------------------------------
# Record / list / current
# ---------------------------------------------------------------------------


def test_record_submission_returns_it_and_denormalizes_submitter(
    client, db_session, fake_msp_admin
):
    org = _seed_org(db_session, fake_msp_admin)
    contact = _seed_contact(db_session, org.id, name="Jane Filer", email="jane@example.com")

    r = client.post(
        _url(org.id),
        json={
            "score": 87,
            "submitted_date": "2026-03-01",
            "submitted_by_contact_id": str(contact.id),
            "note": "Filed by the customer's compliance officer",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["score"] == 87
    assert body["submitted_date"] == "2026-03-01"
    assert body["submitted_by_name"] == "Jane Filer"
    assert body["submitted_by_email"] == "jane@example.com"
    assert body["voided_at"] is None


def test_unknown_contact_rejected(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)
    r = client.post(
        _url(org.id),
        json={
            "score": 87,
            "submitted_date": "2026-03-01",
            "submitted_by_contact_id": str(uuid.uuid4()),
        },
    )
    assert r.status_code == 422


@pytest.mark.parametrize("score", [-205, 111])
def test_score_out_of_range_rejected(client, db_session, fake_msp_admin, score):
    org = _seed_org(db_session, fake_msp_admin)
    contact = _seed_contact(db_session, org.id)
    r = client.post(
        _url(org.id),
        json={
            "score": score,
            "submitted_date": "2026-03-01",
            "submitted_by_contact_id": str(contact.id),
        },
    )
    assert r.status_code == 422


def test_current_is_none_for_an_org_with_no_submission(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)
    r = client.get(_url(org.id, "current"))
    assert r.status_code == 200
    assert r.json() is None


def test_current_is_the_most_recent_by_submitted_date(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)
    contact = _seed_contact(db_session, org.id)
    for d in ("2025-03-01", "2026-03-01", "2024-03-01"):
        client.post(
            _url(org.id),
            json={"score": 50, "submitted_date": d, "submitted_by_contact_id": str(contact.id)},
        )

    r = client.get(_url(org.id, "current"))
    assert r.status_code == 200
    assert r.json()["submitted_date"] == "2026-03-01"


def test_history_lists_newest_first(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)
    contact = _seed_contact(db_session, org.id)
    for d in ("2024-03-01", "2026-03-01", "2025-03-01"):
        client.post(
            _url(org.id),
            json={"score": 50, "submitted_date": d, "submitted_by_contact_id": str(contact.id)},
        )

    r = client.get(_url(org.id))
    assert r.status_code == 200
    dates = [row["submitted_date"] for row in r.json()]
    assert dates == ["2026-03-01", "2025-03-01", "2024-03-01"]


# ---------------------------------------------------------------------------
# Void: one-way, row survives
# ---------------------------------------------------------------------------


def test_void_marks_row_but_keeps_it_in_history(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)
    contact = _seed_contact(db_session, org.id)
    created = client.post(
        _url(org.id),
        json={
            "score": 87,
            "submitted_date": "2026-03-01",
            "submitted_by_contact_id": str(contact.id),
        },
    ).json()

    r = client.post(_url(org.id, created["id"], "void"), json={"reason": "wrong score entered"})
    assert r.status_code == 200
    voided = r.json()
    assert voided["voided_at"] is not None
    assert voided["voided_reason"] == "wrong score entered"
    # The filed facts themselves are untouched by voiding.
    assert voided["score"] == 87
    assert voided["submitted_date"] == "2026-03-01"

    history = client.get(_url(org.id)).json()
    assert len(history) == 1
    assert history[0]["id"] == created["id"]


def test_current_falls_back_to_next_non_voided_after_a_void(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)
    contact = _seed_contact(db_session, org.id)
    older = client.post(
        _url(org.id),
        json={
            "score": 50,
            "submitted_date": "2025-01-01",
            "submitted_by_contact_id": str(contact.id),
        },
    ).json()
    newer = client.post(
        _url(org.id),
        json={
            "score": 60,
            "submitted_date": "2026-01-01",
            "submitted_by_contact_id": str(contact.id),
        },
    ).json()

    assert client.get(_url(org.id, "current")).json()["id"] == newer["id"]

    client.post(_url(org.id, newer["id"], "void"), json={"reason": "duplicate entry"})

    current = client.get(_url(org.id, "current")).json()
    assert current["id"] == older["id"]


def test_voiding_twice_is_rejected(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)
    contact = _seed_contact(db_session, org.id)
    created = client.post(
        _url(org.id),
        json={
            "score": 87,
            "submitted_date": "2026-03-01",
            "submitted_by_contact_id": str(contact.id),
        },
    ).json()
    client.post(_url(org.id, created["id"], "void"), json={"reason": "first void"})

    r = client.post(_url(org.id, created["id"], "void"), json={"reason": "second void"})
    assert r.status_code == 409


def test_void_unknown_submission_404s(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)
    r = client.post(_url(org.id, str(uuid.uuid4()), "void"), json={"reason": "n/a"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Contact deletion must not destroy the submission record
# ---------------------------------------------------------------------------


def test_contact_deletion_does_not_destroy_the_submission_record(
    client, db_session, fake_msp_admin
):
    org = _seed_org(db_session, fake_msp_admin)
    contact = _seed_contact(db_session, org.id, name="Departing Filer", email="gone@example.com")

    created = client.post(
        _url(org.id),
        json={
            "score": 92,
            "submitted_date": "2026-03-01",
            "submitted_by_contact_id": str(contact.id),
            "note": "Filed before the contact left",
        },
    ).json()

    db_session.delete(contact)
    db_session.flush()

    row = db_session.get(SprsSubmission, uuid.UUID(created["id"]))
    assert row is not None, "the submission row must survive the contact's deletion"
    assert row.submitted_by_contact_id is None  # FK went to NULL, not CASCADE
    assert row.submitted_by_name == "Departing Filer"  # denormalized identity survives
    assert row.submitted_by_email == "gone@example.com"
    assert row.score == 92
    assert row.note == "Filed before the contact left"

    # Still reachable through the ordinary API too.
    r = client.get(_url(org.id, "current"))
    assert r.status_code == 200
    assert r.json()["submitted_by_name"] == "Departing Filer"


# ---------------------------------------------------------------------------
# Recompute must never alter a recorded submission's score
# ---------------------------------------------------------------------------


def test_sprs_recompute_does_not_alter_a_recorded_submission(client, db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)
    fw = Framework(key=f"fw-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC", title="Access Control",
        requirement_text="req", sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="obj a")
    db_session.add(obj)
    db_session.flush()
    assessment = start_assessment(db_session, org_id=org.id, framework_id=fw.id, name="A1")
    db_session.commit()

    contact = _seed_contact(db_session, org.id)
    created = client.post(
        _url(org.id),
        json={
            "score": 110,  # deliberately does not match what WinGRC would compute
            "submitted_date": "2026-03-01",
            "submitted_by_contact_id": str(contact.id),
            "assessment_id": str(assessment.id),
        },
    ).json()

    # Force a real recompute -- the live score for this fresh, all-not_met
    # assessment is 110 - 5 = 105, deliberately different from the 110
    # recorded above.
    live_score = recompute_sprs(db_session, assessment.id)
    db_session.commit()
    assert live_score != 110

    row = db_session.get(SprsSubmission, uuid.UUID(created["id"]))
    assert row.score == 110, "a live recompute must never retroactively change a filed score"


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def test_read_only_role_can_read_but_not_record(db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)

    ro_client = _client_as(db_session, "c3pao_assessor", org_id=org.id)
    r = ro_client.get(_url(org.id))
    assert r.status_code == 200

    contact = _seed_contact(db_session, org.id)
    r = ro_client.post(
        _url(org.id),
        json={
            "score": 87,
            "submitted_date": "2026-03-01",
            "submitted_by_contact_id": str(contact.id),
        },
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Bundle inclusion -- captured into the snapshot, not dereferenced live
# ---------------------------------------------------------------------------


def test_submission_appears_in_bundle_snapshot(db_session, fake_msp_admin):
    org = _seed_org(db_session, fake_msp_admin)
    fw = Framework(key=f"fw-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    assessment = start_assessment(db_session, org_id=org.id, framework_id=fw.id, name="A1")
    db_session.commit()

    contact = _seed_contact(db_session, org.id, name="Bundle Filer")
    db_session.add(
        SprsSubmission(
            id=uuid.uuid4(),
            org_id=org.id,
            score=101,
            submitted_date=date(2026, 3, 1),
            submitted_by_contact_id=contact.id,
            submitted_by_name="Bundle Filer",
            submitted_by_email=contact.email,
        )
    )
    db_session.commit()

    snapshot = snapshot_bundle(db_session, NullStorageClient(), org.id, assessment.id)

    assert len(snapshot.sprs_submissions) == 1
    snap = snapshot.sprs_submissions[0]
    assert snap.score == 101
    assert snap.submitted_by_name == "Bundle Filer"
    assert snap.voided is False


def test_voided_submission_still_appears_in_bundle_history_marked_voided(
    db_session, fake_msp_admin
):
    org = _seed_org(db_session, fake_msp_admin)
    fw = Framework(key=f"fw-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    assessment = start_assessment(db_session, org_id=org.id, framework_id=fw.id, name="A1")
    db_session.commit()

    db_session.add(
        SprsSubmission(
            id=uuid.uuid4(),
            org_id=org.id,
            score=101,
            submitted_date=date(2026, 3, 1),
            submitted_by_name="Bundle Filer",
            voided_at=datetime.now(UTC),
            voided_reason="mistake",
        )
    )
    db_session.commit()

    snapshot = snapshot_bundle(db_session, NullStorageClient(), org.id, assessment.id)

    assert len(snapshot.sprs_submissions) == 1
    assert snapshot.sprs_submissions[0].voided is True
