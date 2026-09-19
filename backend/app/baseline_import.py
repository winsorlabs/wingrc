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

from .models import (
    AssessmentObjective,
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    Framework,
    Product,
    ProductBaselineVersion,
)
from .seeds.baselines import _seed_product, normalize_product_key

_VALID_CLASSIFICATIONS = frozenset({"provider_satisfies", "shared", "customer_owns"})
_VALID_COVERAGE_BASES = frozenset({"customer_system", "platform_only", "assists"})
_VALID_CANDIDATE_STATES = frozenset({"pending_evidence", "not_satisfied_by_product"})
_VALID_EVIDENCE_TYPES = frozenset({"screenshot", "export", "document", "link", "policy"})

# A live compliance claim this re-import would disturb the justification
# for. decommissioned OrgProduct rows are historical (CLAUDE.md's own
# deactivation-lifecycle rule keeps their control_state for audit, but
# nothing about them is "current"), so they don't count toward the warning.
_LIVE_ORG_PRODUCT_STATUSES = frozenset({"active", "candidate"})

# Real failure mode from a real ingestion run: the AI classified two
# RocketCyber entries `shared` while its OWN note read as a full disclaim
# ("Kaseya explicitly states it does not implement/enforce..."). `shared`
# partially credits the vendor and queues evidence tasks; the note it came
# with said the opposite. This is a deterministic, post-AI, code-enforced
# check for exactly that contradiction -- the same reasoning as the
# evidence-minimization rules (baseline.py's own docstring): a confused
# model must not be trusted to police itself, so the check lives in code a
# model can't talk its way around. It only ever FLAGS for a human to look
# at -- never reclassifies anything itself (CLAUDE.md's "candidates, never
# auto-met" rule applies just as much to a classification as to a control
# status). Bias this list toward catching things: a false positive costs a
# reviewer one glance at a row they'd have looked at anyway; a false
# negative lets an unearned vendor credit sit in a real SSP unnoticed.
# Expected to grow as real ingestions turn up new disclaim phrasing --
# add to it freely, here, in one place a human can find.
_DISCLAIM_PHRASES = (
    "does not implement",
    "does not enforce",
    "does not provide",
    "does not satisfy",
    "does not manage",
    "does not configure",
    "does not control",
    "does not own",
    "does not offer",
    "not implemented by",
    "not enforced by",
    "not provided by",
    "not configured by",
    "not supported by",
    "not managed by",
    "not covered by",
    "not the responsibility of",
    "customer is responsible",
    "customer's responsibility",
    "responsibility of the customer",
    "customer must",
    "customer's own",
    "customer-managed",
    "outside the scope of",
    "does not cover",
    "no visibility into",
    "cannot enforce",
    "cannot configure",
    "does not have access to",
)


def _evidence_sort_key(t: tuple[str, str, str | None]) -> tuple[str, str, str]:
    """Sort key for an (artifact, type, kb) evidence tuple. kb can be None;
    coercing it to "" avoids "'<' not supported between NoneType and str"
    the moment two specs share artifact+type and the comparison reaches
    the kb element."""
    return (t[0], t[1], t[2] or "")


def _disclaims_coverage(*texts: str | None) -> bool:
    """True if any of the given supporting-text fields reads as a vendor
    disclaiming what its own classification credits it for. Case-
    insensitive substring match against `_DISCLAIM_PHRASES` -- deliberately
    simple (no NLP/AI in this check at all) so it's auditable and can't be
    talked around the way the thing it's checking already was.
    """
    combined = " ".join(t for t in texts if t).lower()
    return any(phrase in combined for phrase in _DISCLAIM_PHRASES)


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


@dataclass
class ValidationProblem:
    """One problem, tagged with the row/field it belongs to when it has
    one -- lets a UI point at the exact control/field needing a decision
    instead of a reviewer counting array indices in a flat message list.
    `message` is always the same text `validate()` has always produced
    (that function is now a thin wrapper over this one), so nothing that
    already parses/asserts on those strings needs to change.
    """

    message: str
    row_index: int | None = None
    field: str | None = None


