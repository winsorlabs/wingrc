"""Integration tests for the CMMC L2 catalog seed.

Requires a running Postgres database; skipped otherwise.
Run with:  pytest tests/test_catalog_seed.py -m integration -v
"""
from __future__ import annotations

from pathlib import Path

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

    fw_id = r1["framework_id"]
    total_controls = (
        db_session.query(Control).filter(Control.framework_id == fw_id).count()
    )
    assert total_controls == r1["controls"], (
        f"Duplicate controls created: DB has {total_controls}, expected {r1['controls']}"
    )
    total_objectives = (
        db_session.query(AssessmentObjective)
        .join(Control, AssessmentObjective.control_id == Control.id)
        .filter(Control.framework_id == fw_id)
        .count()
    )
    assert total_objectives == r1["objectives"], (
        f"Duplicate objectives created: DB has {total_objectives}, expected {r1['objectives']}"
    )


@pytest.mark.integration
def test_guidance_text_loads_clean(db_session):
    """All YAML text fields (discussion, guidance, req, title) are ASCII-printable.

    Allowed: codepoints 32-126 plus newline, carriage return, and tab.
    Disallowed: smart quotes, em-dashes, mojibake, non-printable bytes.
    """
    _YAML_PATH = Path(__file__).parent.parent / "app" / "seeds" / "cmmc_l2.yaml"
    raw = _YAML_PATH.read_text(encoding="utf-8")

    bad: list[tuple[int, str]] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        for col, ch in enumerate(line, start=1):
            if ord(ch) > 126 or (ord(ch) < 32 and ch not in "\n\r\t"):
                bad.append((lineno, f"col {col}: U+{ord(ch):04X} {ch!r}"))

    assert not bad, (
        "Non-ASCII-printable characters found in cmmc_l2.yaml "
        "(first 5):\n" + "\n".join(f"  line {ln}: {desc}" for ln, desc in bad[:5])
    )


@pytest.mark.integration
def test_official_guidance_seeded_for_every_objective(db_session):
    """Every objective gets official_guidance after seeding -- the whole
    point of splitting official_guidance out of the old ad hoc `guidance`
    field was to eliminate the sparsity where "Show guidance" appeared on
    some objectives and not others (see cmmc_official_guidance.yaml's own
    header: every practice has Examine/Interview/Test text, so every
    objective gets a non-empty body)."""
    result = seed_catalog(db_session)
    without_guidance = (
        db_session.query(AssessmentObjective)
        .filter(AssessmentObjective.official_guidance.is_(None))
        .count()
    )
    assert without_guidance == 0, f"{without_guidance} objective(s) have no official_guidance"
    assert result["official_guidance"] == result["objectives"]


@pytest.mark.integration
def test_official_guidance_source_set_whenever_guidance_is(db_session):
    seed_catalog(db_session)
    bad = (
        db_session.query(AssessmentObjective)
        .filter(
            AssessmentObjective.official_guidance.isnot(None),
            AssessmentObjective.official_guidance_source.is_(None),
        )
        .count()
    )
    assert bad == 0, f"{bad} objective(s) have official_guidance but no source citation"


@pytest.mark.integration
def test_official_guidance_yaml_loads_clean(db_session):
    """Same ASCII-printable discipline as cmmc_l2.yaml (see
    test_guidance_text_loads_clean above) -- allow_unicode=False in
    scripts/cmmc_guidance/compose_guidance_yaml.py's yaml.dump call is
    what keeps this true; a regression there would reintroduce raw UTF-8
    (en dashes, etc.) instead of \\uXXXX escapes."""
    path = Path(__file__).parent.parent / "app" / "seeds" / "cmmc_official_guidance.yaml"
    raw = path.read_text(encoding="utf-8")
    bad = [
        (lineno, f"col {col}: U+{ord(ch):04X} {ch!r}")
        for lineno, line in enumerate(raw.splitlines(), start=1)
        for col, ch in enumerate(line, start=1)
        if ord(ch) > 126 or (ord(ch) < 32 and ch not in "\n\r\t")
    ]
    assert not bad, (
        "Non-ASCII-printable characters found in cmmc_official_guidance.yaml "
        "(first 5):\n" + "\n".join(f"  line {ln}: {desc}" for ln, desc in bad[:5])
    )


