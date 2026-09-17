"""AI research of vendor platform documentation -- proposes ADDITIONAL
baseline coverage from fetched web pages, on top of (never instead of, and
never overriding) whatever the existing CRM/baseline document pipeline
(importers/document.py) already concluded.

THE RULE THIS MODULE EXISTS TO ENFORCE (2026-09-17, Jarrod, non-negotiable):

    Web content may propose additional coverage. It may never override a
    disclaim in the vendor's CRM.

The CRM is the vendor's formal, contractual statement of what they are and
aren't responsible for. A documentation page saying "we encrypt your data"
must not upgrade a control the CRM explicitly disclaims -- that's precisely
the direction of error already observed in a real RocketCyber ingestion run
(baseline_import.py's own disclaim-flag docstring), and letting an AI model
police this itself, ONLY via a prompt instruction, is exactly the failure
mode that already happened once. This is enforced here, in code, as a
merge step no model output can talk its way around -- see merge_research()
below. It is the same discipline as baseline_import.py's evidence-
minimization rules and disclaim-flag check: a confused model is not trusted
to self-police; the code is the actual guarantee.

Two-pass design, deliberately kept separate from importers/document.py
rather than teaching one AI call to reconcile everything itself:

  Pass 1 (UNCHANGED): importers/document.py:ingest_document() over the
      uploaded CRM/baseline document(s), exactly as it already runs today.
      This module never modifies that call or its prompt -- see this
      package's own test suite for the explicit "existing CRM-only
      ingestion behaves identically" regression guard this preserves.
  Pass 2 (NEW): ingest_web_page(), one AI call PER approved, already-
      fetched page (never several pages combined into one call) -- this is
      what makes per-claim source attribution (see baseline.py:
      ControlEntry.source) a code-level guarantee rather than something
      trusted from the model's own JSON: every entry Pass 2 produces is
      stamped with the exact URL it came from in code, not parsed from the
      model's response.
  Merge (CODE, not a prompt): merge_research() expands Pass 1's own
      output into a per-control-id lookup and checks every Pass-2 proposal
      against it. A control Pass 1 never mentions is genuinely new
      coverage and is added. A control Pass 1 already covers without
      disclaiming needs no addition (the CRM's own row already stands). A
      control Pass 1 disclaims (classified customer_owns, or its own text
      reads as a disclaim) is NEVER upgraded by a Pass-2 proposal --
      instead a DisclaimFlag is raised, reusing the exact mechanism
      baseline_import.py already built for a document's own internal
      contradictions, extended here to a cross-source one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..ai.base import AIProvider
from ..baseline import (
    BaselineEntry,
    CandidateState,
    Classification,
    ControlEntry,
    EvidenceSpec,
    to_yaml_dict,
)
from ..baseline_import import DisclaimFlag, _disclaims_coverage
from .document import (
    DocumentIngestError,
    _apply_evidence_minimization,
    _parse_ai_json,
)

# Per-page caps (2026-09-17, §5 -- cost and size). Deliberately much
# smaller than importers/document.py's 600k-character combined-document
# guard: that guard bounds ONE call over 1-2 authored/vendor documents;
# these bound N calls (one per approved URL) over web pages of wildly
# varying quality, and the real risk here is an admin approving a large
# batch of pages, not any single page being long.
MAX_PAGE_CHARS = 50_000
MAX_PAGES_PER_RUN = 10
MAX_TOTAL_WEB_CHARS = 200_000

_INGEST_MAX_TOKENS = 8192

_WEB_SYSTEM_PROMPT = """\
You are a CMMC compliance analyst reviewing ONE page of a vendor's own \
platform/admin documentation (not the vendor's CRM or a sales page) to \
identify CMMC / NIST 800-171 controls this product's documented \
capabilities MIGHT satisfy.

You are NOT authoritative. Your output is a set of CANDIDATE proposals a \
human will reconcile against the vendor's formal CRM before anything is \
credited. Do not attempt to resolve conflicts with any other source \
yourself -- you are only shown this one page.

For each control this page's content plausibly supports, assign:

  provider_satisfies  – the documented capability, correctly configured, \
appears to materially satisfy this control.
  shared              – the documented capability enables this control but \
the customer must configure or operate part of it.

Do not propose customer_owns entries -- this pass exists to find ADDITIONAL \
candidate coverage, not to disclaim anything (disclaims come from the CRM,\
 reconciled separately).

Evidence-minimization rules -- apply these in your output:
  • Where a single artifact satisfies multiple objectives, list it once.
  • Evidence type is one of: screenshot | export | document | link.

Only propose a control if this page's own text plausibly supports it -- do \
not invent coverage the page doesn't actually describe.

Return ONLY a JSON object -- no markdown fences, no prose, just JSON:

