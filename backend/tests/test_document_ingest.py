"""Tests for the document-ingestion importer.

rocketcyber.yaml is the primary fixture — it is the expected output shape for a
two-document ingest (MSP baseline + vendor CRM). Tests verify:

  1. The YAML loads cleanly into domain types (baseline.py).
  2. Evidence-minimization invariants hold in the loaded data.
  3. compute_summary() matches the hand-authored YAML summary.
  4. The minimization enforcement function is a hard code-level guarantee
     (not just a prompt instruction), tested independently.
  5. The full ingest pipeline works end-to-end with a stub AI provider that
     returns a pre-baked JSON response, including the case where the model
     ignores the no-evidence-for-customer_owns rule (enforcement must strip it).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.ai.base import AIProvider
from app.ai.none_ import NullProvider
from app.baseline import (
    CandidateState,
    Classification,
    ControlEntry,
    EvidenceSpec,
    load_baseline,
)
from app.importers.document import (
    _apply_evidence_minimization,
    extract_text,
    ingest_document,
)

ROCKETCYBER_YAML = (
    Path(__file__).resolve().parents[2] / "baselines" / "rocketcyber.yaml"
)

# ---------------------------------------------------------------------------
# YAML loader + domain type tests
# ---------------------------------------------------------------------------


def test_load_rocketcyber_yaml_product_meta():
    entry = load_baseline(ROCKETCYBER_YAML)
    assert entry.product.key == "rocketcyber"
    assert entry.product.provider == "Kaseya"
    assert entry.product.category == "ESP"
    assert entry.product.asset_type == "SPA"
    assert len(entry.product.assumed_config) >= 1
    assert len(entry.product.source_docs) >= 1


def test_load_rocketcyber_yaml_control_count():
    entry = load_baseline(ROCKETCYBER_YAML)
    # The fixture covers AC, AT, AU, CM, IA (batch), IR, MA, SC, SI families.
    assert len(entry.controls) >= 10


def test_ia_family_is_customer_owns():
    entry = load_baseline(ROCKETCYBER_YAML)
    ia_entries = [
        c for c in entry.controls
        if (
            isinstance(c.control, list)
            and any("IA." in x for x in c.control)
        ) or (
            isinstance(c.control, str) and c.control.startswith("IA.")
        )
    ]
    assert ia_entries, "IA family entry must be present in rocketcyber.yaml"
    for ia in ia_entries:
        assert ia.classification == Classification.CUSTOMER_OWNS
        assert ia.candidate_state == CandidateState.NOT_SATISFIED_BY_PRODUCT
        assert ia.evidence == [], (
            "customer_owns must carry no evidence specs"
        )
        # The note explaining WHY should still be present.
        assert ia.note, "IA customer_owns entry should carry an explanatory note"


def test_ac_l2_3_1_8_is_customer_owns():
    """Individual (non-batched) customer_owns entry."""
    entry = load_baseline(ROCKETCYBER_YAML)
    ac_8 = next(
        (c for c in entry.controls if c.control == "AC.L2-3.1.8"),
        None,
    )
    assert ac_8 is not None
    assert ac_8.classification == Classification.CUSTOMER_OWNS
    assert ac_8.candidate_state == CandidateState.NOT_SATISFIED_BY_PRODUCT
    assert ac_8.evidence == []


def test_au_family_has_provider_satisfies():
    entry = load_baseline(ROCKETCYBER_YAML)
    au_ps = [
        c for c in entry.controls
        if isinstance(c.control, str)
        and c.control.startswith("AU.")
        and c.classification == Classification.PROVIDER_SATISFIES
    ]
    assert len(au_ps) >= 2, "AU.L2-3.3.1 and AU.L2-3.3.2 should be provider_satisfies"


def test_ir_l2_3_6_1_has_evidence():
    entry = load_baseline(ROCKETCYBER_YAML)
    ir = next(
        (c for c in entry.controls if c.control == "IR.L2-3.6.1"),
        None,
    )
    assert ir is not None
    assert ir.classification == Classification.PROVIDER_SATISFIES
    assert len(ir.evidence) >= 1
    assert all(isinstance(e, EvidenceSpec) for e in ir.evidence)


def test_evidence_minimization_invariant_holds_in_yaml():
    """No customer_owns control in the fixture may carry evidence specs."""
    entry = load_baseline(ROCKETCYBER_YAML)
    violations = [
        c for c in entry.controls
        if c.classification == Classification.CUSTOMER_OWNS and c.evidence
    ]
    assert violations == [], (
        f"Evidence-minimization violated in rocketcyber.yaml for: "
        f"{[v.control for v in violations]}"
    )


def test_compute_summary_provider_satisfies():
    entry = load_baseline(ROCKETCYBER_YAML)
    computed = entry.compute_summary()
    expected = {
        "AC.L2-3.1.11",
        "AU.L2-3.3.1",
        "AU.L2-3.3.2",
        "AU.L2-3.3.8",
        "IR.L2-3.6.1",
        "SC.L2-3.13.15",
        "SI.L2-3.14.6",
    }
    assert set(computed.provider_satisfies) == expected


def test_compute_summary_shared():
    entry = load_baseline(ROCKETCYBER_YAML)
    computed = entry.compute_summary()
    expected = {
        "AC.L2-3.1.1",
        "AC.L2-3.1.2",
        "AC.L2-3.1.5",
        "AT.L2-3.2.2",
        "AU.L2-3.3.3",
        "CM.L2-3.4.2",
        "MA.L2-3.7.1",
        "SI.L2-3.14.1",
    }
    assert set(computed.shared) == expected


def test_compute_summary_customer_owns_includes_ia_and_ac():
    """The IA batch (11 controls) + AC.L2-3.1.8 = 12 customer_owns entries."""
    entry = load_baseline(ROCKETCYBER_YAML)
    computed = entry.compute_summary()
    assert "AC.L2-3.1.8" in computed.customer_owns
    ia_owned = [x for x in computed.customer_owns if x.startswith("IA.")]
    assert len(ia_owned) == 11
    assert len(computed.customer_owns) == 12


# ---------------------------------------------------------------------------
# Evidence-minimization enforcement unit tests (no YAML, no AI)
# ---------------------------------------------------------------------------


def test_minimization_strips_evidence_from_customer_owns():
    """The enforcement function must strip evidence even if the AI added it."""
    dirty = ControlEntry(
        control="IA.L2-3.5.1",
        classification=Classification.CUSTOMER_OWNS,
        candidate_state=CandidateState.PENDING_EVIDENCE,  # wrong — must be corrected
        evidence=[EvidenceSpec(artifact="Something", type="screenshot")],
        provider_contribution="Provider says...",
        customer_action="Customer does...",
    )
    clean = _apply_evidence_minimization(dirty)
    assert clean.evidence == []
    assert clean.candidate_state == CandidateState.NOT_SATISFIED_BY_PRODUCT
    assert clean.provider_contribution is None
    assert clean.customer_action is None


def test_minimization_preserves_shared_evidence():
    ev = EvidenceSpec(artifact="Portal role list", type="export", kb="IAM guide")
    entry = ControlEntry(
        control="AC.L2-3.1.2",
        classification=Classification.SHARED,
        candidate_state=CandidateState.PENDING_EVIDENCE,
        evidence=[ev],
        provider_contribution="RBAC.",
        customer_action="Assign roles.",
    )
    result = _apply_evidence_minimization(entry)
    assert result.evidence == [ev]
    assert result.candidate_state == CandidateState.PENDING_EVIDENCE
    assert result.provider_contribution == "RBAC."


def test_minimization_preserves_provider_satisfies_evidence():
    ev = EvidenceSpec(artifact="Retention config", type="screenshot")
    entry = ControlEntry(
        control="AU.L2-3.3.1",
        classification=Classification.PROVIDER_SATISFIES,
        candidate_state=CandidateState.PENDING_EVIDENCE,
        evidence=[ev],
    )
    result = _apply_evidence_minimization(entry)
    assert result.evidence == [ev]
    assert result.candidate_state == CandidateState.PENDING_EVIDENCE


# ---------------------------------------------------------------------------
# extract_text: format guard
# ---------------------------------------------------------------------------


def test_extract_text_rejects_unsupported_format():
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "matrix.csv"
        bad.write_text("a,b,c", encoding="utf-8")
        with pytest.raises(ValueError, match="Unsupported document type"):
            extract_text(bad)


# ---------------------------------------------------------------------------
# Full ingest pipeline with stub AI provider
# ---------------------------------------------------------------------------

# Pre-baked AI response. Deliberately includes evidence on the customer_owns
# entry to verify the code-level enforcement strips it (not just the prompt).
_STUB_AI_RESPONSE = json.dumps({
    "product": {
        "name": "RocketCyber Managed SIEM + SOC",
        "provider": "Kaseya",
        "role": "24/7 managed SOC; aggregates telemetry and triages alerts.",
        "assumed_config": ["Agent deployed to all in-scope endpoints"],
        "source_docs": ["RocketCyber_CRM.docx"],
    },
    "controls": [
        {
            "control": "AU.L2-3.3.1",
            "objectives": ["a", "b", "c"],
            "classification": "provider_satisfies",
            "provider_contribution": "Generates audit records; 1-year retention.",
            "customer_action": "Configure agents to capture required event types.",
            "evidence": [
                {
                    "artifact": "Defined event types",
                    "type": "screenshot",
                    "kb": "Configuring the Syslog Collector",
                }
            ],
            "candidate_state": "pending_evidence",
        },
        {
            "control": "AC.L2-3.1.1",
            "objectives": ["a", "b"],
            "classification": "shared",
            "provider_contribution": "SSO + RBAC for portal access.",
            "customer_action": "Bind SSO to customer IdP.",
            "evidence": [
                {"artifact": "KaseyaOne SSO config", "type": "screenshot"}
            ],
            "candidate_state": "pending_evidence",
        },
        {
            # Model misbehaves and adds evidence for customer_owns —
            # enforcement must strip it.
            "control": ["IA.L2-3.5.1", "IA.L2-3.5.2", "IA.L2-3.5.3"],
            "objectives": [],
            "classification": "customer_owns",
            "note": "RocketCyber does not manage identity. Customer IdP owns IA.",
            "evidence": [
                {"artifact": "Bad artifact the model added", "type": "screenshot"}
            ],
            "candidate_state": "pending_evidence",  # also wrong
        },
    ],
})


class _StubAIProvider(AIProvider):
    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        return _STUB_AI_RESPONSE


def _fake_docx() -> Path:
    """Return a path to a zero-byte .docx; extract_text is always patched."""
    td = tempfile.mkdtemp()
    p = Path(td) / "crm.docx"
    p.write_bytes(b"")
    return p


def test_ingest_pipeline_au_provider_satisfies():
    fake_doc = _fake_docx()
    with patch("app.importers.document.extract_text", return_value="stub text"):
        entry = ingest_document(
            fake_doc,
            product_key="rocketcyber",
            ai_provider=_StubAIProvider(),
        )

    au = next(c for c in entry.controls if c.control == "AU.L2-3.3.1")
    assert au.classification == Classification.PROVIDER_SATISFIES
    assert au.candidate_state == CandidateState.PENDING_EVIDENCE
    assert len(au.evidence) == 1
    assert au.evidence[0].kb == "Configuring the Syslog Collector"


def test_ingest_pipeline_ia_minimization_enforced():
    """Enforcement must strip the evidence the stub AI wrongly included."""
    fake_doc = _fake_docx()
    with patch("app.importers.document.extract_text", return_value="stub text"):
        entry = ingest_document(
            fake_doc,
            product_key="rocketcyber",
            ai_provider=_StubAIProvider(),
        )

    ia = next(c for c in entry.controls if isinstance(c.control, list))
    assert ia.classification == Classification.CUSTOMER_OWNS
    assert ia.candidate_state == CandidateState.NOT_SATISFIED_BY_PRODUCT
    assert ia.evidence == [], "Code-level enforcement must have stripped this"
    assert ia.provider_contribution is None
    assert ia.customer_action is None


def test_ingest_pipeline_summary_computed():
    fake_doc = _fake_docx()
    with patch("app.importers.document.extract_text", return_value="stub text"):
        entry = ingest_document(
            fake_doc,
            product_key="rocketcyber",
            ai_provider=_StubAIProvider(),
        )

    assert entry.summary is not None
    assert "AU.L2-3.3.1" in entry.summary.provider_satisfies
    assert "AC.L2-3.1.1" in entry.summary.shared
    assert set(entry.summary.customer_owns) == {
        "IA.L2-3.5.1", "IA.L2-3.5.2", "IA.L2-3.5.3"
    }


def test_ingest_pipeline_product_meta_preserved():
    fake_doc = _fake_docx()
    with patch("app.importers.document.extract_text", return_value="stub text"):
        entry = ingest_document(
            fake_doc,
            product_key="rocketcyber",
            ai_provider=_StubAIProvider(),
            category="ESP",
            asset_type="SPA",
        )

    assert entry.product.key == "rocketcyber"
    assert entry.product.category == "ESP"
    assert entry.product.asset_type == "SPA"
    assert entry.product.name == "RocketCyber Managed SIEM + SOC"


def test_null_provider_raises():
    with pytest.raises(RuntimeError, match="No AI provider configured"):
        NullProvider().complete("sys", "user")