@pytest.mark.integration
def test_practitioner_notes_yaml_loads_clean(db_session):
    """Same ASCII-printable discipline as the other two catalog data
    files -- written with plain `--` and straight quotes throughout
    rather than em dashes/curly quotes, verified here rather than
    trusted."""
    path = Path(__file__).parent.parent / "app" / "seeds" / "cmmc_practitioner_notes.yaml"
    raw = path.read_text(encoding="utf-8")
    bad = [
        (lineno, f"col {col}: U+{ord(ch):04X} {ch!r}")
        for lineno, line in enumerate(raw.splitlines(), start=1)
        for col, ch in enumerate(line, start=1)
        if ord(ch) > 126 or (ord(ch) < 32 and ch not in "\n\r\t")
    ]
    assert not bad, (
        "Non-ASCII-printable characters found in cmmc_practitioner_notes.yaml "
        "(first 5):\n" + "\n".join(f"  line {ln}: {desc}" for ln, desc in bad[:5])
    )


@pytest.mark.integration
def test_practitioner_notes_cover_every_seeded_objective(db_session):
    """Cross-check against the real catalog rather than trusting the count
    alone -- catches a stray/mismatched key (found once during authoring:
    an extra CM.L2-3.4.1[g] that didn't correspond to a real objective)."""
    result = seed_catalog(db_session)
    assert result["practitioner_notes"] == result["objectives"], (
        f"{result['objectives'] - result['practitioner_notes']} objective(s) "
        "have no practitioner note"
    )


@pytest.mark.integration
def test_practitioner_notes_are_draft_on_first_seed(db_session):
    seed_catalog(db_session)
    non_draft_with_notes = (
        db_session.query(AssessmentObjective)
        .filter(
            AssessmentObjective.practitioner_notes.isnot(None),
            AssessmentObjective.practitioner_notes_is_draft.is_(False),
        )
        .count()
    )
    assert non_draft_with_notes == 0, "A freshly-seeded practitioner note is not marked draft"


@pytest.mark.integration
def test_reseed_does_not_overwrite_a_reviewed_practitioner_note(db_session):
    """The whole point of practitioner_notes_is_draft: once a qualified
    human reviews a note (flips is_draft to False, optionally edits the
    text), re-running seed_catalog must never silently overwrite it --
    the same "candidates, never auto-met" discipline this codebase already
    applies to control state."""
    seed_catalog(db_session)
    obj = (
        db_session.query(AssessmentObjective)
        .filter(AssessmentObjective.practitioner_notes.isnot(None))
        .first()
    )
    assert obj is not None, "fixture assumption: at least one objective has practitioner_notes"

    obj.practitioner_notes = "Reviewed and rewritten by a human C3PAO."
    obj.practitioner_notes_is_draft = False
    db_session.flush()

    seed_catalog(db_session)
    db_session.refresh(obj)

    assert obj.practitioner_notes == "Reviewed and rewritten by a human C3PAO."
    assert obj.practitioner_notes_is_draft is False


@pytest.mark.integration
def test_reseed_still_updates_an_unreviewed_draft_note(db_session):
    """Contrast with the reviewed case above: an untouched draft (the
    seed-time default) should keep tracking the source YAML on reseed --
    otherwise a content fix to cmmc_practitioner_notes.yaml would never
    reach any row, reviewed or not."""
    seed_catalog(db_session)
    obj = (
        db_session.query(AssessmentObjective)
        .filter(AssessmentObjective.practitioner_notes.isnot(None))
        .first()
    )
    original_text = obj.practitioner_notes
    assert obj.practitioner_notes_is_draft is True

    obj.practitioner_notes = "stale placeholder, still unreviewed"
    db_session.flush()

    seed_catalog(db_session)
    db_session.refresh(obj)

    assert obj.practitioner_notes == original_text


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
