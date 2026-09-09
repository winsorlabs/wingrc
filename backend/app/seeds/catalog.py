"""Idempotent CMMC L2 catalog seed.

Loads framework, all controls, and every assessment objective from
cmmc_l2.yaml into the database, plus two separately-sourced guidance
layers -- never blended together, per CLAUDE.md's compliance-content
discipline:

  cmmc_official_guidance.yaml  -- government-sourced, mechanically
    generated from the real CMMC Assessment Guide Level 2 PDF (see
    scripts/cmmc_guidance/README.md). Populates
    AssessmentObjective.official_guidance; official_guidance_source (the
    citation) is derived here from _GUIDE_VERSION/_GUIDE_DATE below plus
    the practice id + objective key, not stored redundantly 316 times in
    the data file.

  cmmc_practitioner_notes.yaml  -- AI-drafted, advisory. Populates
    AssessmentObjective.practitioner_notes. Always inserted as a draft
    (practitioner_notes_is_draft=True); see _upsert_objective's docstring
    for the upsert rule that protects a human's review from being
    silently overwritten by a later reseed.

Safe to call repeatedly -- uses SELECT-then-upsert so running it twice
yields identical state, EXCEPT for practitioner_notes once a human has
reviewed it (see above).

Usage (CLI):
    wingrc seed-catalog
    wingrc seed-catalog --db-url postgresql+psycopg://...

Usage (Python):
    from app.seeds.catalog import seed_catalog
    result = seed_catalog(session)
    # result = {"framework_id": ..., "controls": 110, "objectives": 320,
    #           "official_guidance": 316, "practitioner_notes": 316}

Source documents (all REVIEWABLE DRAFT -- requires C3PAO sign-off):
    NIST SP 800-171 Rev 2  https://doi.org/10.6028/NIST.SP.800-171r2
    NIST SP 800-171A Rev 2 https://doi.org/10.6028/NIST.SP.800-171Ar2
    CMMC Assessment Guide Level 2 v2
        https://dodcio.defense.gov/Portals/0/Documents/CMMC/AssessmentGuideL2v2.pdf
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AssessmentObjective, Control, Framework

_YAML_PATH = Path(__file__).parent / "cmmc_l2.yaml"
_OFFICIAL_GUIDANCE_PATH = Path(__file__).parent / "cmmc_official_guidance.yaml"
_PRACTITIONER_NOTES_PATH = Path(__file__).parent / "cmmc_practitioner_notes.yaml"

# Mirrors scripts/cmmc_guidance/compose_guidance_yaml.py's GUIDE_VERSION/
# GUIDE_DATE constants -- keep in sync if the guide is re-extracted against
# a newer revision.
_GUIDE_VERSION = "2.13"
_GUIDE_DATE = "September 2024"

# Practitioner-notes generation provenance, recorded on every freshly
# written row (see AssessmentObjective.practitioner_notes_generated_at/
# _model). Authored 2026-09-09 in this one batch; update both if a future
# batch regenerates or extends the notes. Noon UTC, not midnight: the
# frontend displays this with toLocaleDateString() in the viewer's local
# timezone, and a midnight-UTC timestamp renders as "the day before" for
# every timezone west of UTC (verified during this feature's own browser
# check) -- noon UTC is the same calendar day in every real-world
# timezone (UTC-12 through UTC+14).
_PRACTITIONER_NOTES_GENERATED_AT = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
_PRACTITIONER_NOTES_MODEL = "claude-sonnet-5"


def seed_catalog(session: Session) -> dict[str, Any]:
    """Load the CMMC L2 catalog into *session*.  Idempotent (see module
    docstring for the one exception: reviewed practitioner_notes)."""
    data = _load()
    official_guidance = _load_yaml_optional(_OFFICIAL_GUIDANCE_PATH)
    practitioner_notes = _load_yaml_optional(_PRACTITIONER_NOTES_PATH)

    fw = _upsert_framework(session, data["framework"])
    session.flush()

    controls_written = 0
    objectives_written = 0
    official_guidance_written = 0
    practitioner_notes_written = 0

    for seq, ctrl_data in enumerate(data["controls"], start=1):
        ctrl = _upsert_control(session, fw.id, ctrl_data, seq)
        session.flush()
        pid = ctrl_data["id"]
        for obj_data in ctrl_data.get("objectives", []):
            obj, wrote_official, wrote_notes = _upsert_objective(
                session,
                ctrl.id,
                pid,
                obj_data,
                official_guidance.get(pid, {}),
                practitioner_notes.get(pid, {}),
            )
            objectives_written += 1
            official_guidance_written += wrote_official
            practitioner_notes_written += wrote_notes
        controls_written += 1

    session.flush()
    return {
        "framework_id": fw.id,
        "controls": controls_written,
        "objectives": objectives_written,
        "official_guidance": official_guidance_written,
        "practitioner_notes": practitioner_notes_written,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load() -> dict:
    with open(_YAML_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_yaml_optional(path: Path) -> dict:
    """Returns {} if the file doesn't exist -- lets the catalog seed run
    (structural data only) even before the guidance pipeline has produced
    its data files, e.g. in a fresh checkout before running the
    scripts/cmmc_guidance/ pipeline."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _upsert_framework(session: Session, fw_data: dict) -> Framework:
    fw = session.scalars(
        select(Framework).where(Framework.key == fw_data["key"])
    ).first()
    if fw is None:
        fw = Framework(
            id=uuid.uuid4(),
            key=fw_data["key"],
            name=fw_data["name"],
            version=fw_data["version"],
        )
        session.add(fw)
    else:
        fw.name = fw_data["name"]
        fw.version = fw_data["version"]
    return fw