def validate_structured(
    session: Session, data: dict, ctrl_lookup: dict[str, Control]
) -> tuple[list[ValidationProblem], list[tuple[int, list[str], dict]]]:
    """Same checks as validate() (which now just unwraps this), plus a
    row_index/field tag per problem and the parsed_entries list itself --
    both consumed by build_preview() to render a row for every
    structurally-parseable control entry regardless of what else is wrong
    with it, not just once the whole file is clean.
    """
    problems: list[ValidationProblem] = []

    pd = data.get("product")
    if not isinstance(pd, dict):
        return [ValidationProblem("Missing top-level 'product' mapping.")], []
    for required in ("key", "name", "provider", "category"):
        if not pd.get(required):
            problems.append(
                ValidationProblem(f"product.{required} is required.", field=f"product.{required}")
            )
    if pd.get("key"):
        try:
            normalize_product_key(pd["key"])
        except ValueError as exc:
            problems.append(ValidationProblem(str(exc), field="product.key"))

    controls = data.get("controls")
    if controls is None:
        controls = []
    elif not isinstance(controls, list):
        problems.append(ValidationProblem("'controls' must be a list."))
        controls = []

    # Resolve every referenced control up front so the objective-key check
    # below has a real Control row (and its actual objective keys) to
    # validate against -- the same lookup seeds/baselines.py itself builds,
    # not a second parsing pass.
    referenced_control_ids: set[uuid.UUID] = set()
    parsed_entries: list[tuple[int, list[str], dict]] = []
    for idx, entry in enumerate(controls):
        if not isinstance(entry, dict):
            problems.append(ValidationProblem(f"controls[{idx}] is not a mapping.", row_index=idx))
            continue
        ctrl_ids = entry.get("control")
        if isinstance(ctrl_ids, str):
            ctrl_ids = [ctrl_ids]
        if not isinstance(ctrl_ids, list) or not ctrl_ids:
            problems.append(
                ValidationProblem(
                    f"controls[{idx}].control must be a control id or a list of them.",
                    row_index=idx, field="control",
                )
            )
            continue
        parsed_entries.append((idx, ctrl_ids, entry))
        for cid in ctrl_ids:
            ctrl = ctrl_lookup.get(cid)
            if ctrl is None:
                problems.append(
                    ValidationProblem(
                        f"controls[{idx}]: unknown control id {cid!r}.",
                        row_index=idx, field="control",
                    )
                )
            else:
                referenced_control_ids.add(ctrl.id)

    objective_keys = _objective_keys_by_control(session, referenced_control_ids)

    for idx, ctrl_ids, entry in parsed_entries:
        classification = entry.get("classification")
        if classification not in _VALID_CLASSIFICATIONS:
            problems.append(
                ValidationProblem(
                    f"controls[{idx}].classification {classification!r} must be one of "
                    f"{sorted(_VALID_CLASSIFICATIONS)}.",
                    row_index=idx, field="classification",
                )
            )
        # coverage_basis is moot for customer_owns (evidence-minimization
        # already zeroes provider_contribution/evidence for those, so WHERE
        # the vendor's coverage would apply is irrelevant) -- defaulted as
        # before. For provider_satisfies/shared it answers whether this
        # credits the customer's CUI systems or just the vendor's own
        # portal, which CLAUDE.md's hard rules treat very differently, so
        # it must be explicitly reviewed and set, never defaulted. This is
        # the gap left open on purpose by the document-ingestion pipeline
        # (baseline.py:ControlEntry.coverage_basis) -- nothing in a vendor
        # doc reliably distinguishes the two without a human who knows the
        # actual deployment.
        if classification == "customer_owns":
            coverage_basis = entry.get("coverage_basis", "customer_system")
            if coverage_basis not in _VALID_COVERAGE_BASES:
                problems.append(
                    ValidationProblem(
                        f"controls[{idx}].coverage_basis {coverage_basis!r} must be one of "
                        f"{sorted(_VALID_COVERAGE_BASES)}.",
                        row_index=idx, field="coverage_basis",
                    )
                )
        else:
            coverage_basis = entry.get("coverage_basis")
            if coverage_basis not in _VALID_COVERAGE_BASES:
                problems.append(
                    ValidationProblem(
                        f"controls[{idx}].coverage_basis must be explicitly set to one of "
                        f"{sorted(_VALID_COVERAGE_BASES)} for classification={classification!r} "
                        "-- reviewer must confirm whether this credits the customer's CUI "
                        "systems or only the vendor's own platform.",
                        row_index=idx, field="coverage_basis",
                    )
                )
        candidate_state = entry.get("candidate_state", "not_satisfied_by_product")
        if candidate_state not in _VALID_CANDIDATE_STATES:
            problems.append(
                ValidationProblem(
                    f"controls[{idx}].candidate_state {candidate_state!r} must be one of "
                    f"{sorted(_VALID_CANDIDATE_STATES)}.",
                    row_index=idx, field="candidate_state",
                )
            )

        objectives = entry.get("objectives") or []
        if not isinstance(objectives, list) or not all(isinstance(o, str) for o in objectives):
            problems.append(
                ValidationProblem(
                    f"controls[{idx}].objectives must be a list of strings.",
                    row_index=idx, field="objectives",
                )
            )
            objectives = []
        for cid in ctrl_ids:
            ctrl = ctrl_lookup.get(cid)
            if ctrl is None:
                continue  # already reported above
            valid_keys = objective_keys.get(ctrl.id, set())
            for okey in objectives:
                if okey not in valid_keys:
                    problems.append(
                        ValidationProblem(
                            f"controls[{idx}]: objective key {okey!r} is not a real "
                            f"assessment objective on {cid} (known: {sorted(valid_keys)}).",
                            row_index=idx, field="objectives",
                        )
                    )

        evidence = entry.get("evidence") or []
        if not isinstance(evidence, list):
            problems.append(
                ValidationProblem(
                    f"controls[{idx}].evidence must be a list.", row_index=idx, field="evidence"
                )
            )
            evidence = []
        if classification == "customer_owns" and evidence:
            # The minimization invariant (models.py:BaselineEvidenceSpec):
            # customer_owns rows must never generate evidence tasks -- the
            # vendor doesn't satisfy the control, so there is nothing of
            # theirs to collect.
            problems.append(
                ValidationProblem(
                    f"controls[{idx}]: classification=customer_owns must not carry evidence "
                    f"specs ({len(evidence)} found) -- the minimization invariant: a vendor "
                    "that doesn't satisfy a control generates no evidence task for it.",
                    row_index=idx, field="evidence",
                )
            )
        for ev_idx, ev in enumerate(evidence):
            if not isinstance(ev, dict):
                problems.append(
                    ValidationProblem(
                        f"controls[{idx}].evidence[{ev_idx}] is not a mapping.",
                        row_index=idx, field="evidence",
                    )
                )
                continue
            if not ev.get("artifact"):
                problems.append(
                    ValidationProblem(
                        f"controls[{idx}].evidence[{ev_idx}].artifact is required.",
                        row_index=idx, field="evidence",
                    )
                )
            ev_type = ev.get("type")
            if ev_type not in _VALID_EVIDENCE_TYPES:
                problems.append(
                    ValidationProblem(
                        f"controls[{idx}].evidence[{ev_idx}].type {ev_type!r} must be one of "
                        f"{sorted(_VALID_EVIDENCE_TYPES)}.",
                        row_index=idx, field="evidence",
                    )
                )

    return problems, parsed_entries


