"""Tests for importers/research.py -- the AI-research-of-vendor-platform-
documentation slice. No DB needed: merge_research() is a pure function over
BaselineEntry/ControlEntry objects, and ingest_web_page()/
suggest_documentation_urls() are exercised with a stub AIProvider exactly
like test_document_ingest.py's own pattern.

§0 is the headline requirement this file exists to prove: web content may
propose additional coverage, but can never override a CRM disclaim. Every
test in the "merge_research" section below is really a variation on that
one rule.
"""
from __future__ import annotations

import json

import pytest

from app.ai.base import AIProvider
from app.baseline import (
    BaselineEntry,
    CandidateState,
    Classification,
    ControlEntry,
    EvidenceSpec,
    ProductMeta,
)
from app.importers.research import (
    MAX_PAGE_CHARS,
    ResearchIngestError,
    ingest_web_page,
    merge_research,
    suggest_documentation_urls,
)

_PRODUCT = ProductMeta(
    key="testvendor",
    name="Test Vendor Platform",
    provider="Test Vendor Inc",
    category="ESP",
    asset_type="SPA",
    framework="NIST 800-171 Rev 2 / CMMC L2",
    role="Test product for research-merge tests.",
)


def _primary(controls: list[ControlEntry]) -> BaselineEntry:
    return BaselineEntry(product=_PRODUCT, controls=controls)


def _crm_entry(
    control: str, classification: Classification, *, note: str | None = None
) -> ControlEntry:
    # Always a single-element list, never a bare string -- so
    # to_yaml_dict()'s output shape is predictable in these tests
    # regardless of which form a real AI response happens to use (the
    # schema accepts both; merge_research()'s own _expand_ids() handles
    # either uniformly, this is purely about keeping test assertions simple).
    return ControlEntry(
        control=[control],
        classification=classification,
        candidate_state=(
            CandidateState.NOT_SATISFIED_BY_PRODUCT
            if classification == Classification.CUSTOMER_OWNS
            else CandidateState.PENDING_EVIDENCE
        ),
        objectives=["a"],
        note=note,
    )


def _web_entry(
    control: str, classification: Classification, *, source: str = "https://docs.vendor.com/admin"
) -> ControlEntry:
    return ControlEntry(
        control=[control],
        classification=classification,
        candidate_state=CandidateState.PENDING_EVIDENCE,
        objectives=["a"],
        source=source,
    )


# ---------------------------------------------------------------------------
# merge_research: §0's rule
# ---------------------------------------------------------------------------


def test_web_proposal_for_new_control_is_added():
    primary = _primary([_crm_entry("AC.L2-3.1.1", Classification.SHARED)])
    web = [_web_entry("AU.L2-3.3.1", Classification.PROVIDER_SATISFIES)]

    data, flags, summary = merge_research(primary, web)

    # _crm_entry/_web_entry always build control=[id], so to_yaml_dict's
    # output is always a single-element list here -- no need to handle
    # the bare-string form the real schema also allows.
    control_ids = [c["control"][0] for c in data["controls"]]
    assert "AU.L2-3.3.1" in control_ids
    assert summary.controls_added_from_web == 1
    assert summary.conflicts_flagged == 0
    assert flags == []


def test_web_proposal_for_new_control_is_stamped_with_its_source():
    primary = _primary([])
    web = [_web_entry("AU.L2-3.3.1", Classification.PROVIDER_SATISFIES, source="https://docs.vendor.com/logging")]

    data, _flags, _summary = merge_research(primary, web)

    added = next(c for c in data["controls"] if c["control"] == ["AU.L2-3.3.1"])
    assert added["source"] == "https://docs.vendor.com/logging"


def test_web_proposal_matching_existing_non_disclaiming_crm_entry_is_not_duplicated():
    primary = _primary([_crm_entry("AC.L2-3.1.1", Classification.SHARED)])
    web = [_web_entry("AC.L2-3.1.1", Classification.PROVIDER_SATISFIES)]

    data, flags, summary = merge_research(primary, web)

    matching = [c for c in data["controls"] if c["control"] == ["AC.L2-3.1.1"]]
    assert len(matching) == 1, "must not duplicate a control the CRM already covers"
    assert matching[0]["classification"] == "shared", "the CRM's own row must stand unchanged"
    assert summary.controls_added_from_web == 0
    assert flags == []


def test_web_proposal_conflicting_with_customer_owns_crm_entry_is_flagged_not_added():
    """THE §0 HEADLINE TEST: a CRM disclaim (customer_owns) must never be
    silently upgraded by a web-research proposal."""
    crm_entry = _crm_entry(
        "IA.L2-3.5.1", Classification.CUSTOMER_OWNS, note="Customer's IdP owns this."
    )
    primary = _primary([crm_entry])
    web = [
        _web_entry(
            "IA.L2-3.5.1", Classification.PROVIDER_SATISFIES,
            source="https://docs.vendor.com/sso",
        )
    ]

    data, flags, summary = merge_research(primary, web)

    matching = [c for c in data["controls"] if c["control"] == ["IA.L2-3.5.1"]]
    assert len(matching) == 1
    assert matching[0]["classification"] == "customer_owns", "must NEVER be upgraded"
    assert summary.controls_added_from_web == 0
    assert summary.conflicts_flagged == 1
    assert len(flags) == 1
    assert flags[0].row_index == 0
    assert "docs.vendor.com/sso" in flags[0].message
    assert "IA.L2-3.5.1" in flags[0].message


