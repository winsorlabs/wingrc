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
from app.baseline import BaselineEntry as _BaselineEntry
from app.baseline import (
    CandidateState,
    Classification,
    ControlEntry,
    EvidenceSpec,
    ProductMeta,
    load_baseline,
    to_yaml_dict,
)
from app.importers.document import (
    _MAX_INPUT_CHARS,
    _MIN_EXTRACTED_TEXT_CHARS,
    DocumentIngestError,
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
# Input hardening: realistic malformed-upload failure modes, each ending in
# a specific DocumentIngestError naming the file, never a raw traceback.
# Built from genuinely broken real files (truncated/encrypted/legacy-format
# bytes), not asserted from reading the implementation.
# ---------------------------------------------------------------------------


def _write_temp(suffix: str, data: bytes) -> Path:
    fd, path = tempfile.mkstemp(suffix=suffix)
    with open(fd, "wb") as f:
        f.write(data)
    return Path(path)


def _real_pdf_bytes(
    text: str = (
        "Some real extractable text for a legitimate document, long enough "
        "to clear the ingestion threshold."
    ),
) -> bytes:
    """A genuinely valid, parseable PDF with a real text content stream --
    built with pypdf alone (no new dependency needed)."""
    import io

    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = PdfWriter()
    page = w.add_blank_page(width=300, height=200)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 20 150 Td ({text}) Tj ET".encode())
    font_dict = DictionaryObject()
    font_dict[NameObject("/Type")] = NameObject("/Font")
    font_dict[NameObject("/Subtype")] = NameObject("/Type1")
    font_dict[NameObject("/BaseFont")] = NameObject("/Helvetica")
    resources = DictionaryObject()
    font_res = DictionaryObject()
    font_res[NameObject("/F1")] = w._add_object(font_dict)
    resources[NameObject("/Font")] = font_res
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = w._add_object(stream)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _blank_pdf_bytes() -> bytes:
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _encrypted_pdf_bytes() -> bytes:
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.encrypt("secret123")
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


_LEGACY_DOC_BYTES = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 200


def test_extract_pdf_truncated_reports_specific_message():
    full = _real_pdf_bytes()
    path = _write_temp(".pdf", full[: len(full) // 2])
    try:
        with pytest.raises(DocumentIngestError, match="corrupt or truncated"):
            extract_text(path)
    finally:
        path.unlink()


def test_extract_pdf_empty_file_reports_specific_message():
    path = _write_temp(".pdf", b"")
    try:
        with pytest.raises(DocumentIngestError, match="could not read this PDF"):
            extract_text(path)
    finally:
        path.unlink()


def test_extract_pdf_garbage_bytes_reports_specific_message():
    path = _write_temp(".pdf", b"not a pdf at all, just garbage bytes 1234567890")
    try:
        with pytest.raises(DocumentIngestError, match="could not read this PDF"):
            extract_text(path)
    finally:
        path.unlink()


def test_extract_pdf_password_protected_reports_specific_message():
    path = _write_temp(".pdf", _encrypted_pdf_bytes())
    try:
        with pytest.raises(DocumentIngestError, match="password-protected"):
            extract_text(path)
    finally:
        path.unlink()


def test_extract_pdf_valid_file_still_works():
    """The regression this whole section must not cause: a real, valid
    PDF must still extract its real text exactly as before."""
    path = _write_temp(".pdf", _real_pdf_bytes("A specific sentence to look for."))
    try:
        text = extract_text(path)
        assert "A specific sentence to look for." in text
    finally:
        path.unlink()


def test_extract_docx_legacy_doc_reports_specific_message():
    path = _write_temp(".docx", _LEGACY_DOC_BYTES)
    try:
        with pytest.raises(DocumentIngestError, match="legacy Word 97-2003 .doc"):
            extract_text(path)
    finally:
        path.unlink()


def test_extract_docx_garbage_bytes_reports_specific_message():
    path = _write_temp(".docx", b"not a docx at all, just garbage bytes")
    try:
        with pytest.raises(DocumentIngestError, match="not a valid .docx file"):
            extract_text(path)
    finally:
        path.unlink()


def test_extract_docx_valid_zip_but_not_a_docx_package_reports_specific_message():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "not a docx")
    path = _write_temp(".docx", buf.getvalue())
    try:
        with pytest.raises(DocumentIngestError, match="not a valid Word document"):
            extract_text(path)
    finally:
        path.unlink()


def test_ingest_document_min_extracted_text_threshold_is_exclusive_at_the_boundary():
    """Exactly _MIN_EXTRACTED_TEXT_CHARS clears the guard; one character
    short of it does not -- pinning the boundary so a future edit to the
    comparison operator (< vs <=) gets caught."""
    at_threshold = "x" * _MIN_EXTRACTED_TEXT_CHARS
    below_threshold = "x" * (_MIN_EXTRACTED_TEXT_CHARS - 1)

    with patch("app.importers.document.extract_text", return_value=at_threshold):
        ingest_document(  # must not raise
            _fake_docx(), product_key="rocketcyber", ai_provider=_StubAIProvider()
        )

    with patch("app.importers.document.extract_text", return_value=below_threshold):
        with pytest.raises(DocumentIngestError, match="extracted only"):
            ingest_document(
                _fake_docx(), product_key="rocketcyber", ai_provider=_StubAIProvider()
            )


def test_ingest_document_rejects_near_empty_extracted_text_and_names_the_file():
    """A "successful" extraction yielding almost nothing (the scanned-
    image-PDF symptom) must be rejected before ever reaching the AI
    provider -- proven here with a provider that fails the test if
    called at all."""
    path = _write_temp(".pdf", _blank_pdf_bytes())

    class _AssertNeverCalled(AIProvider):
        def complete(self, system, user, *, max_tokens=8192):
            raise AssertionError("must not reach the AI call for near-empty text")

    try:
        with pytest.raises(DocumentIngestError, match="extracted only 0 character"):
            ingest_document(
                path, product_key="scanned-test", ai_provider=_AssertNeverCalled()
            )
    finally:
        path.unlink()


def test_ingest_document_names_which_of_two_documents_is_near_empty():
    good = _write_temp(
        ".pdf",
        _real_pdf_bytes("A real baseline document with enough content to clear the threshold."),
    )
    empty = _write_temp(".pdf", _blank_pdf_bytes())

    class _AssertNeverCalled(AIProvider):
        def complete(self, system, user, *, max_tokens=8192):
            raise AssertionError("must not reach the AI call for near-empty text")

    try:
        with pytest.raises(DocumentIngestError) as exc_info:
            ingest_document(
                good, empty, product_key="two-doc-test", ai_provider=_AssertNeverCalled()
            )
        message = str(exc_info.value)
        assert empty.name in message
        assert good.name not in message
    finally:
        good.unlink()
        empty.unlink()


def test_ingest_document_two_real_documents_still_works():
    """A valid two-document ingest (baseline + CRM) must still work
    exactly as before -- real extraction this time, not the patched
    extract_text every other pipeline test in this file uses."""
    baseline = _write_temp(
        ".pdf",
        _real_pdf_bytes("MSP baseline document with enough real content to clear the threshold."),
    )
    crm = _write_temp(
        ".pdf",
        _real_pdf_bytes("Vendor CRM document with enough real content to clear the threshold."),
    )
    try:
        entry = ingest_document(
            baseline, crm, product_key="rocketcyber", ai_provider=_StubAIProvider()
        )
        assert entry.product.key == "rocketcyber"
        assert entry.product.source_docs == [baseline.name, crm.name]
        assert len(entry.controls) == 3
    finally:
        baseline.unlink()
        crm.unlink()


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


# Long enough to clear _MIN_EXTRACTED_TEXT_CHARS (the extraction-hardening
# slice's own guard against an effectively-empty document) so these tests
# keep exercising the AI-response-handling logic they're actually about.
_STUB_TEXT = (
    "Stub extracted document text, long enough to clear the minimum-"
    "extracted-text guard so these tests exercise what they're actually "
    "testing."
)


def test_ingest_pipeline_au_provider_satisfies():
    fake_doc = _fake_docx()
    with patch("app.importers.document.extract_text", return_value=_STUB_TEXT):
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
    with patch("app.importers.document.extract_text", return_value=_STUB_TEXT):
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
    with patch("app.importers.document.extract_text", return_value=_STUB_TEXT):
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
    with patch("app.importers.document.extract_text", return_value=_STUB_TEXT):
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


# ---------------------------------------------------------------------------
# coverage_basis: AI-ingested entries never get one -- a reviewer must set
# it explicitly (see baseline_import.validate()). Round-trips through
# to_yaml_dict()/load_baseline() when a human has set it.
# ---------------------------------------------------------------------------


def test_ingest_pipeline_never_sets_coverage_basis():
    """The AI pipeline must leave coverage_basis unset on every entry --
    baseline_import.validate() is what forces a human to fill it in before
    apply, per the accepted design decision that nothing in a vendor
    document reliably distinguishes platform-only from customer-system
    coverage without a human who knows the deployment.
    """
    fake_doc = _fake_docx()
    with patch("app.importers.document.extract_text", return_value=_STUB_TEXT):
        entry = ingest_document(
            fake_doc,
            product_key="rocketcyber",
            ai_provider=_StubAIProvider(),
        )
    assert all(c.coverage_basis is None for c in entry.controls)


def test_control_entry_coverage_basis_round_trips_through_yaml_dict():
    entry = _BaselineEntry(
        product=ProductMeta(
            key="x", name="X", provider="X", category="ESP",
            asset_type="SPA", framework="NIST 800-171 Rev 2 / CMMC L2",
            role="role",
        ),
        controls=[
            ControlEntry(
                control="AC.L2-3.1.1",
                classification=Classification.SHARED,
                candidate_state=CandidateState.PENDING_EVIDENCE,
                coverage_basis="platform_only",
            )
        ],
    )
    d = to_yaml_dict(entry)
    assert d["controls"][0]["coverage_basis"] == "platform_only"


def test_control_entry_coverage_basis_omitted_when_unset():
    entry = _BaselineEntry(
        product=ProductMeta(
            key="x", name="X", provider="X", category="ESP",
            asset_type="SPA", framework="NIST 800-171 Rev 2 / CMMC L2",
            role="role",
        ),
        controls=[
            ControlEntry(
                control="AC.L2-3.1.1",
                classification=Classification.SHARED,
                candidate_state=CandidateState.PENDING_EVIDENCE,
            )
        ],
    )
    d = to_yaml_dict(entry)
    assert "coverage_basis" not in d["controls"][0]


# ---------------------------------------------------------------------------
# Permanent AI-provenance fields on ProductMeta round-trip
# ---------------------------------------------------------------------------


def test_product_meta_ai_provenance_round_trips_through_yaml_dict():
    entry = _BaselineEntry(
        product=ProductMeta(
            key="x", name="X", provider="X", category="ESP",
            asset_type="SPA", framework="NIST 800-171 Rev 2 / CMMC L2",
            role="role",
            ai_generated_at="2026-09-13T00:00:00+00:00",
            ai_generated_model="anthropic:claude-sonnet-4-6",
        ),
        controls=[],
    )
    d = to_yaml_dict(entry)
    assert d["product"]["ai_generated_at"] == "2026-09-13T00:00:00+00:00"
    assert d["product"]["ai_generated_model"] == "anthropic:claude-sonnet-4-6"


def test_product_meta_ai_provenance_omitted_when_unset():
    entry = _BaselineEntry(
        product=ProductMeta(
            key="x", name="X", provider="X", category="ESP",
            asset_type="SPA", framework="NIST 800-171 Rev 2 / CMMC L2",
            role="role",
        ),
        controls=[],
    )
    d = to_yaml_dict(entry)
    assert "ai_generated_at" not in d["product"]
    assert "ai_generated_model" not in d["product"]


# ---------------------------------------------------------------------------
# Size guard, max_tokens override, and error normalization
# ---------------------------------------------------------------------------


def test_ingest_document_rejects_oversized_input():
    fake_doc = _fake_docx()
    huge = "x" * (_MAX_INPUT_CHARS + 1)
    with patch("app.importers.document.extract_text", return_value=huge):
        with pytest.raises(DocumentIngestError, match="exceeds the"):
            ingest_document(
                fake_doc,
                product_key="rocketcyber",
                ai_provider=_StubAIProvider(),
            )


def test_ingest_document_passes_higher_max_tokens():
    captured: dict = {}

    class _RecordingProvider(AIProvider):
        def complete(self, system, user, *, max_tokens=8192):
            captured["max_tokens"] = max_tokens
            return _STUB_AI_RESPONSE

    fake_doc = _fake_docx()
    with patch("app.importers.document.extract_text", return_value=_STUB_TEXT):
        ingest_document(
            fake_doc, product_key="rocketcyber", ai_provider=_RecordingProvider()
        )
    assert captured["max_tokens"] > 8192


def test_ingest_document_wraps_null_provider_error():
    """ai_provider='none' must degrade cleanly through this pipeline too --
    a single DocumentIngestError, not a raw RuntimeError leaking a different
    shape to callers than every other failure mode in this module.
    """
    fake_doc = _fake_docx()
    with patch("app.importers.document.extract_text", return_value=_STUB_TEXT):
        with pytest.raises(DocumentIngestError, match="No AI provider configured"):
            ingest_document(
                fake_doc, product_key="rocketcyber", ai_provider=NullProvider()
            )


def test_ingest_document_wraps_malformed_json():
    class _BadJSONProvider(AIProvider):
        def complete(self, system, user, *, max_tokens=8192):
            return "{not valid json"

    fake_doc = _fake_docx()
    with patch("app.importers.document.extract_text", return_value=_STUB_TEXT):
        with pytest.raises(DocumentIngestError, match="isn't valid JSON"):
            ingest_document(
                fake_doc, product_key="rocketcyber", ai_provider=_BadJSONProvider()
            )


def test_ingest_document_wraps_wrong_shape_json():
    class _WrongShapeProvider(AIProvider):
        def complete(self, system, user, *, max_tokens=8192):
            return json.dumps({"controls": []})  # missing "product"

    fake_doc = _fake_docx()
    with patch("app.importers.document.extract_text", return_value=_STUB_TEXT):
        with pytest.raises(DocumentIngestError, match="expected shape"):
            ingest_document(
                fake_doc, product_key="rocketcyber", ai_provider=_WrongShapeProvider()
            )
