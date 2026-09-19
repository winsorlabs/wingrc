"""Idempotent seed for the product baseline library.

Loads every *.yaml file in backend/baselines/ into product,
product_baseline_version, baseline_control, and baseline_evidence_spec.

Baseline versioning (roadmap item P, migration 0054): a control mapping is
never mutated in place. Each call diffs the incoming YAML against the
product's CURRENT version's baseline_control/baseline_evidence_spec
content. If nothing differs, the call is a true no-op (existing version
reused, nothing written, is_published untouched). If anything differs --
including a control disappearing entirely, which the old upsert-by-key
code silently never noticed -- a brand new ProductBaselineVersion is
created with entirely fresh BaselineControl/BaselineEvidenceSpec rows for
every control the new import lists. The previous version's rows are never
touched, so any OrgProduct still pinned to it (engine.py's magic loop
reads OrgProduct.baseline_version_id, not "whatever's current") keeps
seeing exactly what it always saw. See ProductBaselineVersion's own
docstring for the full reasoning.

Product-level fields (name, provider, category, role, assumed_config,
source_docs, ai_generated_*) are NOT versioned -- only the control mapping
and its evidence specs are. Those are display/provenance metadata, not a
compliance claim; there is no "which version of the product's name was a
tenant's activation based on" question worth answering.

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
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    Framework,
    Product,
    ProductBaselineVersion,
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
    total_versions_created = 0
    missing: list[str] = []

    for yaml_path in sorted(_BASELINES_DIR.glob("*.yaml")):
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        r = _seed_product(session, fw, ctrl_lookup, data)
        total_products += 1
        total_bcs += r["baseline_controls"]
        total_specs += r["evidence_specs"]
        total_versions_created += 1 if r["version_created"] else 0
        missing.extend(r["missing"])

    session.flush()
    return {
        "products": total_products,
        "baseline_controls": total_bcs,
        "evidence_specs": total_specs,
        "versions_created": total_versions_created,
        "missing_controls": missing,
    }


def _evidence_sort_key(t: tuple[str, str, str | None]) -> tuple[str, str, str]:
    """Sort key for an (artifact, type, kb) evidence tuple. kb can be None;
    coercing it to "" avoids "'<' not supported between NoneType and str"
    the moment two specs share artifact+type and the comparison reaches
    the kb element."""
    return (t[0], t[1], t[2] or "")


def _entry_signature(row: dict[str, Any]) -> tuple:
    """Comparable snapshot of one control's compliance-relevant content --
    everything that would change what a tenant's activation actually
    claims. batch_group_id is deliberately excluded: it's a display-only
    grouping of YAML rows, not a claim, so a pure regrouping with no other
    change must not mint a new version.
    """
    return (
        tuple(sorted(row["objectives"])),
        row["classification"],
        row["coverage_basis"],
        row["candidate_state"],
        row["provider_contribution"],
        row["customer_action"],
        row["note"],
        row["scope_note"],
        tuple(sorted(row["evidence"], key=_evidence_sort_key)),
    )


def _existing_signature(
    bc: BaselineControl, specs: list[BaselineEvidenceSpec]
) -> tuple:
    return (
        tuple(sorted(bc.objectives or [])),
        bc.classification,
        bc.coverage_basis,
        bc.candidate_state,
        bc.provider_contribution,
        bc.customer_action,
        bc.note,
        bc.scope_note,
        tuple(
            sorted(
                ((s.artifact_description, s.evidence_type, s.kb_reference) for s in specs),
                key=_evidence_sort_key,
            )
        ),
    )


def _seed_product(
    session: Session,
    fw: Framework,
    ctrl_lookup: dict[str, Control],
    data: dict,
    *,
    reset_published: bool = False,
) -> dict:
    """Upsert one product's metadata, and version its control mapping.

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
    compliance claims. Only applied when a new version is actually
    created -- a no-op reimport (content identical to the current
    version) has no "changed compliance mapping" to force a re-review of,
    so it leaves is_published exactly as it was.
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
    session.flush()

    # --- resolve the current version (None for a brand new product) ---
    current_version: ProductBaselineVersion | None = None
    if product.current_version_id is not None:
        current_version = session.get(ProductBaselineVersion, product.current_version_id)

    existing_bcs_by_control: dict[uuid.UUID, BaselineControl] = {}
    existing_specs_by_bc: dict[uuid.UUID, list[BaselineEvidenceSpec]] = {}
    if current_version is not None:
        existing_bcs_by_control = {
            bc.control_id: bc
            for bc in session.scalars(
                select(BaselineControl).where(
                    BaselineControl.baseline_version_id == current_version.id
                )
            )
        }
        if existing_bcs_by_control:
            for s in session.scalars(
                select(BaselineEvidenceSpec).where(
                    BaselineEvidenceSpec.baseline_control_id.in_(
                        [bc.id for bc in existing_bcs_by_control.values()]
                    )
                )
            ):
                existing_specs_by_bc.setdefault(s.baseline_control_id, []).append(s)

    # --- parse every incoming control entry into a comparable row ---
    incoming_by_control: dict[uuid.UUID, dict[str, Any]] = {}
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

            incoming_by_control[ctrl.id] = {
                "objectives": entry.get("objectives") or [],
                "classification": entry["classification"],
                "coverage_basis": entry.get("coverage_basis", "customer_system"),
                "candidate_state": entry.get("candidate_state", "not_satisfied_by_product"),
                "provider_contribution": entry.get("provider_contribution"),
                "customer_action": entry.get("customer_action"),
                "note": entry.get("note"),
                "scope_note": entry.get("scope_note"),
                "batch_group_id": batch_id,
                "evidence": [
                    (ev["artifact"], ev["type"], ev.get("kb"))
                    for ev in (entry.get("evidence") or [])
                ],
            }

    # --- diff against the current version's content ---
    changed = False
    removed: list[str] = []
    uuid_to_control_str = {c.id: cid for cid, c in ctrl_lookup.items()}
    all_control_uuids = set(incoming_by_control) | set(existing_bcs_by_control)
    for ctrl_uuid in all_control_uuids:
        old_bc = existing_bcs_by_control.get(ctrl_uuid)
        new_row = incoming_by_control.get(ctrl_uuid)
        if old_bc is not None and new_row is None:
            changed = True
            removed.append(uuid_to_control_str.get(ctrl_uuid, str(ctrl_uuid)))
        elif old_bc is None and new_row is not None:
            changed = True
        elif old_bc is not None and new_row is not None:
            if _existing_signature(old_bc, existing_specs_by_bc.get(old_bc.id, [])) != (
                _entry_signature(new_row)
            ):
                changed = True

    needs_new_version = current_version is None or changed

    bcs_written = 0
    specs_written = 0

    if needs_new_version:
        next_version_number = (
            1 if current_version is None else current_version.version_number + 1
        )
        new_version = ProductBaselineVersion(
            product_id=product.id, version_number=next_version_number
        )
        session.add(new_version)
        session.flush()
        product.current_version_id = new_version.id
        # A brand new product's first-ever version has no prior publish
        # decision to protect tenants from -- only unpublish when a real,
        # previously-current version is being replaced.
        if reset_published and current_version is not None:
            product.is_published = False

        for ctrl_uuid, row in incoming_by_control.items():
            bc = BaselineControl(
                product_id=product.id,
                baseline_version_id=new_version.id,
                control_id=ctrl_uuid,
                objectives=row["objectives"],
                classification=row["classification"],
                coverage_basis=row["coverage_basis"],
                candidate_state=row["candidate_state"],
                provider_contribution=row["provider_contribution"],
                customer_action=row["customer_action"],
                note=row["note"],
                scope_note=row["scope_note"],
                batch_group_id=row["batch_group_id"],
            )
            session.add(bc)
            session.flush()
            bcs_written += 1
            for artifact, ev_type, kb in row["evidence"]:
                session.add(
                    BaselineEvidenceSpec(
                        baseline_control_id=bc.id,
                        artifact_description=artifact,
                        evidence_type=ev_type,
                        kb_reference=kb,
                    )
                )
                specs_written += 1
        version_number = next_version_number
    else:
        version_number = current_version.version_number if current_version else None

    session.flush()
    return {
        "baseline_controls": bcs_written,
        "evidence_specs": specs_written,
        "missing": missing,
        "removed": removed,
        "product_key": product_key,
        "version_number": version_number,
        "version_created": needs_new_version,
    }
