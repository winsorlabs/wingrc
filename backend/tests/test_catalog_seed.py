"""Integration tests for the CMMC L2 catalog seed.

Requires a running Postgres database; skipped otherwise.
Run with:  pytest tests/test_catalog_seed.py -m integration -v

The one exception is test_catalog_matches_authoritative_reference below --
a pure YAML-to-YAML comparison with no DB involved, deliberately left
unmarked (no @pytest.mark.integration, no db_session fixture) so it runs
in CI's fast `backend` job too, not only `integration`. That's the whole
point of it as a drift guard: a silent catalog gap should fail CI on the
very next PR that touches cmmc_l2.yaml, not wait for the `integration`
job's Postgres service container -- or for another unrelated task to
notice by hand, which is how the 4-objective gap this guards against
actually surfaced.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from app.models import AssessmentObjective, Control, Framework
from app.seeds.catalog import seed_catalog

_SEEDS_DIR = Path(__file__).parent.parent / "app" / "seeds"


def test_catalog_matches_authoritative_reference():
    """cmmc_l2.yaml's objective key set, per practice, must exactly match
    cmmc_catalog_reference.yaml -- a committed, PDF-derived reference
    (scripts/cmmc_guidance/gen_catalog_reference.py), independent of
    cmmc_l2.yaml itself. Checking a file against a reference derived from
    that same file would never catch drift; this reference is generated
    straight from the real Assessment Guide extraction.

    If this fails: either cmmc_l2.yaml has a real gap/extra/mis-keyed
    objective (fix cmmc_l2.yaml -- see scripts/cmmc_guidance/
    reconcile_catalog.py to find exactly what's wrong), or the guide was
    legitimately re-extracted and cmmc_catalog_reference.yaml needs
    regenerating to match (only after reconciling cmmc_l2.yaml against
    the new extraction first -- see that script's own module docstring).
    """
    catalog = yaml.safe_load((_SEEDS_DIR / "cmmc_l2.yaml").read_text(encoding="utf-8"))
    reference = yaml.safe_load(
        (_SEEDS_DIR / "cmmc_catalog_reference.yaml").read_text(encoding="utf-8")
    )

    catalog_keys = {
        c["id"]: sorted(o["key"] for o in c.get("objectives", [])) for c in catalog["controls"]
    }

    assert set(catalog_keys) == set(reference), (
        f"Practice id mismatch.\n"
        f"  in catalog but not reference: {sorted(set(catalog_keys) - set(reference))}\n"
        f"  in reference but not catalog: {sorted(set(reference) - set(catalog_keys))}"
    )

    mismatches = {
        pid: (catalog_keys[pid], reference[pid])
        for pid in catalog_keys
        if catalog_keys[pid] != reference[pid]
    }
    assert not mismatches, "Objective key drift from the authoritative reference:\n" + "\n".join(
        f"  {pid}: catalog={cat}  reference={ref}" for pid, (cat, ref) in mismatches.items()
    )


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
def test_practitioner_notes_untouched_on_first_seed(db_session):
    """Migration 0032: there's no draft/reviewed flag any more. A freshly
    seeded note has practitioner_notes_edited_at/_by both NULL (nobody has
    edited it yet) and practitioner_notes_original populated (frozen copy
    for a future revert)."""
    seed_catalog(db_session)
    edited_with_notes = (
        db_session.query(AssessmentObjective)
        .filter(
            AssessmentObjective.practitioner_notes.isnot(None),
            AssessmentObjective.practitioner_notes_edited_at.isnot(None),
        )
        .count()
    )
    assert edited_with_notes == 0, "A freshly-seeded practitioner note should not show as edited"

    missing_original = (
        db_session.query(AssessmentObjective)
        .filter(
            AssessmentObjective.practitioner_notes.isnot(None),
            AssessmentObjective.practitioner_notes_original.is_(None),
        )
        .count()
    )
    assert missing_original == 0, "A freshly-seeded practitioner note has no original to revert to"


@pytest.mark.integration
def test_reseed_does_not_overwrite_an_edited_practitioner_note(db_session):
    """The whole point of the edited_at signal (successor to the old
    practitioner_notes_is_draft flag): once an msp_admin edits a note,
    re-running seed_catalog must never silently overwrite it -- the same
    "candidates, never auto-met" discipline this codebase already applies
    to control state. Edited (not reviewed) is deliberate: editing doesn't
    make a note authoritative, it just means reseed should leave it alone."""
    seed_catalog(db_session)
    obj = (
        db_session.query(AssessmentObjective)
        .filter(AssessmentObjective.practitioner_notes.isnot(None))
        .first()
    )
    assert obj is not None, "fixture assumption: at least one objective has practitioner_notes"

    obj.practitioner_notes = "Edited and rewritten by an msp_admin."
    obj.practitioner_notes_edited_at = datetime.now(UTC)
    db_session.flush()

    seed_catalog(db_session)
    db_session.refresh(obj)

    assert obj.practitioner_notes == "Edited and rewritten by an msp_admin."
    assert obj.practitioner_notes_edited_at is not None


@pytest.mark.integration
def test_reseed_still_updates_an_unedited_note(db_session):
    """Contrast with the edited case above: an untouched note (the
    seed-time default, edited_at still NULL) should keep tracking the
    source YAML on reseed -- otherwise a content fix to
    cmmc_practitioner_notes.yaml would never reach any row, edited or
    not."""
    seed_catalog(db_session)
    obj = (
        db_session.query(AssessmentObjective)
        .filter(AssessmentObjective.practitioner_notes.isnot(None))
        .first()
    )
    original_text = obj.practitioner_notes
    assert obj.practitioner_notes_edited_at is None

    obj.practitioner_notes = "stale placeholder, still unedited"
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