def test_web_proposal_conflicting_with_disclaiming_text_is_flagged_even_when_classified_shared():
    """The exact real failure mode this whole feature exists to guard
    against (baseline_import.py's own disclaim-flag docstring): an entry
    classified shared/provider_satisfies whose OWN text reads as a
    disclaim must still count as a CRM disclaim for merge purposes, not
    just an explicit customer_owns classification."""
    primary = _primary(
        [
            _crm_entry(
                "AC.L2-3.1.8",
                Classification.SHARED,
                note="Kaseya explicitly states it does not implement or enforce this control.",
            )
        ]
    )
    web = [_web_entry("AC.L2-3.1.8", Classification.PROVIDER_SATISFIES)]

    data, flags, summary = merge_research(primary, web)

    matching = [c for c in data["controls"] if c["control"] == ["AC.L2-3.1.8"]]
    assert matching[0]["classification"] == "shared", "must not upgrade to provider_satisfies"
    assert summary.conflicts_flagged == 1
    assert len(flags) == 1


def test_conflict_never_changes_the_crm_rows_other_fields_either():
    """Assert the negative explicitly: a flagged conflict touches nothing
    about the existing row -- not classification, not note, not anything."""
    crm_row = _crm_entry(
        "IA.L2-3.5.1", Classification.CUSTOMER_OWNS, note="Customer's IdP owns this."
    )
    primary = _primary([crm_row])
    web = [_web_entry("IA.L2-3.5.1", Classification.SHARED)]

    data, _flags, _summary = merge_research(primary, web)

    row = data["controls"][0]
    assert row["classification"] == "customer_owns"
    assert row["note"] == "Customer's IdP owns this."
    assert row.get("provider_contribution") is None
    assert row.get("customer_action") is None


def test_multiple_web_pages_can_each_add_different_new_controls():
    primary = _primary([_crm_entry("AC.L2-3.1.1", Classification.SHARED)])
    web = [
        _web_entry("AU.L2-3.3.1", Classification.PROVIDER_SATISFIES, source="https://docs.vendor.com/a"),
        _web_entry("SI.L2-3.14.1", Classification.SHARED, source="https://docs.vendor.com/b"),
    ]

    data, _flags, summary = merge_research(primary, web)

    control_ids = {c["control"][0] for c in data["controls"]}
    assert {"AC.L2-3.1.1", "AU.L2-3.3.1", "SI.L2-3.14.1"} <= control_ids
    assert summary.controls_added_from_web == 2


def test_web_proposal_never_touched_when_no_research_documents():
    """Sanity check that an empty web_entries list is a true no-op --
    merge_research(primary, []) must reproduce exactly what to_yaml_dict(primary)
    would (the plain, non-research path)."""
    from app.baseline import to_yaml_dict

    primary = _primary([_crm_entry("AC.L2-3.1.1", Classification.SHARED)])
    data, flags, summary = merge_research(primary, [])
    assert data == to_yaml_dict(primary)
    assert flags == []
    assert summary.controls_added_from_web == 0
    assert summary.conflicts_flagged == 0


# ---------------------------------------------------------------------------
# ingest_web_page: the per-page AI pass
# ---------------------------------------------------------------------------


class _StubWebAIProvider(AIProvider):
    def __init__(self, response: str):
        self._response = response

    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        return self._response


_WEB_RESPONSE = """
{
  "controls": [
    {
      "control": "AU.L2-3.3.1",
      "objectives": ["a"],
      "classification": "provider_satisfies",
      "provider_contribution": "The admin console logs all authentication events.",
      "customer_action": null,
      "evidence": [{"artifact": "Audit log export", "type": "export"}],
      "note": "Documented in the admin guide's logging section."
    },
    {
      "control": "IA.L2-3.5.1",
      "objectives": ["a"],
      "classification": "customer_owns",
      "note": "This page also mentions customer-owned identity -- should be dropped."
    }
  ]
}
"""


# Long enough to clear MIN_PAGE_CHARS -- matches test_document_ingest.py's
# own _STUB_TEXT convention exactly (that guard's real-world counterpart).
_STUB_PAGE_TEXT = (
    "Stub extracted page text, long enough to clear the minimum-extracted-"
    "text guard so these tests exercise what they're actually testing."
)