{
  "controls": [
    {
      "control": "AC.L2-3.1.1",
      "objectives": ["a", "b"],
      "classification": "shared",
      "provider_contribution": "What the documented capability provides.",
      "customer_action": "What the customer must still do.",
      "evidence": [
        {"artifact": "Admin console role export", "type": "export"}
      ],
      "note": "optional note"
    }
  ]
}
"""


_MAX_URL_SUGGESTIONS = 8

_SUGGEST_URLS_SYSTEM_PROMPT = """\
You are helping a compliance analyst find a vendor's OFFICIAL PLATFORM or \
ADMIN DOCUMENTATION for security-control research -- NOT sales pages, \
marketing brochures, blog posts, or press releases.

Given a product name and provider, suggest up to {max_suggestions} \
candidate URLs likely to describe the platform's real technical/admin \
capabilities (role-based access control, audit logging, encryption, MFA, \
session management, API security, data retention, etc.) -- the kind of \
page a systems administrator would read to actually configure the \
product, not a page written to sell it.

For each URL, give a one-line rationale explaining why it's likely useful \
and, briefly, why it is not a marketing page.

Do not fabricate a URL you are not reasonably confident is real. If you \
are not confident of real documentation URLs for this vendor, return \
fewer suggestions -- or none -- rather than guessing sales pages. Every \
suggestion is only a proposal: a human approves before anything is \
fetched, and nothing is trusted just because it appears here.

Return ONLY a JSON object -- no markdown fences, no prose, just JSON:

{{
  "suggestions": [
    {{"url": "https://docs.example.com/admin/security", "rationale": "..."}}
  ]
}}
"""


class ResearchIngestError(Exception):
    """Any expected failure in the web-research pass -- caps exceeded, an
    unconfigured AI provider, or a response that doesn't parse. Callers
    catch this specifically and surface a clean 4xx, matching
    DocumentIngestError's own role in the document pipeline."""


@dataclass
class UrlSuggestion:
    url: str
    rationale: str


def suggest_documentation_urls(
    *, product_name: str, provider: str, ai_provider: AIProvider
) -> list[UrlSuggestion]:
    """Ask the AI for candidate documentation URLs. These are proposals
    only -- see this module's own docstring's §1 framing: propose, then a
    human approves, then (and only then) fetch. A hallucinated URL here is
    harmless; it simply fails to fetch later like any other bad URL, never
    silently treated as real."""
    prompt = _SUGGEST_URLS_SYSTEM_PROMPT.format(max_suggestions=_MAX_URL_SUGGESTIONS)
    try:
        raw_json = ai_provider.complete(
            prompt,
            f"Product name: {product_name}\nProvider: {provider}",
            max_tokens=2048,
        )
    except RuntimeError as exc:
        raise ResearchIngestError(str(exc)) from exc

    try:
        data = _parse_ai_json(raw_json)
    except DocumentIngestError as exc:
        raise ResearchIngestError(str(exc)) from exc

    out: list[UrlSuggestion] = []
    for raw in data.get("suggestions", []):
        if not isinstance(raw, dict):
            continue
        url = raw.get("url")
        rationale = raw.get("rationale")
        if isinstance(url, str) and url.startswith("https://") and isinstance(rationale, str):
            out.append(UrlSuggestion(url=url, rationale=rationale))
    return out[:_MAX_URL_SUGGESTIONS]


def _parse_web_control(raw: dict[str, Any], *, source_url: str) -> ControlEntry | None:
    """Like document.py:_parse_control, but for the web-research schema
    (no candidate_state/coverage_basis in the model's own output -- those
    are derived/left-unset exactly as the document pipeline already does)
    and with `source` ALWAYS assigned here, in code, to the exact URL this
    page was fetched from -- never read from the model's JSON, so a
    reviewer's per-claim attribution can never be spoofed by confused or
    adversarial model output.

    Silently skips (returns None for) a customer_owns entry the model
    produced despite the prompt instruction not to -- this pass only ever
    proposes ADDITIONAL coverage; a disclaim from a web page carries no
    weight at all, so there is nothing useful to keep, and there is no
    caller here that needs the exclusion reported the way document.py's
    evidence-minimization does for its own, different, purpose.
    """
    try:
        cls = Classification(raw["classification"])
    except (KeyError, ValueError):
        return None
    if cls == Classification.CUSTOMER_OWNS:
        return None

    evidence = [
        EvidenceSpec(artifact=ev["artifact"], type=ev["type"], kb=ev.get("kb"))
        for ev in raw.get("evidence", [])
        if isinstance(ev, dict) and ev.get("artifact") and ev.get("type")
    ]
    entry = ControlEntry(
        control=raw["control"],
        classification=cls,
        candidate_state=CandidateState.PENDING_EVIDENCE,
        objectives=[str(o) for o in raw.get("objectives", [])],
        provider_contribution=raw.get("provider_contribution"),
        customer_action=raw.get("customer_action"),
        evidence=evidence,
        note=raw.get("note"),
        scope_note=None,
        coverage_basis=None,
        source=source_url,
    )
    return _apply_evidence_minimization(entry)


