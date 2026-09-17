"""Domain types for the WinGRC baseline library.

A BaselineEntry is the candidate record the document-ingestion pass produces
for engineer review. Nothing here is "met" until the customer's configuration
is confirmed and evidence is attached.

Evidence-minimization invariant — enforced here, not left to the AI:
  customer_owns       → no evidence specs, candidate_state = not_satisfied_by_product
  provider_satisfies  → evidence specs present, candidate_state = pending_evidence
  shared              → evidence specs present, candidate_state = pending_evidence

The YAML loader applies this invariant when reading hand-authored YAML too, so
a typo in an author-written file never silently credits a disclaimed control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class Classification(StrEnum):
    PROVIDER_SATISFIES = "provider_satisfies"
    SHARED = "shared"
    CUSTOMER_OWNS = "customer_owns"


class CandidateState(StrEnum):
    PENDING_EVIDENCE = "pending_evidence"
    NOT_SATISFIED_BY_PRODUCT = "not_satisfied_by_product"


@dataclass
class EvidenceSpec:
    artifact: str
    type: str  # screenshot | export | document | link
    kb: str | None = None


@dataclass
class ControlEntry:
    """One control (or a batch of controls) in the baseline library.

    `control` is a single ID string for normal entries, or a list of IDs
    when the source doc disclaims an entire family in one statement (e.g. all
    IA controls routed to the customer IdP).
    """

    control: str | list[str]
    classification: Classification
    candidate_state: CandidateState
    objectives: list[str] = field(default_factory=list)
    provider_contribution: str | None = None
    customer_action: str | None = None
    evidence: list[EvidenceSpec] = field(default_factory=list)
    note: str | None = None
    scope_note: str | None = None
    # WHERE the vendor's coverage applies -- customer_system | platform_only |
    # assists. None on customer_owns entries (moot -- minimization already
    # zeroes evidence/contribution for those). None on provider_satisfies/
    # shared entries means "not yet reviewed" -- baseline_import.validate()
    # requires a reviewer set this explicitly before apply; the AI pipeline
    # never fills it in, since crediting a vendor's own-platform-only control
    # to the customer's CUI systems is exactly what CLAUDE.md's hard rules
    # forbid, and there's nothing in a vendor doc that reliably distinguishes
    # the two without a human who knows the deployment.
    coverage_basis: str | None = None
    # Per-claim source attribution (2026-09-17, AI research slice). None
    # means "from the uploaded CRM/baseline document(s)" -- the original,
    # unchanged document-ingestion pipeline never sets this itself. A URL
    # string means the entry was proposed by importers/research.py's
    # web-research pass over that specific fetched page -- assigned in code
    # when the entry is built, never trusted from the model's own output,
    # so a reviewer's "where did this come from" is always exactly right.
    source: str | None = None


@dataclass
class ProductMeta:
    key: str
    name: str
    provider: str
    category: str       # ESP | CSP | SPA | …
    asset_type: str     # SPA | CUI Asset | …
    framework: str
    role: str
    assumed_config: list[str] = field(default_factory=list)
    source_docs: list[str] = field(default_factory=list)
    # Permanent AI-generation provenance -- set once by the document-ingestion
    # pipeline, never cleared by a later re-import that omits them. None for
    # hand-authored baselines.
    ai_generated_at: str | None = None
    ai_generated_model: str | None = None


@dataclass
class BaselineSummary:
    provider_satisfies: list[str] = field(default_factory=list)
    shared: list[str] = field(default_factory=list)
    customer_owns: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class BaselineEntry:
    product: ProductMeta
    controls: list[ControlEntry] = field(default_factory=list)
    summary: BaselineSummary | None = None

    def compute_summary(self) -> BaselineSummary:
        """Derive the summary roll-up from the control entries."""
        ps: list[str] = []
        sh: list[str] = []
        co: list[str] = []
        for entry in self.controls:
            ids = (
                list(entry.control)
                if isinstance(entry.control, list)
                else [entry.control]
            )
            if entry.classification == Classification.PROVIDER_SATISFIES:
                ps.extend(ids)
            elif entry.classification == Classification.SHARED:
                sh.extend(ids)
            else:
                co.extend(ids)
        return BaselineSummary(
            provider_satisfies=sorted(ps),
            shared=sorted(sh),
            customer_owns=sorted(co),
        )


# ---------------------------------------------------------------------------
# YAML I/O
# ---------------------------------------------------------------------------


def _parse_control_entry(raw: dict[str, Any]) -> ControlEntry:
    cls = Classification(raw["classification"])

    # Enforce minimization regardless of what the YAML says.
    if cls == Classification.CUSTOMER_OWNS:
        return ControlEntry(
            control=raw["control"],
            classification=cls,
            candidate_state=CandidateState.NOT_SATISFIED_BY_PRODUCT,
            objectives=[str(o) for o in raw.get("objectives", [])],
            evidence=[],
            note=raw.get("note"),
            scope_note=raw.get("scope_note"),
        )

    evidence = [
        EvidenceSpec(
            artifact=ev["artifact"],
            type=ev["type"],
            kb=ev.get("kb"),
        )
        for ev in raw.get("evidence", [])
    ]
    return ControlEntry(
        control=raw["control"],
        classification=cls,
        candidate_state=CandidateState(
            raw.get("candidate_state", "pending_evidence")
        ),
        objectives=[str(o) for o in raw.get("objectives", [])],
        provider_contribution=raw.get("provider_contribution"),
        customer_action=raw.get("customer_action"),
        evidence=evidence,
        note=raw.get("note"),
        scope_note=raw.get("scope_note"),
        coverage_basis=raw.get("coverage_basis"),
        source=raw.get("source"),
    )


def load_baseline(path: str | Path) -> BaselineEntry:
    """Parse a baselines/*.yaml file into a BaselineEntry."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    p = data["product"]
    product = ProductMeta(
        key=p["key"],
        name=p["name"],
        provider=p["provider"],
        category=p["category"],
        asset_type=p["asset_type"],
        framework=p["framework"],
        role=p["role"].strip(),
        assumed_config=list(p.get("assumed_config", [])),
        source_docs=list(p.get("source_docs", [])),
        ai_generated_at=p.get("ai_generated_at"),
        ai_generated_model=p.get("ai_generated_model"),
    )
    controls = [_parse_control_entry(c) for c in data.get("controls", [])]

    summary: BaselineSummary | None = None
    if "summary" in data:
        s = data["summary"]
        summary = BaselineSummary(
            provider_satisfies=list(s.get("provider_satisfies", [])),
            shared=list(s.get("shared", [])),
            customer_owns=list(s.get("customer_owns", [])),
            note=s.get("note"),
        )

    return BaselineEntry(product=product, controls=controls, summary=summary)


def to_yaml_dict(entry: BaselineEntry) -> dict[str, Any]:
    """Serialize a BaselineEntry to a plain dict suitable for yaml.dump()."""
    p = entry.product
    prod: dict[str, Any] = {
        "key": p.key,
        "name": p.name,
        "provider": p.provider,
        "category": p.category,
        "asset_type": p.asset_type,
        "framework": p.framework,
        "role": p.role,
    }
    if p.assumed_config:
        prod["assumed_config"] = p.assumed_config
    if p.source_docs:
        prod["source_docs"] = p.source_docs
    if p.ai_generated_at:
        prod["ai_generated_at"] = p.ai_generated_at
    if p.ai_generated_model:
        prod["ai_generated_model"] = p.ai_generated_model

    controls = []
    for c in entry.controls:
        cd: dict[str, Any] = {
            "control": c.control,
            "classification": c.classification.value,
            "candidate_state": c.candidate_state.value,
        }
        if c.objectives:
            cd["objectives"] = c.objectives
        if c.provider_contribution:
            cd["provider_contribution"] = c.provider_contribution
        if c.customer_action:
            cd["customer_action"] = c.customer_action
        if c.evidence:
            cd["evidence"] = [
                {k: v for k, v in {
                    "artifact": e.artifact,
                    "type": e.type,
                    "kb": e.kb,
                }.items() if v is not None}
                for e in c.evidence
            ]
        if c.note:
            cd["note"] = c.note
        if c.scope_note:
            cd["scope_note"] = c.scope_note
        if c.coverage_basis:
            cd["coverage_basis"] = c.coverage_basis
        if c.source:
            cd["source"] = c.source
        controls.append(cd)

    result: dict[str, Any] = {"product": prod, "controls": controls}

    if entry.summary:
        s = entry.summary
        sd: dict[str, Any] = {
            "provider_satisfies": s.provider_satisfies,
            "shared": s.shared,
            "customer_owns": s.customer_owns,
        }
        if s.note:
            sd["note"] = s.note
        result["summary"] = sd

    return result
