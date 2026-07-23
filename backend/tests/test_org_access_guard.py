"""Proves the cross-tenant org-ownership guard (require_org_access) actually
closes the gap: an authenticated user from Org A must not be able to read or
mutate Org B's data via assessments.py, evidence.py, contacts.py, orgs.py, or
bundle.py — the five routers that were previously authentication-only
(Depends(get_current_user), no check that the caller's own org_id matched the
org_id in the URL path).

Each router gets:
  - a 403 test for a GET/list endpoint against another org
  - a 403 test for a mutating endpoint against another org (where one exists;
    bundle.py has none — it's a single GET route)
  - a same-org sanity check, proving the fix isn't over-broad (the caller's
    own org still works normally)

frameworks.py is intentionally not covered — no org_id in its path, correctly
global/unscoped, not part of this fix. users.py already had this class of
guard before this change (via the pre-existing _own_org() helper, now
migrated onto require_org_access itself) and isn't re-tested here.

Separately, GET/POST /orgs (list_orgs/create_org) have no org_id in their own
path — require_org_access doesn't apply to them — so they got a narrow,
role-only gate (require_role("msp_admin", "msp_engineer")) instead, per ADR
0005's per-org isolation boundary. That gate is tested at the bottom of this
file: customer_poc/c3pao_assessor get 403, msp_admin/msp_engineer succeed.
This is NOT the broader role-differentiation pass for the other ~36 routes —
just these two, which had no isolation at all before this.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import CurrentUser, get_current_user
from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    Contact,
    Control,
    ControlState,
    Framework,
    Organization,
)
from app.storage import StorageClient, get_storage_client
from tests.conftest import _app_session, _authed

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


@pytest.fixture
def storage() -> InMemoryStorageClient:
    return InMemoryStorageClient()


@pytest.fixture
def client(db_session, storage, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


def _seed(db_session, *, org_id: uuid.UUID | None = None) -> dict:
    """Org + framework + control + objective + assessment + control_state + contact.

    org_id defaults to a fresh random UUID (an "other org" the caller doesn't
    belong to). Pass org_id=fake_msp_admin.org_id to seed the caller's own org
    instead, for the same-org sanity checks.
    """
    org_kwargs: dict = {"name": f"GuardOrg-{uuid.uuid4().hex[:8]}"}
    if org_id is not None:
        org_kwargs["id"] = org_id
    org = Organization(**org_kwargs)
    fw = Framework(key=f"fw-guard-{uuid.uuid4().hex[:6]}", name="NIST r2", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id="AC.L2-3.1.1",
        family="AC",
        title="Access Control",
        requirement_text="Limit access.",
        sprs_weight=5,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Users identified.")
    db_session.add(obj)
    db_session.flush()

    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=fw.id, name="Guard Test"
    )
    db_session.flush()

    cs = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id,
            ControlState.objective_id == obj.id,
        )
    ).first()

    contact = Contact(
        org_id=org.id,
        name="Existing Contact",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        affiliation="msp",
    )
    db_session.add(contact)
    db_session.flush()

    return {"org": org, "fw": fw, "assessment": assessment, "cs": cs, "contact": contact}


# ---------------------------------------------------------------------------
# assessments.py
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_assessments_get_cross_org_403(client, db_session):
    b = _seed(db_session)
    url = f"/orgs/{b['org'].id}/assessments/{b['assessment'].id}/control-states"
    assert client.get(url).status_code == 403


@pytest.mark.integration
def test_assessments_patch_cross_org_403(client, db_session):
    b = _seed(db_session)
    url = f"/orgs/{b['org'].id}/assessments/{b['assessment'].id}/control-states/{b['cs'].id}"
    assert client.patch(url, json={"status": "met"}).status_code == 403


@pytest.mark.integration
def test_assessments_same_org_still_works(client, db_session, fake_msp_admin):
    a = _seed(db_session, org_id=fake_msp_admin.org_id)
    url = f"/orgs/{a['org'].id}/assessments/{a['assessment'].id}/control-states"
    assert client.get(url).status_code == 200


# ---------------------------------------------------------------------------
# evidence.py
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_evidence_list_cross_org_403(client, db_session):
    b = _seed(db_session)
    url = (
        f"/orgs/{b['org'].id}/assessments/{b['assessment'].id}"
        f"/control-states/{b['cs'].id}/evidence"
    )
    assert client.get(url).status_code == 403


@pytest.mark.integration
def test_evidence_upload_cross_org_403(client, db_session):
    b = _seed(db_session)
    url = (
        f"/orgs/{b['org'].id}/assessments/{b['assessment'].id}"
        f"/control-states/{b['cs'].id}/evidence"
    )
    r = client.post(
        url,
        files={"file": ("x.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        data={"artifact_type": "screenshot"},
    )
    assert r.status_code == 403


@pytest.mark.integration
def test_evidence_same_org_still_works(client, db_session, fake_msp_admin):
    a = _seed(db_session, org_id=fake_msp_admin.org_id)
    url = (
        f"/orgs/{a['org'].id}/assessments/{a['assessment'].id}"
        f"/control-states/{a['cs'].id}/evidence"
    )
    assert client.get(url).status_code == 200


# ---------------------------------------------------------------------------
# contacts.py
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_contacts_list_cross_org_403(client, db_session):
    b = _seed(db_session)
    assert client.get(f"/orgs/{b['org'].id}/contacts").status_code == 403


@pytest.mark.integration
def test_contacts_create_cross_org_403(client, db_session):
    b = _seed(db_session)
    payload = {"name": "Intruder", "email": "intruder@example.com", "affiliation": "msp"}
    assert client.post(f"/orgs/{b['org'].id}/contacts", json=payload).status_code == 403


@pytest.mark.integration
def test_contacts_same_org_still_works(client, db_session, fake_msp_admin):
    a = _seed(db_session, org_id=fake_msp_admin.org_id)
    r = client.get(f"/orgs/{a['org'].id}/contacts")
    assert r.status_code == 200
    assert len(r.json()) == 1


# ---------------------------------------------------------------------------
# orgs.py
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_orgs_profile_get_cross_org_403(client, db_session):
    b = _seed(db_session)
    assert client.get(f"/orgs/{b['org'].id}/profile").status_code == 403


@pytest.mark.integration
def test_orgs_profile_patch_cross_org_403(client, db_session):
    b = _seed(db_session)
    r = client.patch(f"/orgs/{b['org'].id}/profile", json={"industry": "Defense"})
    assert r.status_code == 403


@pytest.mark.integration
def test_orgs_profile_same_org_still_works(client, db_session, fake_msp_admin):
    a = _seed(db_session, org_id=fake_msp_admin.org_id)
    assert client.get(f"/orgs/{a['org'].id}/profile").status_code == 200


# ---------------------------------------------------------------------------
# bundle.py — GET only, no mutating endpoint
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_bundle_cross_org_403(client, db_session):
    b = _seed(db_session)
    url = f"/orgs/{b['org'].id}/assessments/{b['assessment'].id}/bundle"
    assert client.get(url).status_code == 403


@pytest.mark.integration
def test_bundle_same_org_still_works(client, db_session, fake_msp_admin):
    a = _seed(db_session, org_id=fake_msp_admin.org_id)
    url = f"/orgs/{a['org'].id}/assessments/{a['assessment'].id}/bundle"
    assert client.get(url).status_code == 200


# ---------------------------------------------------------------------------
# GET/POST /orgs — role-only gate (msp_admin/msp_engineer), no org_id in path
# ---------------------------------------------------------------------------


def _user_with_role(role: str) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        email=f"{role}@example.com",
        display_name=role,
        role=role,
        is_active=True,
        login_method="local",
    )


@pytest.mark.integration
@pytest.mark.parametrize("role", ["customer_poc", "c3pao_assessor"])
def test_list_orgs_forbidden_for_non_msp_roles(client, role):
    app.dependency_overrides[get_current_user] = lambda: _user_with_role(role)
    assert client.get("/orgs").status_code == 403


@pytest.mark.integration
@pytest.mark.parametrize("role", ["customer_poc", "c3pao_assessor"])
def test_create_org_forbidden_for_non_msp_roles(client, role):
    app.dependency_overrides[get_current_user] = lambda: _user_with_role(role)
    r = client.post("/orgs", json={"name": f"Should Not Exist {uuid.uuid4().hex[:6]}"})
    assert r.status_code == 403


@pytest.mark.integration
@pytest.mark.parametrize("role", ["msp_admin", "msp_engineer"])
def test_list_orgs_allowed_for_msp_roles(client, role):
    app.dependency_overrides[get_current_user] = lambda: _user_with_role(role)
    assert client.get("/orgs").status_code == 200


@pytest.mark.integration
@pytest.mark.parametrize("role", ["msp_admin", "msp_engineer"])
def test_create_org_allowed_for_msp_roles(client, role):
    app.dependency_overrides[get_current_user] = lambda: _user_with_role(role)
    r = client.post("/orgs", json={"name": f"MSP Created Org {uuid.uuid4().hex[:6]}"})
    assert r.status_code == 201