def test_ingest_web_page_stamps_source_on_every_entry():
    entries = ingest_web_page(
        _STUB_PAGE_TEXT, source_url="https://docs.vendor.com/logging",
        ai_provider=_StubWebAIProvider(_WEB_RESPONSE),
    )
    assert len(entries) == 1  # the customer_owns one is dropped
    assert entries[0].control == "AU.L2-3.3.1"
    assert entries[0].source == "https://docs.vendor.com/logging"


def test_ingest_web_page_never_proposes_customer_owns():
    entries = ingest_web_page(
        _STUB_PAGE_TEXT, source_url="https://docs.vendor.com/x",
        ai_provider=_StubWebAIProvider(_WEB_RESPONSE),
    )
    assert all(e.classification != Classification.CUSTOMER_OWNS for e in entries)


def test_ingest_web_page_applies_evidence_minimization():
    entries = ingest_web_page(
        _STUB_PAGE_TEXT, source_url="https://docs.vendor.com/x",
        ai_provider=_StubWebAIProvider(_WEB_RESPONSE),
    )
    au = entries[0]
    assert au.evidence == [EvidenceSpec(artifact="Audit log export", type="export", kb=None)]
    assert au.candidate_state == CandidateState.PENDING_EVIDENCE


def test_ingest_web_page_rejects_oversized_page():
    text = "a" * (MAX_PAGE_CHARS + 1)
    with pytest.raises(ResearchIngestError, match="character"):
        ingest_web_page(
            text, source_url="https://docs.vendor.com/huge",
            ai_provider=_StubWebAIProvider("{}"),
        )


def test_ingest_web_page_rejects_near_empty_page_without_calling_ai():
    """Confirmed live (2026-09-17, the real datto-rmm test run): a page
    that renders its content via client-side JavaScript extracts to 0
    characters, and an AI call given that little input still confidently
    fabricated 9 control proposals -- including control ids that don't
    match this framework's own naming convention. Refused before the AI
    is ever called, matching document.py's own extraction-hardening
    guard."""
    calls = []

    class _CountingProvider(_StubWebAIProvider):
        def complete(self, system, user, *, max_tokens=8192):
            calls.append(user)
            return super().complete(system, user, max_tokens=max_tokens)

    with pytest.raises(ResearchIngestError, match="character"):
        ingest_web_page(
            "", source_url="https://docs.vendor.com/js-rendered",
            ai_provider=_CountingProvider(_WEB_RESPONSE),
        )
    assert calls == [], "must never reach the AI call with near-empty input"


def test_ingest_web_page_rejects_whitespace_only_page():
    with pytest.raises(ResearchIngestError, match="character"):
        ingest_web_page(
            "   \n\n   ", source_url="https://docs.vendor.com/blank",
            ai_provider=_StubWebAIProvider(_WEB_RESPONSE),
        )


def test_ingest_web_page_handles_malformed_json():
    with pytest.raises(ResearchIngestError, match="JSON"):
        ingest_web_page(
            _STUB_PAGE_TEXT, source_url="https://docs.vendor.com/x",
            ai_provider=_StubWebAIProvider("not json at all"),
        )


def test_ingest_web_page_ignores_malformed_control_entries_without_failing():
    response = json.dumps({
        "controls": [
            {"control": "AC.L2-3.1.1"},  # missing classification
            "not-a-dict",
            {"classification": "bogus", "control": "AU.L2-3.3.1"},  # invalid enum value
        ]
    })
    entries = ingest_web_page(
        _STUB_PAGE_TEXT, source_url="https://docs.vendor.com/x",
        ai_provider=_StubWebAIProvider(response),
    )
    assert entries == []


# ---------------------------------------------------------------------------
# suggest_documentation_urls
# ---------------------------------------------------------------------------


_SUGGEST_RESPONSE = json.dumps({
    "suggestions": [
        {"url": "https://docs.vendor.com/admin/security", "rationale": "Official admin guide."},
        {"url": "http://not-https.example.com/", "rationale": "Not https -- filtered."},
        {"url": "https://vendor.com/pricing", "rationale": "This is actually a sales page."},
    ]
})


def test_suggest_documentation_urls_filters_non_https():
    suggestions = suggest_documentation_urls(
        product_name="Test Vendor Platform", provider="Test Vendor Inc",
        ai_provider=_StubWebAIProvider(_SUGGEST_RESPONSE),
    )
    urls = [s.url for s in suggestions]
    assert "https://docs.vendor.com/admin/security" in urls
    assert not any(u.startswith("http://") for u in urls)


def test_suggest_documentation_urls_is_proposal_only_no_fetch_happens():
    """suggest_documentation_urls must never itself fetch anything -- it
    only returns candidate URLs for a human to approve. Confirmed by
    inspecting the function never imports/calls web_fetch."""
    import inspect

    from app.importers import research as research_module

    source = inspect.getsource(research_module.suggest_documentation_urls)
    assert "fetch_url_safely" not in source
    assert "web_fetch" not in source
