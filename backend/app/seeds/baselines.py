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

import uuid
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
) -> dict:
    pd = data["product"]

    product = session.scalars(
        select(Product).where(Product.key == pd["key"])
    ).first()
    if product is None:
        product = Product(
            framework_id=fw.id,
            key=pd["key"],
            name=pd["name"],
            provider=pd["provider"],
            category=pd["category"],
            asset_type=pd.get("asset_type", "SPA"),
            role=pd.get("role", "").strip(),
            assumed_config=pd.get("assumed_config", []),
            is_published=False,
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
    return {"baseline_controls": bcs_written, "evidence_specs": specs_written, "missing": missing}
