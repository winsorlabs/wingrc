"""Integration tests for I.2 — assessor read-only enforcement (require_write()).

Covers, per docs/PLAN-auth-rbac-completion.md I.2:
  - c3pao_assessor gets 403 on every mutating endpoint across the six routers
    require_write() was added to (assessments, evidence, contacts, bundle,
    orgs, users).
  - The gate does not regress a role that could write there before I.2.
  - c3pao_assessor can still read: control states, evidence list, contacts,
    org profile, and the bundle export.

Positive-actor note: the plan's spec says "non-403 for customer_poc"
uniformly, but POST/PATCH/DELETE /users and POST /api-tokens are gated to
msp_admin (msp_engineer for token creation) independently of I.2 — customer_poc
already 403s there for a reason that predates this slice. Asserting non-403
for customer_poc on those specific endpoints would test something false (it
would pass whether or not require_write() were correctly scoped). For that
subset the positive actor is msp_admin instead, which genuinely could write
there before I.2 and is the only actor that actually exercises whether
require_write() regressed it.

Run in-container:
    docker compose exec backend pytest tests/test_assessor_readonly.py -m integration -v
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import CurrentUser, get_current_user
from app.db import get_session
from app.engine import activate_org_product, start_assessment
from app.main import app
from app.models import (
    ApiToken,
    AssessmentObjective,
    BaselineControl,
    BaselineEvidenceSpec,
    Contact,
    ContactDocumentationRole,
    Control,
    ControlState,
    Evidence,
    EvidenceStateLink,
    EvidenceTask,
    Framework,
    Organization,
    Product,
    User,
)
from app.storage import StorageClient, get_storage_client
from tests.conftest import _app_session, _authed

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


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


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _as_role(role: str, *, org_id: uuid.UUID) -> CurrentUser:
    return CurrentUser(
        id=uuid.uuid4(),
        org_id=org_id,
        email=f"{role}@example.com",
        display_name=role,
        role=role,
        is_active=True,
        login_method="local",
        mfa_enrolled=True,
    )


def _client_as(db_session, storage: InMemoryStorageClient, actor: CurrentUser) -> TestClient:
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_storage_client] = lambda: storage
    app.dependency_overrides[get_current_user] = _authed(db_session, actor)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Scenario: one org with a full assessment surface — enough to exercise every
# mutating endpoint the six require_write()-gated routers expose.
# ---------------------------------------------------------------------------


def _seed_scenario(db_session, *, org_id: uuid.UUID) -> dict:
    org = Organization(id=org_id, name=f"ROTestOrg-{uuid.uuid4().hex[:8]}")
    fw = Framework(key=f"fw-ro-{uuid.uuid4().hex[:8]}", name="RO Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id=f"AC.L2-{uuid.uuid4().hex[:6]}",
        family="AC",
        title="Test control",
        requirement_text="Test requirement",
        sprs_weight=1,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Test objective")
    db_session.add(obj)
    db_session.flush()

    # Already-active product (drives an evidence task + a linked reference
    # artifact) and a not-yet-active product (target of the activate case).
    product_active = Product(
        framework_id=fw.id,
        key=f"prod-active-{uuid.uuid4().hex[:8]}",
        name="Active Product",
        provider="Acme",
        category="EDR",
        asset_type="SPA",
        role="Test product",
    )
    product_inactive = Product(
        framework_id=fw.id,
        key=f"prod-inactive-{uuid.uuid4().hex[:8]}",
        name="Inactive Product",
        provider="Acme",
        category="EDR",
        asset_type="SPA",
        role="Test product",
    )
    db_session.add_all([product_active, product_inactive])
    db_session.flush()

    bc_active = BaselineControl(
        product_id=product_active.id,
        control_id=ctrl.id,
        objectives=["a"],
        classification="provider_satisfies",
        candidate_state="pending_evidence",
        coverage_basis="customer_system",
    )
    bc_inactive = BaselineControl(
        product_id=product_inactive.id,
        control_id=ctrl.id,
        objectives=["a"],
        classification="provider_satisfies",
        candidate_state="pending_evidence",
        coverage_basis="customer_system",
    )
    db_session.add_all([bc_active, bc_inactive])
    db_session.flush()

    spec_active = BaselineEvidenceSpec(
        baseline_control_id=bc_active.id,
        artifact_description="Config export",
        evidence_type="export",
    )
    spec_inactive = BaselineEvidenceSpec(
        baseline_control_id=bc_inactive.id,
        artifact_description="Config export 2",
        evidence_type="export",
    )
    db_session.add_all([spec_active, spec_inactive])
    db_session.flush()

    assessment = start_assessment(db_session, org_id=org.id, framework_id=fw.id, name="RO Test")
    db_session.flush()

    activate_org_product(
        db_session,
        org_id=org.id,
        product_id=product_active.id,
        assessment_id=assessment.id,
    )
    db_session.flush()

    cs = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id,
            ControlState.objective_id == obj.id,
        )
    ).first()
    assert cs is not None

    task = db_session.scalars(
        select(EvidenceTask).where(EvidenceTask.assessment_id == assessment.id)
    ).first()
    assert task is not None, "activation should have seeded an evidence task"

    ev = Evidence(
        org_id=org.id,
        kind="reference",
        title="Existing reference",
        artifact_type="document",
        reference_location="https://example.com/existing",
        collected_at=datetime.now(UTC),
    )
    db_session.add(ev)
    db_session.flush()
    db_session.add(EvidenceStateLink(evidence_id=ev.id, control_state_id=cs.id))
    db_session.flush()

    contact = Contact(
        org_id=org.id,
        name="Jane Doe",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        affiliation="customer",
    )
    db_session.add(contact)
    db_session.flush()
    db_session.add(ContactDocumentationRole(contact_id=contact.id, role="cui_user"))
    db_session.flush()

    target_user = User(
        home_org_id=org.id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        display_name="Target User",
        login_method="local",
        role="customer_poc",
        is_active=True,
    )
    db_session.add(target_user)
    db_session.flush()

    token = ApiToken(
        org_id=org.id,
        user_id=target_user.id,
        name="Standing token",
        token_hash=uuid.uuid4().hex + uuid.uuid4().hex,  # 64 hex chars, unique
        role="customer_poc",
    )
    db_session.add(token)
    db_session.flush()

    return {
        "org": org,
        "assessment": assessment,
        "ctrl": ctrl,
        "obj": obj,
        "cs": cs,
        "product_active": product_active,
        "product_inactive": product_inactive,
        "task": task,
        "evidence": ev,
        "contact": contact,
        "target_user": target_user,
        "token": token,
    }


# ---------------------------------------------------------------------------
# Mutating-endpoint cases
# ---------------------------------------------------------------------------

# Endpoints gated to msp_admin (or msp_engineer for token creation)
# independently of I.2 — see module docstring.
_ADMIN_GATED_CASES = frozenset(
    {"post_users_invite", "patch_user", "delete_user", "post_api_token", "delete_api_token"}
)

_CASE_IDS = [
    "patch_control_state",
    "put_statements",
    "post_evidence_file",
    "post_evidence_references",
    "delete_evidence",
    "patch_evidence_task",
    "post_collect_task_file",
    "post_collect_task_reference",
    "post_activate_product",
    "post_deactivate_product",
    "post_contact",
    "patch_contact",
    "delete_contact",
    "post_contact_role",
    "delete_contact_role",
    "post_users_invite",
    "patch_user",
    "delete_user",
    "post_api_token",
    "delete_api_token",
    "patch_profile",
    "post_logo",
    "put_system_description",
]


def _positive_role(case_id: str) -> str:
    return "msp_admin" if case_id in _ADMIN_GATED_CASES else "customer_poc"


def _build_cases(d: dict) -> dict[str, dict]:
    org = d["org"].id
    assessment = d["assessment"].id
    cs = d["cs"].id
    ctrl = d["ctrl"].id
    obj = d["obj"].id
    task = d["task"].id
    ev = d["evidence"].id
    contact = d["contact"].id
    product_active = d["product_active"].id
    product_inactive = d["product_inactive"].id
    target_user = d["target_user"].id
    token = d["token"].id

    base = f"/orgs/{org}"
    a_base = f"{base}/assessments/{assessment}"

    return {
        "patch_control_state": dict(
            method="PATCH",
            url=f"{a_base}/control-states/{cs}",
            kind="json",
            payload={"status": "met"},
        ),
        "put_statements": dict(
            method="PUT",
            url=f"{a_base}/controls/{ctrl}/statements",
            kind="json",
            payload=[{"objective_id": str(obj), "body": "Statement text", "status": "draft"}],
        ),
        "post_evidence_file": dict(
            method="POST",
            url=f"{a_base}/control-states/{cs}/evidence",
            kind="multipart",
            files={"file": ("test.png", _PNG_BYTES, "image/png")},
            data={"artifact_type": "document"},
        ),
        "post_evidence_references": dict(
            method="POST",
            url=f"{a_base}/control-states/{cs}/evidence/references",
            kind="json",
            payload=[
                {"title": "Ref", "location": "https://example.com/ref", "artifact_type": "document"}
            ],
        ),
        "delete_evidence": dict(
            method="DELETE",
            url=f"{a_base}/control-states/{cs}/evidence/{ev}",
            kind="none",
        ),
        "patch_evidence_task": dict(
            method="PATCH",
            url=f"{a_base}/evidence-tasks/{task}",
            kind="json",
            payload={"status": "na"},
        ),
        "post_collect_task_file": dict(
            method="POST",
            url=f"{a_base}/evidence-tasks/{task}/collect",
            kind="multipart",
            files={"file": ("test.png", _PNG_BYTES, "image/png")},
            data={"artifact_type": "document"},
        ),
        "post_collect_task_reference": dict(
            method="POST",
            url=f"{a_base}/evidence-tasks/{task}/collect/reference",
            kind="json",
            payload={
                "title": "Ref2",
                "location": "https://example.com/ref2",
                "artifact_type": "document",
            },
        ),
        "post_activate_product": dict(
            method="POST",
            url=f"{a_base}/products/{product_inactive}/activate",
            kind="json",
            payload={},
        ),
        "post_deactivate_product": dict(
            method="POST",
            url=f"{a_base}/products/{product_active}/deactivate",
            kind="none",
        ),
        "post_contact": dict(
            method="POST",
            url=f"{base}/contacts",
            kind="json",
            payload={
                "name": "New Contact",
                "email": f"{uuid.uuid4().hex[:8]}@example.com",
                "affiliation": "customer",
            },
        ),
        "patch_contact": dict(
            method="PATCH",
            url=f"{base}/contacts/{contact}",
            kind="json",
            payload={"name": "Updated Contact"},
        ),
        "delete_contact": dict(
            method="DELETE",
            url=f"{base}/contacts/{contact}",
            kind="none",
        ),
        "post_contact_role": dict(
            method="POST",
            url=f"{base}/contacts/{contact}/roles",
            kind="json",
            payload={"role": "it_admin"},
        ),
        "delete_contact_role": dict(
            method="DELETE",
            url=f"{base}/contacts/{contact}/roles/cui_user",
            kind="none",
        ),
        "post_users_invite": dict(
            method="POST",
            url=f"{base}/users",
            kind="json",
            payload={
                "email": f"{uuid.uuid4().hex[:8]}@example.com",
                "display_name": "Invitee",
                "role": "customer_poc",
            },
        ),
        "patch_user": dict(
            method="PATCH",
            url=f"{base}/users/{target_user}",
            kind="json",
            payload={"display_name": "Updated User"},
        ),
        "delete_user": dict(
            method="DELETE",
            url=f"{base}/users/{target_user}",
            kind="none",
        ),
        "post_api_token": dict(
            method="POST",
            url=f"{base}/api-tokens",
            kind="json",
            payload={"name": "New Token", "role": "customer_poc", "user_id": str(target_user)},
        ),
        "delete_api_token": dict(
            method="DELETE",
            url=f"{base}/api-tokens/{token}",
            kind="none",
        ),
        "patch_profile": dict(
            method="PATCH",
            url=f"{base}/profile",
            kind="json",
            payload={"industry": "Technology"},
        ),
        "post_logo": dict(
            method="POST",
            url=f"{base}/logo",
            kind="multipart",
            files={"file": ("logo.png", _PNG_BYTES, "image/png")},
            data=None,
        ),
        "put_system_description": dict(
            method="PUT",
            url=f"{base}/system-description",
            kind="json",
            payload={
                "system_name": "Test System",
                "system_type": "minor_application",
                "operational_status": "operational",
            },
        ),
    }


def _do_request(client: TestClient, spec: dict):
    method = spec["method"]
    url = spec["url"]
    if spec["kind"] == "json":
        return client.request(method, url, json=spec["payload"])
    if spec["kind"] == "multipart":
        return client.request(method, url, files=spec["files"], data=spec.get("data"))
    return client.request(method, url)


# ---------------------------------------------------------------------------
# Negative: c3pao_assessor is blocked everywhere
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("case_id", _CASE_IDS)
def test_assessor_blocked_on_every_mutating_endpoint(db_session, storage, case_id):
    org_id = uuid.uuid4()
    d = _seed_scenario(db_session, org_id=org_id)
    spec = _build_cases(d)[case_id]

    client = _client_as(db_session, storage, _as_role("c3pao_assessor", org_id=org_id))
    r = _do_request(client, spec)
    assert r.status_code == 403, (
        f"{case_id}: expected 403 for c3pao_assessor, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# Positive: a role that could write there before I.2 still can
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("case_id", _CASE_IDS)
def test_positive_actor_not_blocked_by_read_only_gate(db_session, storage, case_id):
    org_id = uuid.uuid4()
    d = _seed_scenario(db_session, org_id=org_id)
    spec = _build_cases(d)[case_id]

    role = _positive_role(case_id)
    client = _client_as(db_session, storage, _as_role(role, org_id=org_id))
    r = _do_request(client, spec)
    assert r.status_code != 403, (
        f"{case_id}: expected non-403 for {role}, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# Positive read coverage: assessor's own primary surface stays readable
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_assessor_can_read_control_states(db_session, storage):
    org_id = uuid.uuid4()
    d = _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("c3pao_assessor", org_id=org_id))
    r = client.get(f"/orgs/{org_id}/assessments/{d['assessment'].id}/control-states")
    assert r.status_code == 200


@pytest.mark.integration
def test_assessor_can_read_evidence_list(db_session, storage):
    org_id = uuid.uuid4()
    d = _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("c3pao_assessor", org_id=org_id))
    r = client.get(
        f"/orgs/{org_id}/assessments/{d['assessment'].id}/control-states/{d['cs'].id}/evidence"
    )
    assert r.status_code == 200


@pytest.mark.integration
def test_assessor_can_read_contacts(db_session, storage):
    org_id = uuid.uuid4()
    _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("c3pao_assessor", org_id=org_id))
    r = client.get(f"/orgs/{org_id}/contacts")
    assert r.status_code == 200


@pytest.mark.integration
def test_assessor_can_read_profile(db_session, storage):
    org_id = uuid.uuid4()
    _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("c3pao_assessor", org_id=org_id))
    r = client.get(f"/orgs/{org_id}/profile")
    assert r.status_code == 200


@pytest.mark.integration
def test_assessor_can_get_bundle(db_session, storage):
    org_id = uuid.uuid4()
    d = _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("c3pao_assessor", org_id=org_id))
    r = client.get(f"/orgs/{org_id}/assessments/{d['assessment'].id}/bundle")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