def validate(session: Session, data: dict, ctrl_lookup: dict[str, Control]) -> list[str]:
    """Every problem with `data`, collected rather than raised at the
    first one -- a validator an admin can actually act on in one pass,
    per the task's "report every problem at once" requirement.

    Thin wrapper over validate_structured() -- same messages, same order,
    same everything, just without the row/field tags a UI needs and a
    plain apply-time 422 doesn't.
    """
    problems, _ = validate_structured(session, data, ctrl_lookup)
    return [p.message for p in problems]


@dataclass
class BaselineControlChange:
    control_id: str
    change_type: str  # "new" | "changed" | "unchanged" | "removed"
    # classification/coverage_basis can be None here now that rows are built
    # for every structurally-parseable entry, not just once the whole file
    # is clean -- an unset/invalid value IS the thing a reviewer is being
    # shown this row to fix. For change_type="removed" these carry the
    # control's LAST value (from the version being replaced) since there is
    # no incoming row to read them from -- roadmap item P's §1 bug fix:
    # a control dropped from the YAML used to vanish from this diff
    # entirely and silently keep being honored by the magic loop forever.
    classification: str | None
    coverage_basis: str | None
    field_diffs: dict[str, tuple[Any, Any]] = field(default_factory=dict)


@dataclass
class EvidenceSpecDraft:
    artifact: str
    type: str
    kb: str | None = None


