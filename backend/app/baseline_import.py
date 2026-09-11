"""Validate, preview, and apply an admin-uploaded baseline library YAML
(G.9's Tools import screen) -- the same schema seeds/baselines.py expects
for the git-tracked files under backend/baselines/, but validated up front
and reviewed via a dry-run diff before anything is written, since this is
a runtime upload path rather than a deploy-time, git-reviewed one.

Reuses seeds/baselines.py's _seed_product for the actual write (apply) --
one upsert path, not a second one invented for this screen. See that
function's reset_published parameter for the one behavioral difference
this screen needs: an admin-uploaded re-import always forces
Product.is_published back to False, since publishing is a deliberate act
that must not silently carry forward onto a changed compliance mapping
(ROADMAP/G.9).

Dry-run and apply both call validate() independently -- there is no
"confirmed changes echoed back" token linking the two calls the way
routers/scope.py's workbook import has (a baseline file is one atomic
unit, not a list of independently-selectable rows, so there is nothing
meaningful to select a subset of the way a scope-entity diff has).
Re-validating on apply is cheap (pure Python plus a couple of indexed
reads) and is the actual safety net against "dry-run was skipped and
apply was called directly" -- not merely a formality.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import yaml
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import AssessmentObjective, BaselineControl, Control, Framework, Product
from .seeds.baselines import _seed_product

_VALID_CLASSIFICATIONS = frozenset({"provider_satisfies", "shared", "customer_owns"})
_VALID_COVERAGE_BASES = frozenset({"customer_system", "platform_only", "assists"})
_VALID_CANDIDATE_STATES = frozenset({"pending_evidence", "not_satisfied_by_product"})
_VALID_EVIDENCE_TYPES = frozenset({"screenshot", "export", "document", "link", "policy"})

# A live compliance claim this re-import would disturb the justification
# for. decommissioned OrgProduct rows are historical (CLAUDE.md's own
# deactivation-lifecycle rule keeps their control_state for audit, but
# nothing about them is "current"), so they don't count toward the warning.
_LIVE_ORG_PRODUCT_STATUSES = frozenset({"active", "candidate"})


def parse_yaml(raw: bytes) -> tuple[dict | None, str | None]:
    """Parse raw YAML bytes. Returns (data, None) or (None, error) --
    never raises: a malformed upload is an ordinary validation failure to
    report, not a 500.
    """
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return None, f"Could not parse YAML: {e}"
    if not isinstance(data, dict):
        return None, "Top-level YAML content must be a mapping."
    return data, None


def _objective_keys_by_control(
    session: Session, control_ids: set[uuid.UUID]
) -> dict[uuid.UUID, set[str]]:
    if not control_ids:
        return {}
    rows = session.execute(
        select(AssessmentObjective.control_id, AssessmentObjective.objective_key).where(
            AssessmentObjective.control_id.in_(control_ids)
        )
    ).all()
    out: dict[uuid.UUID, set[str]] = {}
    for control_id, objective_key in rows:
        out.setdefault(control_id, set()).add(objective_key)
    return out


def validate(session: Session, data: dict, ctrl_lookup: dict[str, Control]) -> list[str]:
    """Every problem with `data`, collected rather than raised at the
    first one -- a validator an admin can actually act on in one pass,
    per the task's "report every problem at once" requirement.
    """
    problems: list[str] = []

    pd = data.get("product")
    if not isinstance(pd, dict):
        return ["Missing top-level 'product' mapping."]
    for required in ("key", "name", "provider", "category"):
        if not pd.get(required):
            problems.append(f"product.{required} is required.")

    controls = data.get("controls")
    if controls is None:
        controls = []
    elif not isinstance(controls, list):
        problems.append("'controls' must be a list.")
        controls = []

    # Resolve every referenced control up front so the objective-key check
    # below has a real Control row (and its actual objective keys) to
    # validate against -- the same lookup seeds/baselines.py itself builds,
    # not a second parsing pass.
    referenced_control_ids: set[uuid.UUID] = set()
    parsed_entries: list[tuple[int, list[str], dict]] = []
    for idx, entry in enumerate(controls):
        if not isinstance(entry, dict):
            problems.append(f"controls[{idx}] is not a mapping.")
            continue
        ctrl_ids = entry.get("control")
        if isinstance(ctrl_ids, str):
            ctrl_ids = [ctrl_ids]
        if not isinstance(ctrl_ids, list) or not ctrl_ids:
            problems.append(f"controls[{idx}].control must be a control id or a list of them.")
            continue
        parsed_entries.append((idx, ctrl_ids, entry))
        for cid in ctrl_ids:
            ctrl = ctrl_lookup.get(cid)
            if ctrl is None:
                problems.append(f"controls[{idx}]: unknown control id {cid!r}.")
            else:
                referenced_control_ids.add(ctrl.id)

    objective_keys = _objective_keys_by_control(session, referenced_control_ids)

    for idx, ctrl_ids, entry in parsed_entries:
        classification = entry.get("classification")
        if classification not in _VALID_CLASSIFICATIONS:
            problems.append(
                f"controls[{idx}].classification {classification!r} must be one of "
                f"{sorted(_VALID_CLASSIFICATIONS)}."
            )
        coverage_basis = entry.get("coverage_basis", "customer_system")
        if coverage_basis not in _VALID_COVERAGE_BASES:
            problems.append(
                f"controls[{idx}].coverage_basis {coverage_basis!r} must be one of "
                f"{sorted(_VALID_COVERAGE_BASES)}."
            )
        candidate_state = entry.get("candidate_state", "not_satisfied_by_product")
        if candidate_state not in _VALID_CANDIDATE_STATES:
            problems.append(
                f"controls[{idx}].candidate_state {candidate_state!r} must be one of "
                f"{sorted(_VALID_CANDIDATE_STATES)}."
            )

        objectives = entry.get("objectives") or []
        if not isinstance(objectives, list) or not all(isinstance(o, str) for o in objectives):
            problems.append(f"controls[{idx}].objectives must be a list of strings.")
            objectives = []
        for cid in ctrl_ids:
            ctrl = ctrl_lookup.get(cid)
            if ctrl is None:
                continue  # already reported above
            valid_keys = objective_keys.get(ctrl.id, set())
            for okey in objectives:
                if okey not in valid_keys:
                    problems.append(
                        f"controls[{idx}]: objective key {okey!r} is not a real "
                        f"assessment objective on {cid} (known: {sorted(valid_keys)})."
                    )

        evidence = entry.get("evidence") or []
        if not isinstance(evidence, list):
            problems.append(f"controls[{idx}].evidence must be a list.")
            evidence = []
        if classification == "customer_owns" and evidence:
            # The minimization invariant (models.py:BaselineEvidenceSpec):
            # customer_owns rows must never generate evidence tasks -- the
            # vendor doesn't satisfy the control, so there is nothing of
            # theirs to collect.
            problems.append(
                f"controls[{idx}]: classification=customer_owns must not carry evidence "
                f"specs ({len(evidence)} found) -- the minimization invariant: a vendor "
                "that doesn't satisfy a control generates no evidence task for it."
            )
        for ev_idx, ev in enumerate(evidence):
            if not isinstance(ev, dict):
                problems.append(f"controls[{idx}].evidence[{ev_idx}] is not a mapping.")
                continue
            if not ev.get("artifact"):
                problems.append(f"controls[{idx}].evidence[{ev_idx}].artifact is required.")
            ev_type = ev.get("type")
            if ev_type not in _VALID_EVIDENCE_TYPES:
                problems.append(
                    f"controls[{idx}].evidence[{ev_idx}].type {ev_type!r} must be one of "
                    f"{sorted(_VALID_EVIDENCE_TYPES)}."
                )

    return problems


@dataclass
class BaselineControlChange:
    control_id: str
    change_type: str  # "new" | "changed" | "unchanged"
    classification: str
    coverage_basis: str
    field_diffs: dict[str, tuple[Any, Any]] = field(default_factory=dict)


@dataclass
class BaselineImportPreview:
    problems: list[str]
    product_key: str
    product_is_new: bool
    product_name: str
    control_changes: list[BaselineControlChange]
    affected_org_count: int
    affected_org_names: list[str]


def build_preview(
    session: Session, data: dict, ctrl_lookup: dict[str, Control]
) -> BaselineImportPreview:
    problems = validate(session, data, ctrl_lookup)
    pd = data.get("product") if isinstance(data.get("product"), dict) else {}
    product_key = pd.get("key", "")
    product = (
        session.scalars(select(Product).where(Product.key == product_key)).first()
        if product_key
        else None
    )

    existing_bcs: dict[uuid.UUID, BaselineControl] = {}
    if product is not None:
        existing_bcs = {
            bc.control_id: bc
            for bc in session.scalars(
                select(BaselineControl).where(BaselineControl.product_id == product.id)
            )
        }

    changes: list[BaselineControlChange] = []
    if not problems:
        for entry in data.get("controls", []):
            ctrl_ids = entry["control"]
            if isinstance(ctrl_ids, str):
                ctrl_ids = [ctrl_ids]
            classification = entry["classification"]
            coverage_basis = entry.get("coverage_basis", "customer_system")
            candidate_state = entry.get("candidate_state", "not_satisfied_by_product")
            objectives = entry.get("objectives") or []
            for cid in ctrl_ids:
                ctrl = ctrl_lookup.get(cid)
                if ctrl is None:
                    continue
                existing = existing_bcs.get(ctrl.id)
                if existing is None:
                    changes.append(
                        BaselineControlChange(cid, "new", classification, coverage_basis)
                    )
                    continue
                diffs: dict[str, tuple[Any, Any]] = {}
                if existing.classification != classification:
                    diffs["classification"] = (existing.classification, classification)
                if existing.coverage_basis != coverage_basis:
                    diffs["coverage_basis"] = (existing.coverage_basis, coverage_basis)
                if existing.candidate_state != candidate_state:
                    diffs["candidate_state"] = (existing.candidate_state, candidate_state)
                if sorted(existing.objectives or []) != sorted(objectives):
                    diffs["objectives"] = (existing.objectives, objectives)
                changes.append(
                    BaselineControlChange(
                        cid,
                        "changed" if diffs else "unchanged",
                        classification,
                        coverage_basis,
                        diffs,
                    )
                )

    affected_org_names: list[str] = []
    if product is not None:
        rows = session.execute(
            text("SELECT org_name, status FROM auth.product_deployment_footprint(:pid)"),
            {"pid": product.id},
        ).all()
        affected_org_names = [r.org_name for r in rows if r.status in _LIVE_ORG_PRODUCT_STATUSES]

    return BaselineImportPreview(
        problems=problems,
        product_key=product_key,
        product_is_new=product is None,
        product_name=pd.get("name", ""),
        control_changes=changes,
        affected_org_count=len(affected_org_names),
        affected_org_names=affected_org_names,
    )


def apply_import(
    session: Session, data: dict, ctrl_lookup: dict[str, Control], fw: Framework
) -> dict:
    """Write the validated import. Caller (the router) re-validates
    immediately before calling this -- see module docstring for why that
    isn't a formality here.
    """
    return _seed_product(session, fw, ctrl_lookup, data, reset_published=True)
