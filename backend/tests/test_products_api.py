"""Integration tests for the products list endpoint and activation/board integration.

Critical invariant tested here (user note 3):
  Activating RocketCyber must flip AU (provider_satisfies) objectives to
  pending_evidence but must NOT touch IA (customer_owns) objectives.
  This guards against over-crediting a vendor for controls it disclaims.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    Assessment,
    AssessmentObjective,
    BaselineControl,
    Control,
    Framework,
    Organization,
    Product,
)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Shared seed helpers
# ---------------------------------------------------------------------------


def _seed_empty(db_session) -> dict:
    """Org + framework + assessment with no products in the baseline."""
    org = Organization(name=f"ProdTestOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-prod-{uuid.uuid4().hex[:6]}", name="NIST r2", version="r2")
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

    assessment = Assessment(org_id=org.id, framework_id=fw.id, name="Empty Test")
    db_session.add(assessment)
    db_session.flush()

    return {"org": org, "fw": fw, "assessment": assessment}


def _seed_rocketcyber(db_session) -> dict:
    """Seed a RocketCyber-style product covering AU (provider_satisfies)
    and explicitly disclaiming IA (customer_owns).

    Framework has two controls:
      AU.L2-3.3.1  objective [a]  -- authoritative SIEM; RC owns this
      IA.L2-3.5.1  objective [a]  -- customer IdP owns this; RC does NOT
    """
    org = Organization(name=f"ProdTestOrg-RC-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-rc-{uuid.uuid4().hex[:6]}", name="NIST r2", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    au_ctrl = Control(
        framework_id=fw.id,
        control_id="AU.L2-3.3.1",
        family="AU",
        title="Event Logging",
        requirement_text="Create audit records.",
        sprs_weight=3,
        sequence_order=1,
    )
    ia_ctrl = Control(
        framework_id=fw.id,
        control_id="IA.L2-3.5.1",
        family="IA",
        title="Identify System Users",
        requirement_text="Identify users.",
        sprs_weight=3,
        sequence_order=2,
    )
    db_session.add_all([au_ctrl, ia_ctrl])
    db_session.flush()

    au_a = AssessmentObjective(control_id=au_ctrl.id, objective_key="a", text="Event types logged.")
    ia_a = AssessmentObjective(control_id=ia_ctrl.id, objective_key="a", text="Users identified.")
    db_session.add_all([au_a, ia_a])
    db_session.flush()

    test_key = f"rc-test-{uuid.uuid4().hex[:8]}"
    product = Product(
        framework_id=fw.id,
        key=test_key,
        name="RocketCyber Managed SIEM + SOC",
        provider="Kaseya",
        category="ESP",
        asset_type="SPA",
        role="Authoritative SIEM and 24/7 managed SOC.",
    )
    db_session.add(product)
    db_session.flush()

    # AU: RocketCyber is the authoritative SIEM
    bc_au = BaselineControl(
        product_id=product.id,
        control_id=au_ctrl.id,
        objectives=["a"],
        classification="provider_satisfies",
        candidate_state="pending_evidence",
        provider_contribution="RocketCyber generates and retains audit records.",
    )
    # IA: RocketCyber explicitly disclaims identity management
    bc_ia = BaselineControl(
        product_id=product.id,
        control_id=ia_ctrl.id,
        objectives=["a"],
        classification="customer_owns",
        candidate_state="not_satisfied_by_product",
        note="RocketCyber does NOT identify users; customer IdP owns the IA family.",
    )
    db_session.add_all([bc_au, bc_ia])
    db_session.flush()

    # Use start_assessment to properly seed control_state rows
    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=fw.id, name="RC Integration Test"
    )
    db_session.flush()

    return {
        "org": org,
        "fw": fw,
        "product": product,
        "bc_au": bc_au,
        "bc_ia": bc_ia,
        "au_ctrl": au_ctrl,
        "ia_ctrl": ia_ctrl,
        "au_a": au_a,
        "ia_a": ia_a,
        "assessment": assessment,
    }


def _products_url(d: dict) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/products"


def _states_url(d: dict) -> str:
    return f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states"


def _activate_url(d: dict) -> str:
    return (
        f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}"
        f"/products/{d['product'].id}/activate"
    )


# ---------------------------------------------------------------------------
# 1. List products — basic shape
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_list_products_empty_when_no_baseline(client, db_session):
    """Returns empty list when no products are seeded for this framework."""
    d = _seed_empty(db_session)
    r = client.get(_products_url(d))
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.integration
def test_list_products_shows_rocketcyber(client, db_session):
    """RocketCyber product appears with correct coverage counts."""
    d = _seed_rocketcyber(db_session)
    r = client.get(_products_url(d))
    assert r.status_code == 200
    products = r.json()
    assert len(products) == 1
    p = products[0]
    assert p["key"] == d["product"].key
    assert p["name"] == "RocketCyber Managed SIEM + SOC"
    assert p["provider"] == "Kaseya"
    assert p["is_active"] is False
    assert p["provider_satisfies_count"] == 1
    assert p["shared_count"] == 0
    assert p["customer_owns_count"] == 1


@pytest.mark.integration
def test_list_products_shows_active_after_activate(client, db_session):
    """After activation, is_active flips to True."""
    d = _seed_rocketcyber(db_session)
    assert client.get(_products_url(d)).json()[0]["is_active"] is False

    act = client.post(_activate_url(d))
    assert act.status_code == 200

    products = client.get(_products_url(d)).json()
    assert products[0]["is_active"] is True
    assert products[0]["activated_at"] is not None


# ---------------------------------------------------------------------------
# 2. Control-states response includes product key
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_control_states_include_product_key_after_activate(client, db_session):
    """After activation, sourced_from_product_key appears on covered objectives."""
    d = _seed_rocketcyber(db_session)
    client.post(_activate_url(d))

    rows = client.get(_states_url(d)).json()
    by_ctrl = {row["control_id"] + "[" + row["objective_key"] + "]": row for row in rows}

    au = by_ctrl["AU.L2-3.3.1[a]"]
    assert au["sourced_from_product_key"] == d["product"].key

    ia = by_ctrl["IA.L2-3.5.1[a]"]
    assert ia["sourced_from_product_key"] is None


# ---------------------------------------------------------------------------
# 3. Critical: RocketCyber AU/IA segregation (user note 3)
#
#    Activating RocketCyber must flip AU objectives (provider_satisfies) to
#    pending_evidence — never to "met" — and must leave IA (customer_owns)
#    objectives untouched at not_met/customer_owns.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_activate_rocketcyber_au_pending_evidence_not_met(client, db_session):
    """Activation sets AU objectives to pending_evidence, NOT met.

    The magic loop marks a product's covered objectives as candidates awaiting
    evidence. 'met' requires confirmed config + attached evidence by an engineer.
    Activation alone must never produce 'met'.
    """
    d = _seed_rocketcyber(db_session)
    r = client.post(_activate_url(d))
    assert r.status_code == 200
    result = r.json()
    assert result["objectives_updated"] == 1   # only AU[a]; IA[a] is customer_owns

    rows = client.get(_states_url(d)).json()
    by_ctrl = {row["control_id"] + "[" + row["objective_key"] + "]": row for row in rows}

    au = by_ctrl["AU.L2-3.3.1[a]"]
    assert au["status"] == "pending_evidence", (
        f"AU objective must be pending_evidence after activation, got {au['status']!r}"
    )
    assert au["responsibility"] == "provider_satisfies"
    assert au["sourced_from_product_key"] == d["product"].key
    assert au["status"] != "met", "Activation must never produce 'met' — evidence required first"


@pytest.mark.integration
def test_activate_rocketcyber_ia_family_untouched(client, db_session):
    """IA (customer_owns) objectives remain not_met/customer_owns after RC activation.

    RocketCyber's baseline explicitly disclaims the IA family — the customer's
    IdP owns identity. This test proves the engine does not over-credit the vendor.
    """
    d = _seed_rocketcyber(db_session)
    client.post(_activate_url(d))

    rows = client.get(_states_url(d)).json()
    by_ctrl = {row["control_id"] + "[" + row["objective_key"] + "]": row for row in rows}

    ia = by_ctrl["IA.L2-3.5.1[a]"]
    assert ia["status"] == "not_met", (
        f"IA (customer_owns) must stay not_met after RC activation, got {ia['status']!r}"
    )
    assert ia["responsibility"] == "customer_owns", (
        f"IA responsibility must stay customer_owns, got {ia['responsibility']!r}"
    )
    assert ia["sourced_from_product_key"] is None, (
        "IA objective must have no product source — RocketCyber does not own IA"
    )


@pytest.mark.integration
def test_list_products_wrong_org_returns_404(client, db_session):
    d = _seed_rocketcyber(db_session)
    url = (
        f"/orgs/{uuid.uuid4()}/assessments/{d['assessment'].id}/products"
    )
    assert client.get(url).status_code == 404


# ---------------------------------------------------------------------------
# 4. coverage_basis: platform_only controls must NOT activate
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_platform_only_controls_excluded_from_activation(client, db_session):
    """A provider_satisfies control with coverage_basis=platform_only must NOT
    flip to pending_evidence when the product is activated.

    This guards the core correctness invariant: vendor CRMs often claim controls
    that only apply to their own platform (e.g. AC.L2-3.1.11 session termination
    for the vendor's web console). Those must be excluded from the magic loop so
    the customer's assessment does not show false pending_evidence for controls
    the product does not perform in the customer's environment.
    """
    org = Organization(name=f"PlatOnlyOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-plat-{uuid.uuid4().hex[:6]}", name="NIST r2", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    # Two controls: AU (customer_system) and AC.3.1.11 (platform_only)
    au_ctrl = Control(
        framework_id=fw.id, control_id="AU.L2-3.3.1", family="AU",
        title="Event Logging", requirement_text="Create audit records.",
        sprs_weight=3, sequence_order=1,
    )
    ac_ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.11", family="AC",
        title="Session Termination", requirement_text="Terminate sessions.",
        sprs_weight=1, sequence_order=2,
    )
    db_session.add_all([au_ctrl, ac_ctrl])
    db_session.flush()

    au_a = AssessmentObjective(control_id=au_ctrl.id, objective_key="a", text="Events logged.")
    ac_a = AssessmentObjective(
        control_id=ac_ctrl.id, objective_key="a", text="Sessions terminated."
    )
    db_session.add_all([au_a, ac_a])
    db_session.flush()

    product = Product(
        framework_id=fw.id, key=f"rc-plat-{uuid.uuid4().hex[:8]}",
        name="RC Platform Test", provider="Kaseya", category="ESP",
        asset_type="SPA", role="Test product.",
    )
    db_session.add(product)
    db_session.flush()

    bc_au = BaselineControl(
        product_id=product.id, control_id=au_ctrl.id, objectives=["a"],
        classification="provider_satisfies", coverage_basis="customer_system",
        candidate_state="pending_evidence",
    )
    bc_ac = BaselineControl(
        product_id=product.id, control_id=ac_ctrl.id, objectives=["a"],
        classification="provider_satisfies", coverage_basis="platform_only",
        candidate_state="pending_evidence",
    )
    db_session.add_all([bc_au, bc_ac])
    db_session.flush()

    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=fw.id, name="Platform Only Test"
    )
    db_session.flush()

    activate_url = (
        f"/orgs/{org.id}/assessments/{assessment.id}/products/{product.id}/activate"
    )
    r = client.post(activate_url)
    assert r.status_code == 200
    result = r.json()
    assert result["objectives_updated"] == 1, (
        f"Only 1 objective (AU, customer_system) should activate; "
        f"got {result['objectives_updated']}"
    )

    states_url = f"/orgs/{org.id}/assessments/{assessment.id}/control-states"
    rows = client.get(states_url).json()
    by_ctrl = {row["control_id"] + "[" + row["objective_key"] + "]": row for row in rows}

    au = by_ctrl["AU.L2-3.3.1[a]"]
    assert au["status"] == "pending_evidence", (
        f"AU (customer_system) must be pending_evidence after activation, got {au['status']!r}"
    )

    ac = by_ctrl["AC.L2-3.1.11[a]"]
    assert ac["status"] == "not_met", (
        f"AC.L2-3.1.11 (platform_only) must stay not_met after activation, got {ac['status']!r}"
    )
    assert ac["responsibility"] == "customer_owns", (
        f"platform_only control must stay customer_owns, got {ac['responsibility']!r}"
    )
    assert ac["sourced_from_product_key"] is None, (
        "platform_only control must have no product source"
    )


@pytest.mark.integration
def test_coverage_basis_counts_in_product_list(client, db_session):
    """Products endpoint reports coverage_basis breakdown correctly."""
    org = Organization(name=f"BasisCountOrg-{uuid.uuid4().hex[:6]}")
    fw = Framework(key=f"fw-basis-{uuid.uuid4().hex[:6]}", name="NIST r2", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrls = []
    for i, cid in enumerate(["AU.L2-3.3.1", "AU.L2-3.3.2", "AC.L2-3.1.11", "IA.L2-3.5.1"]):
        c = Control(
            framework_id=fw.id, control_id=cid, family=cid[:2],
            title=cid, requirement_text=".", sprs_weight=1, sequence_order=i,
        )
        db_session.add(c)
        ctrls.append(c)
    db_session.flush()

    for c in ctrls:
        db_session.add(AssessmentObjective(control_id=c.id, objective_key="a", text="."))
    db_session.flush()

    product = Product(
        framework_id=fw.id, key=f"basis-test-{uuid.uuid4().hex[:8]}",
        name="Basis Test Product", provider="Test", category="ESP",
        asset_type="SPA", role="Test.",
    )
    db_session.add(product)
    db_session.flush()

    # 2 customer_system, 1 platform_only, 1 customer_owns (no coverage_basis)
    db_session.add(BaselineControl(
        product_id=product.id, control_id=ctrls[0].id, objectives=["a"],
        classification="provider_satisfies", coverage_basis="customer_system",
        candidate_state="pending_evidence",
    ))
    db_session.add(BaselineControl(
        product_id=product.id, control_id=ctrls[1].id, objectives=["a"],
        classification="provider_satisfies", coverage_basis="customer_system",
        candidate_state="pending_evidence",
    ))
    db_session.add(BaselineControl(
        product_id=product.id, control_id=ctrls[2].id, objectives=["a"],
        classification="provider_satisfies", coverage_basis="platform_only",
        candidate_state="pending_evidence",
    ))
    db_session.add(BaselineControl(
        product_id=product.id, control_id=ctrls[3].id, objectives=["a"],
        classification="customer_owns", candidate_state="not_satisfied_by_product",
    ))
    db_session.flush()

    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=fw.id, name="Basis Count Test"
    )
    db_session.flush()

    r = client.get(f"/orgs/{org.id}/assessments/{assessment.id}/products")
    assert r.status_code == 200
    p = r.json()[0]

    assert p["customer_system_count"] == 2
    assert p["platform_only_count"] == 1
    assert p["assists_count"] == 0
    assert p["customer_owns_count"] == 1
