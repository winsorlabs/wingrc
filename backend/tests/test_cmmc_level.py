"""Integration tests for CMMC Level 1 practice flag.

Verifies that:
  - Exactly 17 controls are seeded with is_level_1=True
  - Specific controls have the correct flag value
  - The GET /control-states API exposes is_level_1 on each row

Run in-container:
    docker compose exec backend pytest tests/test_cmmc_level.py -m integration -v
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import Assessment, Control, Organization
from app.seeds.catalog import seed_catalog
from tests.conftest import _app_session, _authed


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db_session):
    """Seed the catalog and return the framework id."""
    result = seed_catalog(db_session)
    db_session.flush()
    return result


# ---------------------------------------------------------------------------
# Catalog seed tests
# ---------------------------------------------------------------------------

KNOWN_L1 = {
    "AC.L2-3.1.1", "AC.L2-3.1.2", "AC.L2-3.1.20", "AC.L2-3.1.22",
    "IA.L2-3.5.1", "IA.L2-3.5.2",
    "MP.L2-3.8.3",
    "PE.L2-3.10.1", "PE.L2-3.10.3", "PE.L2-3.10.4", "PE.L2-3.10.5",
    "SC.L2-3.13.1", "SC.L2-3.13.5",
    "SI.L2-3.14.1", "SI.L2-3.14.2", "SI.L2-3.14.4", "SI.L2-3.14.5",
}

KNOWN_L2_ONLY = {
    "AC.L2-3.1.3",   # CUI flow control — not L1
    "AU.L2-3.3.1",   # Audit logging — not L1
    "CM.L2-3.4.1",   # Baseline config — not L1
    "IA.L2-3.5.3",   # MFA — not L1
    "SC.L2-3.13.11", # FIPS crypto — not L1
}


@pytest.mark.integration
def test_l1_count_is_17(db_session, seeded):
    controls = db_session.scalars(
        select(Control).where(Control.is_level_1.is_(True))
    ).all()
    assert len(controls) == 17, (
        f"Expected 17 L1 controls, got {len(controls)}: "
        f"{[c.control_id for c in controls]}"
    )


@pytest.mark.integration
def test_known_l1_controls_flagged(db_session, seeded):
    for cid in KNOWN_L1:
        ctrl = db_session.scalars(
            select(Control).where(Control.control_id == cid)
        ).first()
        assert ctrl is not None, f"Control {cid} not found in seed"
        assert ctrl.is_level_1 is True, f"{cid} should be is_level_1=True"


@pytest.mark.integration
def test_known_l2_only_controls_not_flagged(db_session, seeded):
    for cid in KNOWN_L2_ONLY:
        ctrl = db_session.scalars(
            select(Control).where(Control.control_id == cid)
        ).first()
        assert ctrl is not None, f"Control {cid} not found in seed"
        assert ctrl.is_level_1 is False, f"{cid} should be is_level_1=False"


@pytest.mark.integration
def test_total_controls_is_110(db_session, seeded):
    total = db_session.scalars(select(Control)).all()
    assert len(total) == 110


# ---------------------------------------------------------------------------
# API test: is_level_1 exposed in GET /control-states
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_control_states_expose_is_level_1(client, db_session, seeded, fake_msp_admin):
    from app.engine import _seed_control_states

    fw_id = seeded["framework_id"]

    org = Organization(id=fake_msp_admin.org_id, name=f"L1TestOrg-{uuid.uuid4().hex}")
    db_session.add(org)
    db_session.flush()

    assessment = Assessment(org_id=org.id, framework_id=fw_id, name="L1 Test")
    db_session.add(assessment)
    db_session.flush()

    _seed_control_states(db_session, org.id, fw_id, assessment.id)
    db_session.flush()

    r = client.get(f"/orgs/{org.id}/assessments/{assessment.id}/control-states")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) > 0

    l1_rows = [row for row in rows if row["is_level_1"] is True]
    l2_rows = [row for row in rows if row["is_level_1"] is False]

    assert len(l1_rows) > 0, "No L1 rows returned"
    assert len(l2_rows) > len(l1_rows), "L2-only rows should outnumber L1 rows"

    ac_l1 = [row for row in l1_rows if row["control_id"] == "AC.L2-3.1.1"]
    assert len(ac_l1) > 0, "AC.L2-3.1.1 should appear with is_level_1=True"

    au_l2 = [row for row in rows if row["control_id"] == "AU.L2-3.3.1"]
    assert all(row["is_level_1"] is False for row in au_l2), (
        "AU.L2-3.3.1 should not be L1"
    )
