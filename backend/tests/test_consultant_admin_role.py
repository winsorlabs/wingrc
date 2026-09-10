"""Integration tests for consultant_admin (migration 0034) -- a restricted
platform role for an external consultant (e.g. a C3PAO hired to help, not
to assess) with full access to compliance data and no access to
identity/security administration.

Covers the can/cannot audit from the task that added this role:
  CAN   — everything require_write()-gated that isn't independently
          restricted to msp_admin/msp_engineer (scope, assessments,
          control states, evidence, contacts, RACI, bundle export),
          reusing test_assessor_readonly.py's own scenario/case builders
          so this is exercised against the exact same endpoint surface
          that file already proves c3pao_assessor is blocked from.
  CAN   — integrations config (routers/integrations.py), explicitly named
          in scope by the task.
  CANNOT — user management, API token minting (both already covered by
          test_assessor_readonly.py's _ADMIN_GATED_CASES, re-asserted here
          for consultant_admin specifically).
  CANNOT — audit log (routers/audit_log.py) — decided and documented in
          that router's own module docstring; asserted here as a real
          403, not inferred from the UI.
  CANNOT — create a new org (routers/orgs.py's create_org) — an
          MSP-business decision, not compliance-data work; also ties into
          org_membership.py's _AUTO_PROVISION_ROLES exclusion.
  CANNOT — edit practitioner notes (routers/objectives.py) — deployment-
          wide catalog content, deliberately excluded (see that router's
          own module docstring for the full reasoning).
  Not read-only: consultant_admin is not in auth.py's _READ_ONLY_ROLES —
          asserted directly against the ladder, not just via the CAN
          cases above.

Run in-container:
    docker compose exec backend pytest tests/test_consultant_admin_role.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import _READ_ONLY_ROLES, _ROLE_RANK, get_current_user
from app.db import get_session
from app.main import app
from app.models import Organization
from tests.conftest import _app_session, _authed, _grant
from tests.test_assessor_readonly import (
    _ADMIN_GATED_CASES,
    _CASE_IDS,
    _as_role,
    _build_cases,
    _client_as,
    _do_request,
    _seed_scenario,
)
from tests.test_assessor_readonly import storage as storage  # noqa: F401 (fixture)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Own copy of test_assessor_readonly.py's autouse fixture -- pytest
    autouse fixtures only apply within the module that defines them, so
    importing that file's helpers doesn't inherit its cleanup."""
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Rank ladder (no DB needed, but kept under the same marker as its siblings
# for one consistent `pytest -m integration` run)
# ---------------------------------------------------------------------------


def test_role_rank_ladder_after_renumbering():
    assert _ROLE_RANK == {
        "msp_admin": 5,
        "consultant_admin": 4,
        "msp_engineer": 3,
        "customer_poc": 2,
        "c3pao_assessor": 1,
    }


def test_consultant_admin_not_read_only():
    assert "consultant_admin" not in _READ_ONLY_ROLES


def test_c3pao_assessor_still_the_only_read_only_role_after_renumbering():
    """The ladder renumber (migration 0034) must not have disturbed
    require_write()'s independent role set."""
    assert _READ_ONLY_ROLES == frozenset({"c3pao_assessor"})


# ---------------------------------------------------------------------------
# CAN: every compliance-data mutating endpoint test_assessor_readonly.py
# already proves c3pao_assessor is blocked from, minus the ones that are
# independently msp_admin/msp_engineer-gated (asserted as CANNOT below).
# ---------------------------------------------------------------------------

_DATA_CASE_IDS = [c for c in _CASE_IDS if c not in _ADMIN_GATED_CASES]


@pytest.mark.parametrize("case_id", _DATA_CASE_IDS)
def test_consultant_admin_can_write_compliance_data(db_session, storage, case_id):
    org_id = uuid.uuid4()
    d = _seed_scenario(db_session, org_id=org_id)
    spec = _build_cases(d)[case_id]

    client = _client_as(db_session, storage, _as_role("consultant_admin", org_id=org_id))
    r = _do_request(client, spec)
    assert r.status_code != 403, (
        f"{case_id}: expected non-403 for consultant_admin, got {r.status_code}: {r.text}"
    )


# ---------------------------------------------------------------------------
# CANNOT: the same admin-gated cases (user management, API token minting)
# test_assessor_readonly.py routes msp_admin through as the positive actor
# for -- consultant_admin must 403 there too, same as c3pao_assessor.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(_ADMIN_GATED_CASES))
def test_consultant_admin_blocked_on_user_and_token_management(db_session, storage, case_id):
    org_id = uuid.uuid4()
    d = _seed_scenario(db_session, org_id=org_id)
    spec = _build_cases(d)[case_id]

    client = _client_as(db_session, storage, _as_role("consultant_admin", org_id=org_id))
    r = _do_request(client, spec)
    assert r.status_code == 403, (
        f"{case_id}: expected 403 for consultant_admin, got {r.status_code}: {r.text}"
    )


def test_consultant_admin_can_list_users(db_session, storage):
    """Corrected after a real bench-stack failure, not assumed: GET
    .../users is require_org_access() with no role restriction at all
    (users.py:154-158) -- ANY org member can already list users today,
    including customer_poc and c3pao_assessor. This predates
    consultant_admin and isn't part of the msp_admin-gated identity/
    security surface (invite/patch/delete/reset-mfa/unlock/reset-password
    all stay separately gated, asserted 403 elsewhere in this file) --
    only the mutating actions are restricted, not visibility into who's
    on the engagement."""
    org_id = uuid.uuid4()
    _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("consultant_admin", org_id=org_id))
    r = client.get(f"/orgs/{org_id}/users")
    assert r.status_code == 200


