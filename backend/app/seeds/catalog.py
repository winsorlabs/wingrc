"""Idempotent CMMC L2 catalog seed.

Loads framework, all controls, and every assessment objective from
cmmc_l2.yaml into the database.  Safe to call repeatedly — uses
SELECT-then-upsert so running it twice yields identical state.

Usage (CLI):
    wingrc seed-catalog
    wingrc seed-catalog --db-url postgresql+psycopg://...

Usage (Python):
    from app.seeds.catalog import seed_catalog
    result = seed_catalog(session)
    # result = {"framework_id": ..., "controls": 110, "objectives": 320}

Source documents (all REVIEWABLE DRAFT — requires C3PAO sign-off):
    NIST SP 800-171 Rev 2  https://doi.org/10.6028/NIST.SP.800-171r2
    NIST SP 800-171A Rev 2 https://doi.org/10.6028/NIST.SP.800-171Ar2
    CMMC Assessment Guide Level 2 v2
        https://dodcio.defense.gov/Portals/0/Documents/CMMC/AssessmentGuideL2v2.pdf
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AssessmentObjective, Control, Framework

_YAML_PATH = Path(__file__).parent / "cmmc_l2.yaml"


def seed_catalog(session: Session) -> dict[str, Any]:
    """Load the CMMC L2 catalog into *session*.  Idempotent."""
    data = _load()
    fw = _upsert_framework(session, data["framework"])
    session.flush()

    controls_written = 0
    objectives_written = 0

    for seq, ctrl_data in enumerate(data["controls"], start=1):
        ctrl = _upsert_control(session, fw.id, ctrl_data, seq)
        session.flush()
        for obj_data in ctrl_data.get("objectives", []):
            _upsert_objective(session, ctrl.id, obj_data)
            objectives_written += 1
        controls_written += 1

    session.flush()
    return {
        "framework_id": fw.id,
        "controls": controls_written,
        "objectives": objectives_written,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load() -> dict:
    with open(_YAML_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


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
    if ctrl is None:
        ctrl = Control(
            id=uuid.uuid4(),
            framework_id=framework_id,
            control_id=data["id"],
            family=data["family"],
            title=data["title"],
            requirement_text=data["req"],
            sprs_weight=data.get("weight", 1),
            sequence_order=data.get("seq", seq),
            discussion=data.get("discussion"),
        )
        session.add(ctrl)
    else:
        ctrl.family = data["family"]
        ctrl.title = data["title"]
        ctrl.requirement_text = data["req"]
        ctrl.sprs_weight = data.get("weight", 1)
        ctrl.sequence_order = data.get("seq", seq)
        ctrl.discussion = data.get("discussion")
    return ctrl


def _upsert_objective(
    session: Session,
    control_id: uuid.UUID,
    data: dict,
) -> AssessmentObjective:
    obj = session.scalars(
        select(AssessmentObjective).where(
            AssessmentObjective.control_id == control_id,
            AssessmentObjective.objective_key == data["key"],
        )
    ).first()
    sat_type = data.get("type", "narrative")
    cadence = data.get("cadence")
    cadence_resp = data.get("cadence_resp")
    guidance = data.get("guidance")
    if obj is None:
        obj = AssessmentObjective(
            id=uuid.uuid4(),
            control_id=control_id,
            objective_key=data["key"],
            text=data["text"],
            satisfaction_type=sat_type,
            cadence=cadence,
            cadence_responsibility=cadence_resp,
            is_draft=True,
            guidance=guidance,
        )
        session.add(obj)
    else:
        obj.text = data["text"]
        obj.satisfaction_type = sat_type
        obj.cadence = cadence
        obj.cadence_responsibility = cadence_resp
        obj.is_draft = True
        obj.guidance = guidance
    return obj