def ingest_web_page(
    text: str, *, source_url: str, ai_provider: AIProvider
) -> list[ControlEntry]:
    """Run the web-research AI pass over ONE already-fetched page's
    extracted text. Returns candidate ControlEntry rows, each stamped with
    source_url. Raises ResearchIngestError for any expected failure.
    """
    if len(text) > MAX_PAGE_CHARS:
        raise ResearchIngestError(
            f"{source_url}: extracted text ({len(text):,} characters) exceeds the "
            f"{MAX_PAGE_CHARS:,}-character per-page research limit."
        )
    try:
        raw_json = ai_provider.complete(
            _WEB_SYSTEM_PROMPT,
            f"=== Source: {source_url} ===\n{text}",
            max_tokens=_INGEST_MAX_TOKENS,
        )
    except RuntimeError as exc:
        raise ResearchIngestError(str(exc)) from exc

    try:
        data = _parse_ai_json(raw_json)
    except DocumentIngestError as exc:
        raise ResearchIngestError(str(exc)) from exc

    entries: list[ControlEntry] = []
    for raw in data.get("controls", []):
        if not isinstance(raw, dict):
            continue
        try:
            parsed = _parse_web_control(raw, source_url=source_url)
        except (KeyError, TypeError, ValueError):
            continue
        if parsed is not None:
            entries.append(parsed)
    return entries


def _expand_ids(control: str | list[str]) -> list[str]:
    return list(control) if isinstance(control, list) else [control]


@dataclass
class ResearchMergeSummary:
    controls_added_from_web: int
    conflicts_flagged: int


def merge_research(
    primary: BaselineEntry, web_entries: list[ControlEntry]
) -> tuple[dict[str, Any], list[DisclaimFlag], ResearchMergeSummary]:
    """THE §0 ENFORCEMENT POINT. Merge web-research candidates onto the
    document pipeline's own output at the plain-dict level (the same shape
    build_preview() already consumes), so the merged result flows through
    the EXACT SAME dry-run preview / disclaim-flag / evidence-minimization
    path a document-only import already uses -- one pipeline, not two.

    Every web_entries item is expected to already be single-control-id
    (ingest_web_page() only ever proposes one control id at a time in
    today's prompt) -- expanded defensively anyway in case that ever
    changes, since the merge decision genuinely is per individual control.

    For each control id a web entry claims:
      - Not present in `primary` at all -> genuinely new coverage. Appended
        as a new row, `source` already pointing at the fetching URL.
      - Present in `primary` and NOT disclaimed (classification isn't
        customer_owns and its own supporting text doesn't read as a
        disclaim) -> the CRM/baseline already covers it; nothing is added
        or changed. Web research supplements gaps, it doesn't duplicate or
        re-litigate what the document pipeline already settled.
      - Present in `primary` AND disclaimed -> THE RULE. Never added, never
        upgraded. A DisclaimFlag is raised on the CRM's own existing row
        instead, naming the URL and the classification it proposed, so a
        reviewer sees the conflict without the mapping ever silently
        crediting the vendor for something the CRM itself says it doesn't
        do.
    """
    data = to_yaml_dict(primary)
    controls_list: list[dict[str, Any]] = data.setdefault("controls", [])

    # control_id -> (row index in controls_list, the row dict, is_disclaimed)
    crm_index: dict[str, tuple[int, dict[str, Any]]] = {}
    for i, row in enumerate(controls_list):
        for cid in _expand_ids(row.get("control", [])):
            crm_index.setdefault(cid, (i, row))

    def _row_disclaims(row: dict[str, Any]) -> bool:
        if row.get("classification") == "customer_owns":
            return True
        return _disclaims_coverage(
            row.get("note"),
            row.get("provider_contribution"),
            row.get("customer_action"),
            row.get("scope_note"),
        )

    extra_flags: list[DisclaimFlag] = []
    controls_added = 0
    conflicts = 0

    for entry in web_entries:
        for cid in _expand_ids(entry.control):
            existing = crm_index.get(cid)
            if existing is None:
                # Genuinely new -- add as its own row (single control id;
                # not re-batched into any existing group).
                new_row = to_yaml_dict(
                    BaselineEntry(product=primary.product, controls=[entry])
                )["controls"][0]
                new_row["control"] = [cid]
                new_idx = len(controls_list)
                controls_list.append(new_row)
                crm_index[cid] = (new_idx, new_row)
                controls_added += 1
                continue

            row_idx, row = existing
            if _row_disclaims(row):
                conflicts += 1
                extra_flags.append(
                    DisclaimFlag(
                        row_index=row_idx,
                        message=(
                            f"Web research at {entry.source} suggests "
                            f"{entry.classification.value!r} coverage for {cid}, but "
                            "the CRM/baseline disclaims this control -- the web "
                            "proposal was NOT added. A CRM disclaim can never be "
                            "silently overridden by documentation research; confirm "
                            "manually if you believe the CRM text is stale."
                        ),
                    )
                )
            # else: CRM already covers this control without disclaiming it
            # -- nothing to add, the existing row stands as-is.

    return (
        data,
        extra_flags,
        ResearchMergeSummary(
            controls_added_from_web=controls_added,
            conflicts_flagged=conflicts,
        ),
    )