@dataclass
class ControlEntryDraft:
    """The full editable shape of one control entry, built for every row
    in parsed_entries regardless of what validate_structured() flagged on
    it -- a reviewer needs to see and fix the actual (possibly invalid)
    values, not have the row disappear until everything else is already
    right. Mirrors baseline.py:ControlEntry's field set; kept separate
    (rather than reusing ControlEntry/_parse_control_entry directly)
    because that parser enum-casts and dict-indexes in ways that raise on
    exactly the malformed values this is meant to surface, not hide.
    """

    row_index: int
    control: list[str]
    classification: str | None
    coverage_basis: str | None
    candidate_state: str | None
    objectives: list[str] = field(default_factory=list)
    provider_contribution: str | None = None
    customer_action: str | None = None
    evidence: list[EvidenceSpecDraft] = field(default_factory=list)
    note: str | None = None
    scope_note: str | None = None
    # None = from the uploaded CRM/baseline document(s); a URL = proposed by
    # importers/research.py's web-research pass over that fetched page. See
    # baseline.py:ControlEntry.source's own docstring.
    source: str | None = None


@dataclass
class DisclaimFlag:
    """Advisory only -- deliberately NOT a ValidationProblem. This never
    blocks Apply and never changes anything about the row; it exists
    purely so a reviewer notices the contradiction between a classification
    and its own supporting text without having to read both closely on
    every one of 17+ rows. See _disclaims_coverage()'s own docstring for
    why the check itself is a plain phrase match, not a confidence score
    or anything a reviewer could treat as permission to skip reading it.
    """

    row_index: int
    message: str


@dataclass
class BaselineImportPreview:
    problems: list[str]
    product_key: str
    product_is_new: bool
    product_name: str
    control_changes: list[BaselineControlChange]
    affected_org_count: int
    affected_org_names: list[str]
    row_problems: list[ValidationProblem] = field(default_factory=list)
    control_rows: list[ControlEntryDraft] = field(default_factory=list)
    disclaim_flags: list[DisclaimFlag] = field(default_factory=list)
    # Baseline versioning (roadmap item P): the version number Apply will
    # create if anything actually differs, and whether it would differ at
    # all -- current_version_number is None for a brand new product (there
    # is no "current" yet). has_changes False means Apply is a true no-op:
    # the existing version is reused, nothing is written, is_published is
    # left exactly as it is. See seeds/baselines.py:_seed_product for the
    # same diff this mirrors.
    current_version_number: int | None = None
    next_version_number: int = 1
    has_changes: bool = True


def _coerce_evidence_drafts(raw: Any) -> list[EvidenceSpecDraft]:
    if not isinstance(raw, list):
        return []
    out: list[EvidenceSpecDraft] = []
    for ev in raw:
        if not isinstance(ev, dict):
            continue
        out.append(
            EvidenceSpecDraft(
                artifact=ev.get("artifact") or "",
                type=ev.get("type") or "",
                kb=ev.get("kb"),
            )
        )
    return out