def test_consultant_admin_blocked_on_list_api_tokens(db_session, storage):
    org_id = uuid.uuid4()
    _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("consultant_admin", org_id=org_id))
    r = client.get(f"/orgs/{org_id}/api-tokens")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# CANNOT: audit log (decided and documented in routers/audit_log.py)
# ---------------------------------------------------------------------------


def test_consultant_admin_blocked_on_audit_log(db_session, storage):
    org_id = uuid.uuid4()
    _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("consultant_admin", org_id=org_id))
    r = client.get(f"/orgs/{org_id}/audit-log")
    assert r.status_code == 403


def test_msp_admin_still_sees_audit_log(db_session, storage):
    """Regression guard: the audit-log exclusion is specific to
    consultant_admin, not an accidental tightening of the existing gate."""
    org_id = uuid.uuid4()
    _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("msp_admin", org_id=org_id))
    r = client.get(f"/orgs/{org_id}/audit-log")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# CANNOT: create a new org
# ---------------------------------------------------------------------------


def test_consultant_admin_blocked_on_create_org(db_session):
    """require_role() (unlike require_org_access()) checks only the
    authenticated identity's own role, not org_membership -- no
    Organization/_grant() needed here, deliberately not routed through
    _client_as."""
    consultant = _as_role("consultant_admin", org_id=uuid.uuid4())
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, consultant)
    client = TestClient(app)
    r = client.post("/orgs", json={"name": f"ShouldNotExist-{uuid.uuid4().hex[:8]}"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# CANNOT: edit practitioner notes (deployment-wide catalog content)
# ---------------------------------------------------------------------------


def _seed_objective_for_notes(db_session):
    from app.models import AssessmentObjective, Control, Framework

    fw = Framework(key=f"fw-{uuid.uuid4().hex}", name="Test FW", version="r2")
    db_session.add(fw)
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
    obj = AssessmentObjective(
        control_id=ctrl.id,
        objective_key="a",
        text="Test objective",
        practitioner_notes="Original AI text.",
        practitioner_notes_original="Original AI text.",
    )
    db_session.add(obj)
    db_session.flush()
    return obj


def test_consultant_admin_blocked_on_practitioner_notes_edit(db_session):
    consultant = _as_role("consultant_admin", org_id=uuid.uuid4())
    obj = _seed_objective_for_notes(db_session)

    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, consultant)
    client = TestClient(app)
    try:
        r = client.patch(
            f"/objectives/{obj.id}/practitioner-notes", json={"text": "rewritten"}
        )
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# CAN: integrations config (deployment-wide, explicitly in scope per the
# task that added this role -- see routers/integrations.py's own docstring
# for the tension this leaves open).
# ---------------------------------------------------------------------------


def test_consultant_admin_can_list_integrations(db_session):
    consultant = _as_role("consultant_admin", org_id=uuid.uuid4())
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, consultant)
    client = TestClient(app)
    try:
        r = client.get("/integrations")
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_consultant_admin_can_set_integration_credential(db_session):
    consultant = _as_role("consultant_admin", org_id=uuid.uuid4())
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, consultant)
    client = TestClient(app)
    try:
        r = client.put(
            "/integrations/liongard/credential",
            json={
                "config": {"instance_url": "https://myinstance.app.liongard.com"},
                "credential": {"access_key_id": "AKID", "access_key_secret": "s3cr3t"},
            },
        )
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# CAN: scope entity creation (not part of test_assessor_readonly.py's case
# list, so a small standalone check rather than reusing that table).
# ---------------------------------------------------------------------------


def test_consultant_admin_can_create_scope_entity(db_session, storage):
    org_id = uuid.uuid4()
    org = Organization(id=org_id, name=f"ScopeTestOrg-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()

    consultant = _as_role("consultant_admin", org_id=org_id)
    _grant(db_session, consultant, org_id=org_id)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, consultant)
    client = TestClient(app)
    try:
        r = client.post(
            f"/orgs/{org_id}/scope",
            json={"entity_type": "device", "natural_key": f"laptop-{uuid.uuid4().hex[:6]}"},
        )
        assert r.status_code == 201
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# CAN: RACI assignment (not part of test_assessor_readonly.py's case list
# either -- raci.py wasn't one of the six routers I.2 added require_write()
# to, but the task explicitly named RACI as in-scope compliance work).
# ---------------------------------------------------------------------------


def test_consultant_admin_can_assign_raci(db_session, storage):
    org_id = uuid.uuid4()
    d = _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("consultant_admin", org_id=org_id))
    r = client.post(
        f"/orgs/{org_id}/assessments/{d['assessment'].id}/raci",
        json={
            "control_state_id": str(d["cs"].id),
            "contact_id": str(d["contact"].id),
            "raci_letter": "R",
        },
    )
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# CAN: bundle export (GET, not in the mutating-only _CASE_IDS list).
# ---------------------------------------------------------------------------


def test_consultant_admin_can_export_bundle(db_session, storage):
    org_id = uuid.uuid4()
    d = _seed_scenario(db_session, org_id=org_id)
    client = _client_as(db_session, storage, _as_role("consultant_admin", org_id=org_id))
    r = client.get(f"/orgs/{org_id}/assessments/{d['assessment'].id}/bundle")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