def _upsert_control(
    session: Session,
    framework_id: uuid.UUID,
    data: dict,
    seq: int,
) -> Control:
    ctrl = session.scalars(
        select(Control).where(
            Control.framework_id == framework_id,
            Control.control_id == data["id"],
        )
    ).first()
    is_level_1 = data.get("level", 2) == 1
    if ctrl is None:
        ctrl = Control(
            id=uuid.uuid4(),
            framework_id=framework_id,
            control_id=data["id"],
            family=data["family"],
            title=data["title"],
            requirement_text=data["req"],
            sprs_weight=data.get("weight", 1),
            is_level_1=is_level_1,
            sequence_order=data.get("seq", seq),
            discussion=data.get("discussion"),
        )
        session.add(ctrl)
    else:
        ctrl.family = data["family"]
        ctrl.title = data["title"]
        ctrl.requirement_text = data["req"]
        ctrl.sprs_weight = data.get("weight", 1)
        ctrl.is_level_1 = is_level_1
        ctrl.sequence_order = data.get("seq", seq)
        ctrl.discussion = data.get("discussion")
    return ctrl


def _should_write_practitioner_notes(existing: AssessmentObjective | None) -> bool:
    """Reseeding must never clobber a human's review of an AI-drafted
    note. Write practitioner_notes on a fresh insert (existing is None)
    or when the current row is still an untouched draft -- either never
    populated (practitioner_notes is None) or populated but not yet
    reviewed (practitioner_notes_is_draft is still True, the seed-time
    default). Once a human reviews a note and flips is_draft to False,
    every later reseed leaves practitioner_notes/model/generated_at alone
    -- even if the source YAML's text for that objective has since
    changed. That's a deliberate trade-off (documented in
    backend/app/seeds/README-ish module docstring above and in
    ROADMAP.md's writeup for this feature): a content improvement to an
    already-reviewed note requires a human to re-touch that row, the same
    way improving a workbook importer's parsing never silently rewrites
    an org's already-confirmed scope_entity data.

    official_guidance has no equivalent protection -- see _upsert_objective
    for why that's intentional, not an oversight.
    """
    return (
        existing is None
        or existing.practitioner_notes_is_draft
        or existing.practitioner_notes is None
    )


def _upsert_objective(
    session: Session,
    control_id: uuid.UUID,
    practice_id: str,
    data: dict,
    official_guidance_for_control: dict[str, str],
    practitioner_notes_for_control: dict[str, str],
) -> tuple[AssessmentObjective, int, int]:
    okey = data["key"]
    obj = session.scalars(
        select(AssessmentObjective).where(
            AssessmentObjective.control_id == control_id,
            AssessmentObjective.objective_key == okey,
        )
    ).first()
    sat_type = data.get("type", "narrative")
    cadence = data.get("cadence")
    cadence_resp = data.get("cadence_resp")

    official_text = official_guidance_for_control.get(okey)
    official_source = (
        f"CMMC Assessment Guide – Level 2, Version {_GUIDE_VERSION} ({_GUIDE_DATE}) "
        f"— {practice_id}[{okey}]"
        if official_text
        else None
    )
    notes_text = practitioner_notes_for_control.get(okey)

    is_new = obj is None
    if obj is None:
        obj = AssessmentObjective(
            id=uuid.uuid4(),
            control_id=control_id,
            objective_key=okey,
            text=data["text"],
            satisfaction_type=sat_type,
            cadence=cadence,
            cadence_responsibility=cadence_resp,
            is_draft=True,
        )
        session.add(obj)
    else:
        obj.text = data["text"]
        obj.satisfaction_type = sat_type
        obj.cadence = cadence
        obj.cadence_responsibility = cadence_resp
        obj.is_draft = True

    # official_guidance is verbatim-derived from the real Assessment Guide
    # PDF, not human-editable prose -- unlike practitioner_notes, there is
    # no legitimate "a human improved on this" case to protect against, so
    # it always tracks the source data file (the same "always overwrite"
    # behavior every other structural field on this row already has). A
    # transcription fix to cmmc_official_guidance.yaml should always reach
    # every deployment's DB on the next reseed.
    obj.official_guidance = official_text
    obj.official_guidance_source = official_source
    wrote_official = 1 if official_text else 0

    wrote_notes = 0
    if notes_text is not None and _should_write_practitioner_notes(obj if not is_new else None):
        obj.practitioner_notes = notes_text
        obj.practitioner_notes_is_draft = True
        obj.practitioner_notes_generated_at = _PRACTITIONER_NOTES_GENERATED_AT
        obj.practitioner_notes_model = _PRACTITIONER_NOTES_MODEL
        wrote_notes = 1

    return obj, wrote_official, wrote_notes