def build_preview(
    session: Session, data: dict, ctrl_lookup: dict[str, Control]
) -> BaselineImportPreview:
    # parsed_entries (validate_structured's own first pass) isn't used here
    # -- see the row-building loop below for why it iterates the raw
    # controls list directly instead.
    problems_structured, _parsed_entries = validate_structured(session, data, ctrl_lookup)
    problems = [p.message for p in problems_structured]
    pd = data.get("product") if isinstance(data.get("product"), dict) else {}
    raw_key = pd.get("key", "")
    try:
        # Normalized so an existing product ("dattormm") is recognized as
        # the same product on a re-import spelled "DattoRMM" -- an exact-
        # match lookup on the raw, un-normalized key would report this as
        # a brand new product instead of an update. Falls back to the raw
        # key when it doesn't normalize (validate() above already reports
        # that as a problem, so this preview's product_key/product_is_new
        # fields are cosmetic once there's a problem to fix regardless).
        product_key = normalize_product_key(raw_key) if raw_key else ""
    except ValueError:
        product_key = raw_key
    product = (
        session.scalars(select(Product).where(Product.key == product_key)).first()
        if product_key
        else None
    )

    # Scoped to the product's CURRENT version only -- product_id alone
    # would also pull in every historical version's rows once a product
    # has been reimported more than once, which would make every one of
    # their controls look "unchanged" against whichever historical row
    # happened to share a control_id (roadmap item P: only the current
    # version is "what an admin might disturb by reimporting").
    current_version: ProductBaselineVersion | None = None
    existing_bcs: dict[uuid.UUID, BaselineControl] = {}
    existing_specs_by_bc: dict[uuid.UUID, list[BaselineEvidenceSpec]] = {}
    if product is not None and product.current_version_id is not None:
        current_version = session.get(ProductBaselineVersion, product.current_version_id)
        existing_bcs = {
            bc.control_id: bc
            for bc in session.scalars(
                select(BaselineControl).where(
                    BaselineControl.baseline_version_id == current_version.id
                )
            )
        }
        if existing_bcs:
            for s in session.scalars(
                select(BaselineEvidenceSpec).where(
                    BaselineEvidenceSpec.baseline_control_id.in_(
                        [bc.id for bc in existing_bcs.values()]
                    )
                )
            ):
                existing_specs_by_bc.setdefault(s.baseline_control_id, []).append(s)

    # Row-building iterates every dict-shaped entry in data["controls"]
    # directly -- NOT parsed_entries (validate_structured()'s first pass),
    # which excludes any entry whose `control` field isn't *already* a
    # valid non-empty id/list. That exclusion is right for validate_
    # structured's own purposes (objective-key/evidence checks need a real
    # control to check against), but wrong here: a freshly-added blank row
    # (the "+ Add control" affordance, before a reviewer has typed a real
    # id into it) would otherwise silently vanish from the very next
    # Re-check response instead of staying visible with its own "control id
    # required" flag like any other incomplete row. `ctrl_ids` is derived
    # leniently (empty list if missing/malformed) purely for display and
    # diff-lookup purposes; validate_structured's own problem reporting for
    # a bad `control` field is unaffected by this.
    changes: list[BaselineControlChange] = []
    control_rows: list[ControlEntryDraft] = []
    disclaim_flags: list[DisclaimFlag] = []
    seen_control_uuids: set[uuid.UUID] = set()
    for idx, entry in enumerate(data.get("controls") or []):
        if not isinstance(entry, dict):
            continue
        raw_ctrl_ids = entry.get("control")
        if isinstance(raw_ctrl_ids, str):
            ctrl_ids: list[str] = [raw_ctrl_ids]
        elif isinstance(raw_ctrl_ids, list):
            ctrl_ids = [c for c in raw_ctrl_ids if isinstance(c, str)]
        else:
            ctrl_ids = []

        classification = entry.get("classification")
        if not isinstance(classification, str):
            classification = None
        coverage_basis = entry.get(
            "coverage_basis",
            "customer_system" if classification == "customer_owns" else None,
        )
        if not isinstance(coverage_basis, str):
            coverage_basis = None
        candidate_state = entry.get("candidate_state", "not_satisfied_by_product")
        if not isinstance(candidate_state, str):
            candidate_state = None
        objectives = entry.get("objectives") or []
        if not isinstance(objectives, list):
            objectives = []
        elif not all(isinstance(o, str) for o in objectives):
            objectives = [o for o in objectives if isinstance(o, str)]

        provider_contribution = entry.get("provider_contribution")
        customer_action = entry.get("customer_action")
        note = entry.get("note")
        scope_note = entry.get("scope_note")
        source = entry.get("source")
        if not isinstance(source, str):
            source = None

        control_rows.append(
            ControlEntryDraft(
                row_index=idx,
                control=list(ctrl_ids),
                classification=classification,
                coverage_basis=coverage_basis,
                candidate_state=candidate_state,
                objectives=objectives,
                provider_contribution=provider_contribution,
                customer_action=customer_action,
                evidence=_coerce_evidence_drafts(entry.get("evidence")),
                note=note,
                scope_note=scope_note,
                source=source,
            )
        )

        if classification in ("shared", "provider_satisfies") and _disclaims_coverage(
            note, provider_contribution, customer_action, scope_note
        ):
            disclaim_flags.append(
                DisclaimFlag(
                    row_index=idx,
                    message=(
                        "This entry's own supporting text reads as a disclaim "
                        f"(vendor doesn't cover this), but it's classified "
                        f"{classification!r} -- confirm this should credit the "
                        "vendor at all, not customer_owns instead."
                    ),
                )
            )

        evidence_tuples = [
            (ev.get("artifact"), ev.get("type"), ev.get("kb"))
            for ev in (entry.get("evidence") or [])
            if isinstance(ev, dict)
        ]

        for cid in ctrl_ids:
            ctrl = ctrl_lookup.get(cid)
            if ctrl is None:
                continue
            seen_control_uuids.add(ctrl.id)
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
            if (existing.provider_contribution or None) != (provider_contribution or None):
                diffs["provider_contribution"] = (
                    existing.provider_contribution, provider_contribution
                )
            if (existing.customer_action or None) != (customer_action or None):
                diffs["customer_action"] = (existing.customer_action, customer_action)
            if (existing.note or None) != (note or None):
                diffs["note"] = (existing.note, note)
            if (existing.scope_note or None) != (scope_note or None):
                diffs["scope_note"] = (existing.scope_note, scope_note)
            existing_evidence = sorted(
                (
                    (s.artifact_description, s.evidence_type, s.kb_reference)
                    for s in existing_specs_by_bc.get(existing.id, [])
                ),
                key=_evidence_sort_key,
            )
            new_evidence = sorted(evidence_tuples, key=_evidence_sort_key)
            if existing_evidence != new_evidence:
                diffs["evidence"] = (existing_evidence, new_evidence)
            changes.append(
                BaselineControlChange(
                    cid,
                    "changed" if diffs else "unchanged",
                    classification,
                    coverage_basis,
                    diffs,
                )
            )

    # A control the current version claims but this import no longer
    # lists -- roadmap item P's §1 bug: previously silent, now a visible
    # "removed" row so a reviewer sees exactly what Apply is about to stop
    # honoring going forward (existing tenants pinned to the current
    # version are unaffected either way -- see ProductBaselineVersion's
    # own docstring).
    uuid_to_control_str = {c.id: cid for cid, c in ctrl_lookup.items()}
    for ctrl_uuid, existing in existing_bcs.items():
        if ctrl_uuid in seen_control_uuids:
            continue
        changes.append(
            BaselineControlChange(
                uuid_to_control_str.get(ctrl_uuid, str(ctrl_uuid)),
                "removed",
                existing.classification,
                existing.coverage_basis,
            )
        )

    affected_org_names: list[str] = []
    if product is not None:
        rows = session.execute(
            text("SELECT org_name, status FROM auth.product_deployment_footprint(:pid)"),
            {"pid": product.id},
        ).all()
        affected_org_names = [r.org_name for r in rows if r.status in _LIVE_ORG_PRODUCT_STATUSES]

    current_version_number = current_version.version_number if current_version else None
    # Mirrors seeds/baselines.py:_seed_product's own needs_new_version test
    # exactly: a brand new product (no current version yet) always creates
    # version 1 regardless of how many control rows it lists.
    has_changes = current_version is None or any(
        c.change_type != "unchanged" for c in changes
    )
    next_version_number = (current_version_number or 0) + 1 if has_changes else (
        current_version_number or 1
    )

    return BaselineImportPreview(
        problems=problems,
        product_key=product_key,
        product_is_new=product is None,
        product_name=pd.get("name", ""),
        control_changes=changes,
        affected_org_count=len(affected_org_names),
        affected_org_names=affected_org_names,
        row_problems=problems_structured,
        control_rows=control_rows,
        disclaim_flags=disclaim_flags,
        current_version_number=current_version_number,
        next_version_number=next_version_number,
        has_changes=has_changes,
    )


def apply_import(
    session: Session, data: dict, ctrl_lookup: dict[str, Control], fw: Framework
) -> dict:
    """Write the validated import. Caller (the router) re-validates
    immediately before calling this -- see module docstring for why that
    isn't a formality here.
    """
    return _seed_product(session, fw, ctrl_lookup, data, reset_published=True)
