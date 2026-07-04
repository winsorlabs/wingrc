"""Integration tests for the CMMC L2 catalog seed.

Requires a running Postgres database; skipped otherwise.
Run with:  pytest tests/test_catalog_seed.py -m integration -v
"""
from __future__ import annotations

import pytest

from app.models import AssessmentObjective, Control, Framework
from app.seeds.catalog import seed_catalog


@pytest.mark.integration
def test_seed_creates_framework(db_session):
    result = seed_catalog(db_session)
    fw = db_session.get(Framework, result["framework_id"])
    assert fw is not None
    assert fw.key == "nist-800-171-r2"
    assert fw.version == "r2"


@pytest.mark.integration
def test_seed_control_count(db_session):
    result = seed_catalog(db_session)
    assert result["controls"] >= 100, (
        f"Expected ≥100 controls, got {result['controls']}"
    )


@pytest.mark.integration
def test_seed_objective_count(db_session):
    result = seed_catalog(db_session)
    assert result["objectives"] >= 200, (
        f"Expected ≥200 objectives, got {result['objectives']}"
    )


@pytest.mark.integration
def test_seed_all_satisfaction_types_present(db_session):
    seed_catalog(db_session)
    types_present = {
        row[0]
        for row in db_session.query(AssessmentObjective.satisfaction_type).distinct()
    }
    expected = {"product", "document_list", "scheduled_operation", "narrative"}
    assert expected == types_present, (
        f"Missing satisfaction_types: {expected - types_present}"
    )


@pytest.mark.integration
def test_seed_scheduled_operations_have_cadence(db_session):
    seed_catalog(db_session)
    bad = (
        db_session.query(AssessmentObjective)
        .filter(
            AssessmentObjective.satisfaction_type == "scheduled_operation",
            AssessmentObjective.cadence.is_(None),
        )
        .all()
    )
    assert bad == [], (
        f"{len(bad)} scheduled_operation objective(s) are missing cadence: "
        + ", ".join(f"{o.objective_key}" for o in bad)
    )


@pytest.mark.integration
def test_seed_all_objectives_are_draft(db_session):
    seed_catalog(db_session)
    non_draft = (
        db_session.query(AssessmentObjective)
        .filter(AssessmentObjective.is_draft.is_(False))
        .count()
    )
    assert non_draft == 0, f"{non_draft} objective(s) have is_draft=False"


@pytest.mark.integration
def test_seed_is_idempotent(db_session):
    r1 = seed_catalog(db_session)
    db_session.flush()
    r2 = seed_catalog(db_session)

    assert r1["controls"] == r2["controls"]
    assert r1["objectives"] == r2["objectives"]
    assert r1["framework_id"] == r2["framework_id"]

    total_controls = db_session.query(Control).count()
    assert total_controls == r1["controls"], (
        f"Duplicate controls created: DB has {total_controls}, expected {r1['controls']}"
    )
    total_objectives = db_session.query(AssessmentObjective).count()
    assert total_objectives == r1["objectives"], (
        f"Duplicate objectives created: DB has {total_objectives}, expected {r1['objectives']}"
    )


@pytest.mark.integration
def test_seed_families_present(db_session):
    seed_catalog(db_session)
    families = {
        row[0]
        for row in db_session.query(Control.family).distinct()
    }
    expected_families = {
        "AC", "AT", "AU", "CM", "IA", "IR", "MA", "MP",
        "PE", "PS", "RA", "CA", "SC", "SI",
    }
    missing = expected_families - families
    assert not missing, f"Missing control families: {missing}"
