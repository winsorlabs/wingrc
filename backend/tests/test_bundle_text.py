"""A person's plain text keeps its paragraphs and line breaks in the bundle.

Each field here is typed into a textarea and stored as plain text. Until
2026-11-12 the bundle escaped each into one <p>, so HTML collapsed every
newline and a three-paragraph implementation statement read as one run-on
paragraph in the assessor's SSP, the PDF included (it shares these
bodies). `bundle_service._text_html` now draws a blank line as a new
paragraph and a single newline as a line break. No database.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from app import bundle_service as b

STATUS_TAG = "<span class='s-tag'>approved</span>"


def _snapshot(
    stmt: str | None = "One paragraph.",
    system_description: str | None = None,
    boundary: str | None = None,
    cui_flow: str | None = None,
    finding: str | None = None,
    note: str | None = None,
) -> b.BundleSnapshot:
    obj = b.ObjectiveSnap(
        objective_key="AC.L2-3.1.1[a]",
        objective_text="authorized users are identified.",
        status="met",
        responsibility="shared",
        stmt_body=stmt,
        stmt_status="approved",
    )
    ctrl = b.ControlSnap(
        control_id="AC.L2-3.1.1",
        family="Access Control",
        title="Authorized Access Control",
        requirement_text="Limit system access to authorized users.",
        sprs_weight=5,
        rollup_status="met",
        objectives=[obj],
    )
    return b.BundleSnapshot(
        generated_at=datetime(2026, 11, 12, tzinfo=UTC),
        sprs_score=110,
        org=b.OrgSnap(*(["Example Org"] + [None] * 14)),
        sys_desc=b.SysDescSnap(
            system_name="Enclave",
            system_type="enclave",
            operational_status="operational",
            system_description=system_description,
            cui_categories=[],
            cui_storage_locations=[],
            authorization_boundary_description=boundary,
            external_connections=[],
            cui_flow_description=cui_flow,
        ),
        assessment=b.AssessmentSnap(uuid.UUID(int=1), "A", "self", "in_progress", None),
        contacts=[],
        controls=[ctrl],
        evidence_files={},
        evidence_hashes={},
        unavailable_ev_ids=set(),
        unlinked_evidence=[],
        open_tasks=[],
        findings=[b.FindingSnap("Stale accounts", "low", finding, "open")] if finding else [],
        sprs_submissions=(
            [b.SprsSubmissionSnap(88, "2026-11-01", "Pat", note, False)] if note else []
        ),
    )


def _statement_html(stmt: str) -> str:
    page = b._implementation_body(_snapshot(stmt=stmt))
    start = page.index("<p class=\"txt\">")
    return page[start : page.index(STATUS_TAG) + len(STATUS_TAG) + len("</p>")]


def test_a_statement_s_paragraphs_are_paragraphs_with_its_tag_in_the_last():
    assert _statement_html("First paragraph.\n\nSecond paragraph.") == (
        '<p class="txt">First paragraph.</p>'
        f'<p class="txt">Second paragraph.{STATUS_TAG}</p>'
    )


def test_a_line_break_is_a_line_break_not_a_paragraph():
    assert _statement_html("Accounts require:\nmultifactor sign-in.") == (
        f'<p class="txt">Accounts require:<br>multifactor sign-in.{STATUS_TAG}</p>'
    )


def test_the_importer_s_bulleted_body_keeps_its_shape():
    # What the SSP importer's cell reader stores for a bulleted cell (#99),
    # written out.
    body = (
        "Accounts require:\n\n- multifactor sign-in;\n- a named owner.\n\nReviewed each quarter."
    )
    assert _statement_html(body) == (
        '<p class="txt">Accounts require:</p>'
        '<p class="txt">- multifactor sign-in;<br>- a named owner.</p>'
        f'<p class="txt">Reviewed each quarter.{STATUS_TAG}</p>'
    )


def test_a_nested_item_keeps_its_indent():
    html = _statement_html("- an owner;\n  - named by HR.")
    assert "<br>  - named by HR." in html
    assert ".txt{white-space:pre-wrap}" in b._CSS, (
        "the indent is kept in the HTML but only white-space shows it"
    )


def test_windows_newlines_several_blank_lines_and_blank_lines_holding_spaces():
    assert _statement_html("One.\r\n\r\nTwo.\n\n\n\nThree.\n  \t\nFour.") == (
        '<p class="txt">One.</p><p class="txt">Two.</p><p class="txt">Three.</p>'
        f'<p class="txt">Four.{STATUS_TAG}</p>'
    )


def test_text_is_escaped_and_nothing_is_interpreted():
    html = _statement_html("<script>x</script> & **bold**")
    assert html == (
        f'<p class="txt">&lt;script&gt;x&lt;/script&gt; &amp; **bold**{STATUS_TAG}</p>'
    )


def test_a_statement_of_only_newlines_still_carries_its_tag():
    assert _statement_html("\n\n") == f'<p class="txt">{STATUS_TAG}</p>'


def test_the_system_description_s_three_narratives_keep_their_paragraphs():
    page = b._sys_desc_body(
        _snapshot(
            system_description="Purpose.\n\nUsers.",
            boundary="Tenant.\nLaptops.",
            cui_flow="In.\n\nOut.",
        )
    )
    assert '<p class="txt">Purpose.</p><p class="txt">Users.</p>' in page
    assert '<p class="txt">Tenant.<br>Laptops.</p>' in page
    assert '<p class="txt">In.</p><p class="txt">Out.</p>' in page


def test_an_empty_narrative_still_says_not_provided():
    page = b._sys_desc_body(_snapshot())
    assert page.count('<p class="txt">Not provided.</p>') == 3


def test_a_finding_s_description_keeps_its_paragraphs():
    page = b._render_outstanding(_snapshot(finding="Two accounts unused.\n\nBoth disabled."))
    assert (
        '<td><p class="txt">Two accounts unused.</p><p class="txt">Both disabled.</p></td>'
    ) in page


def test_a_sprs_filing_note_keeps_its_line_breaks():
    page = b._render_scoring(_snapshot(note="Filed late.\nCorrected 11-02."))
    assert '<td><p class="txt">Filed late.<br>Corrected 11-02.</p></td>' in page


def test_no_newline_survives_inside_a_rendered_paragraph():
    # A raw newline inside <p> is what HTML collapsed; pre-wrap would now
    # show it, so the renderer must have turned every one into markup.
    page = b._implementation_body(_snapshot(stmt="a\nb\n\nc\r\nd"))
    for para in re.findall(r'<p class="txt">(.*?)</p>', page, flags=re.S):
        assert "\n" not in para and "\r" not in para
