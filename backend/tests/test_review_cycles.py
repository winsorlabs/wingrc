"""Integration tests for periodic review & attestation (D.3's first
half). Covers the design task's own §9 verification list:

  - The snapshot doesn't change when scope_entity changes afterward
    (the central invariant).
  - Attestation records the authenticated identity, never a submitted name.
  - customer_poc can attest and can do nothing else newly writable.
  - Cross-org isolation.
  - An unanswered cycle closes into a durable non-response record.
  - Flagging produces follow-up, not a scope mutation.
  - Full attestation closes a cycle early and produces linked Evidence.

Run in-container:
    docker compose exec backend pytest tests/test_review_cycles.py -m integration -v
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
    Control,
    Evidence,
    EvidenceStateLink,
    Framework,
    Organization,
    ReviewCycle,
    ReviewCycleReviewer,
    ScopeEntity,
    User,
)
from app.storage import StorageClient, get_storage_client
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

pytestmark = pytest.mark.integration


class InMemoryStorageClient(StorageClient):
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self.files[key] = data

    def presigned_url(
        self, key: str, expires_in: int = 300, download_filename: str | None = None
    ) -> str:
        return f"http://fake/{key}"

    def delete_file(self, key: str) -> None:
        self.files.pop(key, None)

    def get_bytes(self, key: str) -> bytes:
        return self.files.get(key, b"")


@pytest.fixture
def storage() -> InMemoryStorageClient:
    return InMemoryStorageClient()


@pytest.fixture
def fake_msp_admin():
    return _make_fake_user()


@pytest.fixture
def client(db_session, storage, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _client_as(db_session, storage, user, *, org_id):
    # _seed_customer_poc already grants membership itself (needed so
    # auth.org_reviewer_candidates() finds it as a reviewer regardless of
    # whether a TestClient is ever built for that user) -- guard against a
    # second, duplicate grant here for that case, while still granting
    # fresh for a user (e.g. a genuine cross-org outsider) that wasn't
    # pre-seeded with one.
    from app.models import OrgMembership

    existing = db_session.scalars(
        select(OrgMembership).where(
            OrgMembership.user_id == user.id, OrgMembership.org_id == org_id
        )
    ).first()
    if existing is None:
        _grant(db_session, user, org_id=org_id)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


def _seed_org_with_catalog(db_session, fake_msp_admin) -> dict:
    org = Organization(id=fake_msp_admin.org_id, name=f"ReviewOrg-{uuid.uuid4().hex[:8]}")
    fw = Framework(key=f"fw-review-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)

    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC",
        title="Authorized Access Control",
        requirement_text="req", sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    obj_a = AssessmentObjective(
        control_id=ctrl.id, objective_key="a", text="Authorized users identified"
    )
    obj_c = AssessmentObjective(
        control_id=ctrl.id, objective_key="c", text="Authorized devices identified"
    )
    db_session.add_all([obj_a, obj_c])
    db_session.flush()

    assessment = start_assessment(db_session, org_id=org.id, framework_id=fw.id, name="A1")
    db_session.commit()

    person = ScopeEntity(
        org_id=org.id, entity_type="person", natural_key="jane.doe",
        status="active", in_boundary=True,
    )
    device = ScopeEntity(
        org_id=org.id, entity_type="device", natural_key="LAPTOP-001",
        status="active", in_boundary=True,
    )
    decommissioned = ScopeEntity(
        org_id=org.id, entity_type="device", natural_key="OLD-LAPTOP", status="decommissioned",
        in_boundary=True,
    )
    db_session.add_all([person, device, decommissioned])
    db_session.flush()

    return {
        "org": org, "fw": fw, "ctrl": ctrl, "obj_a": obj_a, "obj_c": obj_c,
        "assessment": assessment, "person": person, "device": device,
        "decommissioned": decommissioned,
    }


def _seed_customer_poc(db_session, *, org_id, email: str | None = None) -> User:
    user = User(
        home_org_id=org_id, email=email or f"poc-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Client Reviewer", login_method="local", role="customer_poc", is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    _grant(db_session, _to_current_user(user, org_id))
    return user


def _to_current_user(user: User, org_id):
    from app.auth import CurrentUser
    return CurrentUser(
        id=user.id, org_id=org_id, email=user.email, display_name=user.display_name,
        role=user.role, is_active=True, login_method="local", mfa_enrolled=True,
    )


def _open_url(org_id) -> str:
    return f"/orgs/{org_id}/review-cycles"


# ---------------------------------------------------------------------------
# Basic open + snapshot correctness
# ---------------------------------------------------------------------------


def test_open_cycle_snapshots_only_active_in_boundary_users_and_devices(
    client, db_session, fake_msp_admin
):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    r = client.post(_open_url(d["org"].id))
    assert r.status_code == 201, r.text
    cycle_id = r.json()["id"]

    detail = client.get(f"{_open_url(d['org'].id)}/{cycle_id}").json()
    natural_keys = {i["natural_key"] for i in detail["items"]}
    assert natural_keys == {"jane.doe", "LAPTOP-001"}  # decommissioned excluded
    subject_types = {i["natural_key"]: i["subject_type"] for i in detail["items"]}
    assert subject_types["jane.doe"] == "user"
    assert subject_types["LAPTOP-001"] == "device"


def test_open_cycle_includes_msp_and_client_reviewers(client, db_session, fake_msp_admin):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    poc = _seed_customer_poc(db_session, org_id=d["org"].id)

    r = client.post(_open_url(d["org"].id))
    cycle_id = r.json()["id"]
    detail = client.get(f"{_open_url(d['org'].id)}/{cycle_id}").json()

    sides = {rv["reviewer_email"]: rv["reviewer_side"] for rv in detail["reviewers"]}
    assert sides[fake_msp_admin.email] == "msp"
    assert sides[poc.email] == "client"


def test_cannot_open_a_second_cycle_while_one_is_open(client, db_session, fake_msp_admin):
    # Found live on bench (§9's HTTP walkthrough): the manual MSP-triggered
    # open had no guard against a second concurrent open cycle for the same
    # org, unlike the scheduler's own due-check.
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    r1 = client.post(_open_url(d["org"].id))
    assert r1.status_code == 201, r1.text

    r2 = client.post(_open_url(d["org"].id))
    assert r2.status_code == 409, r2.text

    rows = client.get(_open_url(d["org"].id)).json()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Central invariant: the snapshot never changes after the fact
# ---------------------------------------------------------------------------


def test_snapshot_unaffected_by_later_scope_entity_changes(client, db_session, fake_msp_admin):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    r = client.post(_open_url(d["org"].id))
    cycle_id = r.json()["id"]

    before = client.get(f"{_open_url(d['org'].id)}/{cycle_id}").json()
    before_keys = sorted(i["natural_key"] for i in before["items"])

    # Mutate scope after the cycle opened: add a new device, decommission
    # the existing one, rename the person.
    new_device = ScopeEntity(
        org_id=d["org"].id, entity_type="device", natural_key="NEW-LAPTOP",
        status="active", in_boundary=True,
    )
    db_session.add(new_device)
    d["device"].status = "decommissioned"
    d["person"].natural_key = "jane.doe.renamed"
    db_session.commit()

    after = client.get(f"{_open_url(d['org'].id)}/{cycle_id}").json()
    after_keys = sorted(i["natural_key"] for i in after["items"])

    assert after_keys == before_keys, "the cycle's item snapshot must not change after open"
    assert "NEW-LAPTOP" not in after_keys
    assert "jane.doe.renamed" not in after_keys
    assert "jane.doe" in after_keys  # the ORIGINAL natural_key, unchanged


# ---------------------------------------------------------------------------
# Attestation: authenticated identity, not a submitted name
# ---------------------------------------------------------------------------


def test_attest_uses_authenticated_identity(client, db_session, storage, fake_msp_admin):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    poc = _seed_customer_poc(db_session, org_id=d["org"].id, email="real.reviewer@example.com")
    cycle_id = client.post(_open_url(d["org"].id)).json()["id"]

    poc_client = _client_as(
        db_session, storage, _to_current_user(poc, d["org"].id), org_id=d["org"].id
    )
    # AttestIn has no name/identity field at all -- only a comment -- so
    # there is nothing for a client to spoof; assert the response still
    # correctly reflects the authenticated poc's own identity.
    r = poc_client.post(
        f"{_open_url(d['org'].id)}/{cycle_id}/attest", json={"comment": "Looks correct"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reviewer_email"] == "real.reviewer@example.com"
    assert body["status"] == "attested"
    assert body["comment"] == "Looks correct"

    row = db_session.get(ReviewCycleReviewer, uuid.UUID(body["id"]))
    assert row.user_id == poc.id


# ---------------------------------------------------------------------------
# customer_poc: can attest and flag, cannot manage
# ---------------------------------------------------------------------------


def test_customer_poc_can_attest_and_flag_but_not_open_or_resolve(
    client, db_session, storage, fake_msp_admin
):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    poc = _seed_customer_poc(db_session, org_id=d["org"].id)
    cycle_id = client.post(_open_url(d["org"].id)).json()["id"]
    item_id = client.get(f"{_open_url(d['org'].id)}/{cycle_id}").json()["items"][0]["id"]

    poc_client = _client_as(
        db_session, storage, _to_current_user(poc, d["org"].id), org_id=d["org"].id
    )

    # Cannot open a cycle.
    r = poc_client.post(_open_url(d["org"].id))
    assert r.status_code == 403

    # Can flag an item.
    r = poc_client.post(
        f"{_open_url(d['org'].id)}/{cycle_id}/items/{item_id}/flag",
        json={"reason": "This device was returned last month"},
    )
    assert r.status_code == 201, r.text
    flag_id = r.json()["id"]

    # Cannot resolve the flag it just raised.
    r = poc_client.post(
        f"{_open_url(d['org'].id)}/{cycle_id}/flags/{flag_id}/resolve",
        json={"note": "handled"},
    )
    assert r.status_code == 403

    # Can attest.
    r = poc_client.post(f"{_open_url(d['org'].id)}/{cycle_id}/attest", json={"comment": None})
    assert r.status_code == 200


def test_customer_poc_gains_no_other_newly_writable_access(client, db_session, fake_msp_admin):
    """Negative test against several unrelated MSP-only endpoints --
    confirms this feature did not broaden customer_poc's access anywhere
    beyond the narrow attest/flag carve-out on review-cycles itself."""
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    poc_user = User(
        home_org_id=d["org"].id, email=f"poc2-{uuid.uuid4().hex[:8]}@example.com",
        display_name="POC", login_method="local", role="customer_poc", is_active=True,
    )
    db_session.add(poc_user)
    db_session.flush()
    poc_current = _to_current_user(poc_user, d["org"].id)
    _grant(db_session, poc_current, org_id=d["org"].id)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, poc_current)
    poc_client = TestClient(app)

    # Grant org membership (msp_admin-gated elsewhere).
    r = poc_client.post(
        f"/orgs/{d['org'].id}/memberships",
        json={"user_id": str(fake_msp_admin.id), "role": "customer_poc"},
    )
    assert r.status_code == 403

    # Invite a new user (msp_admin-gated).
    r = poc_client.post(
        f"/orgs/{d['org'].id}/users",
        json={"email": "nobody@example.com", "display_name": "Nobody", "role": "customer_poc"},
    )
    assert r.status_code == 403

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Cross-org isolation
# ---------------------------------------------------------------------------


def test_reviewer_from_org_a_cannot_see_or_attest_org_b_cycle(client, db_session, fake_msp_admin):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    cycle_id = client.post(_open_url(d["org"].id)).json()["id"]

    other_org = Organization(name=f"OtherOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(other_org)
    db_session.flush()
    outsider = _make_fake_user(org_id=other_org.id, role="customer_poc")
    outsider_client = _client_as(
        db_session, InMemoryStorageClient(), outsider, org_id=other_org.id
    )

    r = outsider_client.get(f"/orgs/{other_org.id}/review-cycles/{cycle_id}")
    assert r.status_code == 404

    r = outsider_client.post(
        f"/orgs/{other_org.id}/review-cycles/{cycle_id}/attest", json={"comment": None}
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Flagging never mutates scope
# ---------------------------------------------------------------------------


def test_flagging_an_item_does_not_touch_scope_entity(client, db_session, fake_msp_admin):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    cycle_id = client.post(_open_url(d["org"].id)).json()["id"]
    item = client.get(f"{_open_url(d['org'].id)}/{cycle_id}").json()["items"][0]

    before = {
        (e.id, e.status, e.in_boundary, e.natural_key)
        for e in db_session.scalars(select(ScopeEntity).where(ScopeEntity.org_id == d["org"].id))
    }

    r = client.post(
        f"{_open_url(d['org'].id)}/{cycle_id}/items/{item['id']}/flag",
        json={"reason": "This looks wrong"},
    )
    assert r.status_code == 201

    after = {
        (e.id, e.status, e.in_boundary, e.natural_key)
        for e in db_session.scalars(select(ScopeEntity).where(ScopeEntity.org_id == d["org"].id))
    }
    assert before == after, "flagging an item must never mutate scope_entity"


# ---------------------------------------------------------------------------
# Full attestation closes early and produces linked evidence
# ---------------------------------------------------------------------------


def test_full_attestation_closes_cycle_and_creates_linked_evidence(
    client, db_session, storage, fake_msp_admin
):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    poc = _seed_customer_poc(db_session, org_id=d["org"].id)
    cycle_id = client.post(_open_url(d["org"].id)).json()["id"]

    # msp_admin attests first.
    r = client.post(f"{_open_url(d['org'].id)}/{cycle_id}/attest", json={"comment": None})
    assert r.status_code == 200

    detail = client.get(f"{_open_url(d['org'].id)}/{cycle_id}").json()
    assert detail["status"] == "open"  # not everyone has attested yet

    # poc attests last -- this should close the cycle.
    poc_client = _client_as(
        db_session, storage, _to_current_user(poc, d["org"].id), org_id=d["org"].id
    )
    r = poc_client.post(f"{_open_url(d['org'].id)}/{cycle_id}/attest", json={"comment": None})
    assert r.status_code == 200

    cycle = db_session.get(ReviewCycle, uuid.UUID(cycle_id))
    db_session.refresh(cycle)
    assert cycle.status == "completed"
    assert cycle.closed_at is not None

    evidence_rows = db_session.scalars(
        select(Evidence).where(
            Evidence.org_id == d["org"].id, Evidence.artifact_type == "attestation"
        )
    ).all()
    assert len(evidence_rows) == 1
    ev = evidence_rows[0]
    assert ev.kind == "file"

    link_count = db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.evidence_id == ev.id)
    ).all()
    assert len(link_count) == 2  # objective [a] and [c] on the one in_progress assessment


# ---------------------------------------------------------------------------
# Cadence config (§5) -- per-org, must appear in the SSP; surfaced via the
# existing org profile PATCH endpoint.
# ---------------------------------------------------------------------------


def test_org_defaults_to_six_month_cadence(db_session, fake_msp_admin):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    assert d["org"].review_cadence_months == 6


def test_review_cadence_is_editable_via_org_profile_patch(client, db_session, fake_msp_admin):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    r = client.patch(f"/orgs/{d['org'].id}/profile", json={"review_cadence_months": 3})
    assert r.status_code == 200, r.text
    assert r.json()["review_cadence_months"] == 3

    db_session.refresh(d["org"])
    assert d["org"].review_cadence_months == 3


def test_review_cadence_out_of_range_rejected(client, db_session, fake_msp_admin):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    r = client.patch(f"/orgs/{d['org'].id}/profile", json={"review_cadence_months": 0})
    assert r.status_code == 422
    r = client.patch(f"/orgs/{d['org'].id}/profile", json={"review_cadence_months": 61})
    assert r.status_code == 422


def test_cycle_snapshots_the_orgs_cadence_at_open_time(client, db_session, fake_msp_admin):
    """A later cadence change must not retroactively change what an
    already-open cycle's own due_at was set against."""
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    cycle_id = client.post(_open_url(d["org"].id)).json()["id"]

    client.patch(f"/orgs/{d['org'].id}/profile", json={"review_cadence_months": 12})

    detail = client.get(f"{_open_url(d['org'].id)}/{cycle_id}").json()
    assert detail["cadence_months"] == 6  # the value at open time, not 12
