"""Idempotent seed for the product baseline library.

Loads every *.yaml file in backend/baselines/ into product, baseline_control,
and baseline_evidence_spec.  Safe to call repeatedly — uses SELECT-then-upsert
for product and baseline_control; evidence specs are delete-and-replace per
baseline_control (no natural key to upsert on).

Usage (CLI):
    wingrc seed-baselines
    wingrc seed-baselines --db-url postgresql+psycopg://...

Usage (Python):
    from app.seeds.baselines import seed_baselines
    result = seed_baselines(session)
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import (
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    EvidenceTask,
    Framework,
    Product,
)

# baselines/ lives alongside app/ inside the backend/ tree so it is
# accessible inside the Docker container (./backend is mounted as /app).
_BASELINES_DIR = Path(__file__).parents[2] / "baselines"
_FRAMEWORK_KEY = "nist-800-171-r2"

# Lowercase, hyphen-separated -- the shape every baseline already follows
# (baselines/rocketcyber.yaml). No leading/trailing/double hyphens.
_PRODUCT_KEY_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def normalize_product_key(raw: str) -> str:
    """Lowercase and validate a product key against the slug shape every
    baseline already follows. `_seed_product` below is the single write
    path for `Product.key` (the CLI seed of `baselines/*.yaml`, the admin
    YAML import, and the document-ingestion endpoint's `product_key` field
    all funnel through here or call this directly) -- so normalization
    only has to be right in one place for every path to agree on identity.

    Lowercasing is silent, deliberate normalization: "DattoRMM" and
    "dattormm" are unambiguously the same product, and treating them as
    two rows would silently fork one tool's baseline into two candidates.
    Anything containing a character outside [a-z0-9-] is rejected outright
    rather than mangled into something the caller didn't type -- "Datto
    RMM" (a space) becomes an error naming the expected shape, never a
    silent "datto-rmm".

    Raises ValueError with a message safe to surface directly to a user
    (an HTTP 422 detail, or a baseline_import.py validation problem).
    """
    normalized = raw.strip().lower()
    if not _PRODUCT_KEY_PATTERN.match(normalized):
        raise ValueError(
            f"Product key {raw!r} is not valid -- use lowercase letters, digits, "
            "and hyphens only, with no leading/trailing/double hyphens "
            "(e.g. 'datto-rmm'). No spaces or other punctuation."
        )
    return normalized


def seed_baselines(session: Session) -> dict[str, Any]:
    """Load all baseline YAML files into *session*.  Idempotent."""
    fw = session.scalars(
        select(Framework).where(Framework.key == _FRAMEWORK_KEY)
    ).first()
    if fw is None:
        raise RuntimeError(
            f"Framework '{_FRAMEWORK_KEY}' not found — run 'wingrc seed-catalog' first."
        )

    # Build control lookup once: control_id_str -> Control row
    ctrl_lookup: dict[str, Control] = {
        c.control_id: c
        for c in session.scalars(
            select(Control).where(Control.framework_id == fw.id)
        ).all()
    }

    total_products = 0
    total_bcs = 0
    total_specs = 0
    missing: list[str] = []

    for yaml_path in sorted(_BASELINES_DIR.glob("*.yaml")):
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        r = _seed_product(session, fw, ctrl_lookup, data)
        total_products += 1
        total_bcs += r["baseline_controls"]
        total_specs += r["evidence_specs"]
        missing.extend(r["missing"])

    session.flush()
    return {
        "products": total_products,
        "baseline_controls": total_bcs,
        "evidence_specs": total_specs,
        "missing_controls": missing,
    }


def _seed_product(
    session: Session,
    fw: Framework,
    ctrl_lookup: dict[str, Control],
    data: dict,
    *,
    reset_published: bool = False,
) -> dict:
    """Upsert one product + its baseline_control/evidence_spec rows.

    reset_published: False for this module's own callers (the CLI seeding
    git-tracked baseline files at deploy time -- a lower-stakes trust
    boundary, since those files are reviewed in git; re-running the seed
    must never silently un-publish a tool a deployment already exposed to
    its tenants). The Tools admin screen's import path (baseline_import.py)
    passes True: an admin-uploaded YAML is a higher-stakes boundary, and
    G.9's whole point is that publishing is a deliberate act, not
    something that survives a re-import unattended -- re-importing an
    already-published product's mapping must force a fresh review, not
    carry the old publish decision forward onto new (possibly different)
    compliance claims.
    """
    pd = data["product"]
    product_key = normalize_product_key(pd["key"])

    # Permanent AI-generation provenance: set only when the incoming YAML
    # explicitly carries it, and never cleared on a re-import that omits it
    # -- once a baseline is known to be AI-generated, that fact is permanent
    # (matches AssessmentObjective.practitioner_notes_generated_at/_model).
    ai_generated_at = (
        datetime.fromisoformat(pd["ai_generated_at"])
        if pd.get("ai_generated_at")
        else None
    )
    ai_generated_model = pd.get("ai_generated_model")

    product = session.scalars(
        select(Product).where(Product.key == product_key)
    ).first()
    if product is None:
        product = Product(
            framework_id=fw.id,
            key=product_key,
            name=pd["name"],
            provider=pd["provider"],
            category=pd["category"],
            asset_type=pd.get("asset_type", "SPA"),
            role=pd.get("role", "").strip(),
            assumed_config=pd.get("assumed_config", []),
            source_docs=pd.get("source_docs", []),
            is_published=False,
            ai_generated_at=ai_generated_at,
            ai_generated_model=ai_generated_model,
        )
        session.add(product)
    else:
        product.framework_id = fw.id
        product.name = pd["name"]
        product.provider = pd["provider"]
        product.category = pd["category"]
        product.asset_type = pd.get("asset_type", "SPA")
        product.role = pd.get("role", "").strip()
        product.assumed_config = pd.get("assumed_config", [])
        product.source_docs = pd.get("source_docs", [])
        if ai_generated_at is not None:
            product.ai_generated_at = ai_generated_at
        if ai_generated_model is not None:
            product.ai_generated_model = ai_generated_model
        if reset_published:
            product.is_published = False
    session.flush()

    bcs_written = 0
    specs_written = 0
    missing: list[str] = []

    for entry in data.get("controls", []):
        # "control" can be a single string or a list (e.g. the IA batch)
        ctrl_ids = entry["control"]
        if isinstance(ctrl_ids, str):
            ctrl_ids = [ctrl_ids]

        # Rows from one multi-control YAML entry share a batch_group_id
        batch_id = uuid.uuid4() if len(ctrl_ids) > 1 else None

        for ctrl_id_str in ctrl_ids:
            ctrl = ctrl_lookup.get(ctrl_id_str)
            if ctrl is None:
                missing.append(ctrl_id_str)
                continue

            bc = session.scalars(
                select(BaselineControl).where(
                    BaselineControl.product_id == product.id,
                    BaselineControl.control_id == ctrl.id,
                )
            ).first()
            if bc is None:
                bc = BaselineControl(
                    product_id=product.id,
                    control_id=ctrl.id,
                    objectives=entry.get("objectives") or [],
                    classification=entry["classification"],
                    coverage_basis=entry.get("coverage_basis", "customer_system"),
                    candidate_state=entry.get("candidate_state", "not_satisfied_by_product"),
                    provider_contribution=entry.get("provider_contribution"),
                    customer_action=entry.get("customer_action"),
                    note=entry.get("note"),
                    scope_note=entry.get("scope_note"),
                    batch_group_id=batch_id,
                )
                session.add(bc)
            else:
                bc.objectives = entry.get("objectives") or []
                bc.classification = entry["classification"]
                bc.coverage_basis = entry.get("coverage_basis", "customer_system")
                bc.candidate_state = entry.get("candidate_state", "not_satisfied_by_product")
                bc.provider_contribution = entry.get("provider_contribution")
                bc.customer_action = entry.get("customer_action")
                bc.note = entry.get("note")
                bc.scope_note = entry.get("scope_note")
                bc.batch_group_id = batch_id
            session.flush()

            # Delete-and-replace specs: no natural key to upsert by.
            # Nullify evidence_task.baseline_spec_id FK first — those tasks
            # survive reseed (they record what was collected) but lose the
            # spec pointer that will be replaced with a fresh row.
            old_specs = session.scalars(
                select(BaselineEvidenceSpec).where(
                    BaselineEvidenceSpec.baseline_control_id == bc.id
                )
            ).all()
            if old_specs:
                old_ids = [s.id for s in old_specs]
                session.execute(
                    update(EvidenceTask)
                    .where(EvidenceTask.baseline_spec_id.in_(old_ids))
                    .values(baseline_spec_id=None)
                )
                for s in old_specs:
                    session.delete(s)
                session.flush()

            for ev in entry.get("evidence") or []:
                session.add(
                    BaselineEvidenceSpec(
                        baseline_control_id=bc.id,
                        artifact_description=ev["artifact"],
                        evidence_type=ev["type"],
                        kb_reference=ev.get("kb"),
                    )
                )
                specs_written += 1

            bcs_written += 1

    session.flush()
    return {
        "baseline_controls": bcs_written,
        "evidence_specs": specs_written,
        "missing": missing,
        "product_key": product_key,
    }
