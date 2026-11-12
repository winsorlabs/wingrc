"""importers/ssp_docx.py: the SSP table parse and the match, no database.

The catalog is the real one, cmmc_l2.yaml's 320 objectives, so a match
here is a match against the 800-171A text WinGRC ships. Files are built
with python-docx in the shape of the redacted sample: a family heading,
then a table of Control, Status, Notes, Implementation Statement, RACI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from docx import Document

from app.importers import ssp_docx
from app.importers.ssp_docx import SspNotRecognised

_CATALOG_YAML = Path(__file__).resolve().parents[1] / "app" / "seeds" / "cmmc_l2.yaml"
_HEADER = ["Control", "Status", "Notes", "Implementation Statement", "RACI"]
_STATEMENT = "Users are provisioned in Entra ID by the IT administrator on HR's request."


@pytest.fixture(scope="module")
def catalog() -> list[ssp_docx.CatalogObjective]:
    data = yaml.safe_load(_CATALOG_YAML.read_text(encoding="utf-8"))
    return [
        ssp_docx.CatalogObjective(f"{c['id']}[{o['key']}]", f"{c['id']}[{o['key']}]",
                                  c["id"], c["family"], o["text"])
        for c in data["controls"] for o in c["objectives"]
    ]


def _ssp(path: Path, sections: list[tuple[str, list[list[str]]]], *, header=_HEADER) -> Path:
    doc = Document()
    doc.add_paragraph("8. NIST SP 800-171 Rev 2 Security Controls")
    for heading, rows in sections:
        doc.add_paragraph(heading)
        table = doc.add_table(rows=1, cols=len(header))
        for cell, text in zip(table.rows[0].cells, header, strict=True):
            cell.text = text
        for row in rows:
            cells = table.add_row().cells
            for cell, text in zip(cells, row, strict=True):
                cell.text = text
    doc.save(str(path))
    return path


def _row(control: str, statement: str = _STATEMENT, *, status="Met", notes="-",
         raci="Pat Doe <pat@example.com>") -> list[str]:
    return [control, status, notes, statement, raci]


def test_exact_matches_after_normalising_case_space_quotes_and_period(tmp_path, catalog):
    path = _ssp(tmp_path / "s.docx", [("8.1 Access Control", [
        _row("Authorized users are identified."),
        _row("  PROCESSES acting on behalf of   authorized users are identified"),
    ])])
    p = ssp_docx.preview(path, catalog)
    assert [(m.kind, m.objective.ref) for m in p.matches] == [
        ("exact", "AC.L2-3.1.1[a]"), ("exact", "AC.L2-3.1.1[b]"),
    ]


def test_the_briefs_wording_of_3_1_1_c_is_not_the_catalogs_and_goes_to_review(tmp_path, catalog):
    # The brief quotes the sample's [c] as "Devices including other systems
    # ..."; 800-171A, and the catalog, say "Devices (and other systems)".
    # Close is not equal: it is listed with its candidate, never accepted.
    path = _ssp(tmp_path / "s.docx", [("8.1 Access Control", [
        _row("Devices including other systems authorized to connect to the system are identified."),
    ])])
    [m] = ssp_docx.preview(path, catalog).matches
    assert m.kind == "unmatched"
    assert m.objective is None
    assert m.candidates[0][0] == "AC.L2-3.1.1[c]"


def test_a_row_under_the_wrong_family_heading_is_a_mismatch(tmp_path, catalog):
    # SI.L2-3.14.1[a]'s text, filed under Access Control.
    si_text = next(o.text for o in catalog if o.ref == "SI.L2-3.14.1[a]")
    path = _ssp(tmp_path / "s.docx", [("8.1 Access Control", [_row(si_text)])])
    [m] = ssp_docx.preview(path, catalog).matches
    assert m.kind == "family_mismatch"
    assert m.objective is None
    assert m.candidates == (("SI.L2-3.14.1[a]", 1.0),)


def test_the_family_heading_tells_apart_two_objectives_with_one_text(tmp_path, catalog):
    # "Privileged accounts are identified." is AC.L2-3.1.5[a] and IA.L2-3.5.3[a].
    path = _ssp(tmp_path / "s.docx", [
        ("8.1 Access Control", [_row("Privileged accounts are identified.")]),
        ("8.5 Identification and Authentication", [_row("Privileged accounts are identified.")]),
    ])
    assert [m.objective.ref for m in ssp_docx.preview(path, catalog).matches] == [
        "AC.L2-3.1.5[a]", "IA.L2-3.5.3[a]",
    ]


def test_one_text_twice_in_one_family_is_ambiguous_and_not_accepted(tmp_path, catalog):
    # AU.L2-3.3.1[d] and AU.L2-3.3.2[b] share their text.
    text = next(o.text for o in catalog if o.ref == "AU.L2-3.3.1[d]")
    path = _ssp(tmp_path / "s.docx", [("8.3 Audit and Accountability", [_row(text)])])
    [m] = ssp_docx.preview(path, catalog).matches
    assert m.kind == "ambiguous"
    assert {ref for ref, _ in m.candidates} == {"AU.L2-3.3.1[d]", "AU.L2-3.3.2[b]"}


def test_an_unmatched_row_is_listed_with_its_text_never_dropped(tmp_path, catalog):
    path = _ssp(tmp_path / "s.docx", [("8.1 Access Control", [
        _row("Authorized users are identified."),
        _row("Our firewall is very good."),
    ])])
    p = ssp_docx.preview(path, catalog)
    assert len(p.rows) == 2, "fixture must have both rows, or this test proves nothing"
    [m] = p.of("unmatched")
    assert m.row.control_text == "Our firewall is very good."
    assert m.row.number == 2


def test_a_table_before_any_family_heading_is_not_accepted(tmp_path, catalog):
    doc = Document()
    table = doc.add_table(rows=1, cols=5)
    for cell, text in zip(table.rows[0].cells, _HEADER, strict=True):
        cell.text = text
    for cell, text in zip(table.add_row().cells, _row("Authorized users are identified."),
                          strict=True):
        cell.text = text
    doc.save(str(tmp_path / "s.docx"))
    [m] = ssp_docx.preview(tmp_path / "s.docx", catalog).matches
    assert m.kind == "no_family"


def test_the_dash_marker_reads_as_empty_not_as_text(tmp_path, catalog):
    path = _ssp(tmp_path / "s.docx", [("8.1 Access Control", [
        _row("Authorized users are identified.", statement="-", notes="-", status="-", raci="-"),
    ])])
    [r] = ssp_docx.parse(path)
    assert (r.statement, r.notes, r.status, r.raci_present) == (None, None, None, False)


def test_repeated_statements_are_each_kept_word_for_word(tmp_path, catalog):
    # Repetition across objectives is expected and fine (Jarrod,
    # 2026-11-11): nothing groups, flags or collapses it.
    path = _ssp(tmp_path / "s.docx", [("8.1 Access Control", [
        _row("Authorized users are identified.", statement=_STATEMENT),
        _row("Processes acting on behalf of authorized users are identified.",
             statement=_STATEMENT.replace("IT administrator", "IT  administrator")),
        _row("Devices (and other systems) authorized to connect to the system are identified.",
             statement=_STATEMENT.replace("HR's", "HR’s")),
    ])])
    p = ssp_docx.preview(path, catalog)
    assert [m.kind for m in p.matches] == ["exact"] * 3
    assert [m.row.statement for m in p.matches] == [
        _STATEMENT,
        _STATEMENT.replace("IT administrator", "IT  administrator"),
        _STATEMENT.replace("HR's", "HR’s"),
    ]
    assert not hasattr(p, "duplicates")


def test_a_docx_without_the_table_is_refused_by_name(tmp_path, catalog):
    path = _ssp(tmp_path / "prose.docx", [("8.1 Access Control", [])],
                header=["Requirement", "Response", "Owner", "Evidence", "Notes"])
    with pytest.raises(SspNotRecognised, match=r"prose\.docx: no table with the columns"):
        ssp_docx.parse(path)


def test_a_file_that_is_not_a_docx_is_refused_by_name(tmp_path, catalog):
    path = tmp_path / "scanned.pdf"
    path.write_bytes(b"%PDF-1.7\n")
    with pytest.raises(SspNotRecognised, match=r"scanned\.pdf: not a \.docx"):
        ssp_docx.parse(path)


def test_the_same_file_twice_gives_the_same_result(tmp_path, catalog):
    path = _ssp(tmp_path / "s.docx", [("8.1 Access Control", [
        _row("Authorized users are identified."), _row("Our firewall is very good."),
    ])])
    assert ssp_docx.preview(path, catalog) == ssp_docx.preview(path, catalog)


def test_status_is_tallied_as_the_sources_claim(tmp_path, catalog):
    path = _ssp(tmp_path / "s.docx", [("8.1 Access Control", [
        _row("Authorized users are identified."),
        _row("Processes acting on behalf of authorized users are identified."),
        _row("Privileged accounts are identified.", status="Not Met"),
    ])])
    assert ssp_docx.status_tally(ssp_docx.parse(path)) == [("Met", 2), ("Not Met", 1)]


@pytest.mark.parametrize("heading, family", [
    ("8.1 Access Control", "AC"),
    ("3.14 System and Information Integrity", "SI"),
    ("Security Assessment (CA)", "CA"),
    ("Physical Protection", "PE"),
    ("8. NIST SP 800-171 Rev 2 Security Controls", None),
    ("Access Control (SI)", None),
    ("Access control is described below", None),
])
def test_family_headings(heading, family):
    assert ssp_docx._family_of(heading) == family


# The committed synthetic SSP (tests/fixtures/make_ssp_synthetic.py). The
# real sample is a customer's document and stays out of the repo; this one
# is invented, with one planted case per row, written out here.
_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_the_synthetic_ssp_reads_as_planted(catalog):
    p = ssp_docx.preview(_FIXTURES / "ssp_synthetic.docx", catalog)
    assert [(m.row.number, m.kind, m.objective.ref if m.objective else None)
            for m in p.matches] == [
        (1, "exact", "AC.L2-3.1.1[a]"),
        (2, "exact", "AC.L2-3.1.1[b]"),
        (3, "unmatched", None),           # "including", not "(and other systems)"
        (4, "exact", "AC.L2-3.1.5[a]"),
        (5, "family_mismatch", None),     # SI's text under Access Control
        (6, "unmatched", None),           # in no catalog
        (7, "ambiguous", None),           # AU.L2-3.3.1[d] = AU.L2-3.3.2[b]
        (8, "exact", "IA.L2-3.5.3[a]"),   # row 4's text, told apart by family
    ]
    assert p.matches[2].candidates[0][0] == "AC.L2-3.1.1[c]"
    assert p.matches[4].candidates == (("SI.L2-3.14.1[a]", 1.0),)
    # Rows 1 and 2: a near-duplicate pair, kept to document the shape.
    assert p.rows[1].statement == p.rows[0].statement.replace("access request", "access-request")
    row6 = p.rows[5]
    assert (row6.notes, row6.statement, row6.raci_present) == (None, None, False)
    assert p.rows[2].notes == "Inventory reconciled at each quarterly review."
    assert ssp_docx.status_tally(p.rows) == [("Met", 7), ("Not Met", 1)]


def test_the_committed_synthetic_ssp_is_what_its_script_writes(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_ssp_synthetic",
                                                  _FIXTURES / "make_ssp_synthetic.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fresh = module.build(tmp_path / "fresh.docx")
    assert ssp_docx.parse(_FIXTURES / "ssp_synthetic.docx") == ssp_docx.parse(fresh), (
        "re-run tests/fixtures/make_ssp_synthetic.py and commit the .docx")


# Cell fidelity: what a statement body holds, exactly. The text in a cell
# must arrive on its objective as written; text that reads almost right
# is the case review would never catch.
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _statement_cell(tmp_path: Path, fill) -> tuple[Path, object]:
    """One row whose statement cell `fill(cell)` writes; returns the file
    and that cell as python-docx saw it before saving."""
    doc = Document()
    doc.add_paragraph("8.1 Access Control")
    table = doc.add_table(rows=1, cols=5)
    for cell, text in zip(table.rows[0].cells, _HEADER, strict=True):
        cell.text = text
    cells = table.add_row().cells
    for cell, text in zip(cells, _row("Authorized users are identified."), strict=True):
        cell.text = text
    cells[3].text = ""
    fill(cells[3])
    path = tmp_path / "s.docx"
    doc.save(str(path))
    return path, cells[3]


def _body(path: Path) -> str:
    [row] = ssp_docx.parse(path)
    return row.statement


def test_a_two_paragraph_cell_keeps_its_paragraph_break(tmp_path):
    def fill(cell):
        cell.paragraphs[0].add_run("Accounts are requested by ticket.")
        cell.add_paragraph("The IT lead approves each request.")
    path, _ = _statement_cell(tmp_path, fill)
    assert _body(path) == "Accounts are requested by ticket.\n\nThe IT lead approves each request."


def test_a_line_break_inside_a_paragraph_is_one_newline(tmp_path):
    def fill(cell):
        run = cell.paragraphs[0].add_run("Owner: IT lead")
        run.add_break()
        run.add_text("Backup: office manager")
    path, _ = _statement_cell(tmp_path, fill)
    assert _body(path) == "Owner: IT lead\nBackup: office manager"


def test_a_bulleted_cell_keeps_its_bullets(tmp_path):
    def fill(cell):
        cell.paragraphs[0].add_run("Accounts require:")
        cell.add_paragraph("multifactor sign-in;", style="List Bullet")
        cell.add_paragraph("a named owner.", style="List Bullet")
        cell.add_paragraph("Reviewed each quarter.")
    path, saw = _statement_cell(tmp_path, fill)
    assert "- " not in saw.text, "python-docx must drop the bullets, or this test proves nothing"
    assert _body(path) == (
        "Accounts require:\n\n- multifactor sign-in;\n- a named owner.\n\nReviewed each quarter."
    )


def test_a_numbered_cell_keeps_its_numbers(tmp_path):
    def fill(cell):
        cell.paragraphs[0].add_run("Request the account.")
        cell.paragraphs[0].style = "List Number"
        cell.add_paragraph("Approve it.", style="List Number")
    path, _ = _statement_cell(tmp_path, fill)
    assert _body(path) == "1. Request the account.\n2. Approve it."


def test_a_very_long_statement_arrives_whole(tmp_path):
    long = " ".join(f"Sentence {i} names control step {i * 7 % 13}." for i in range(2000))
    path, _ = _statement_cell(tmp_path, lambda cell: cell.paragraphs[0].add_run(long))
    body = _body(path)
    assert len(long) > 50_000, "fixture must be long, or this test proves nothing"
    assert body == long


def test_curly_quotes_and_dashes_are_kept_as_written(tmp_path):
    text = ("The “owner” approves — always; it’s required "
            "(2024–2025), and spacing  is  the source’s.")
    path, _ = _statement_cell(tmp_path, lambda cell: cell.paragraphs[0].add_run(text))
    assert _body(path) == text


def test_text_inside_a_content_control_is_not_dropped(tmp_path):
    from docx.oxml import parse_xml

    def fill(cell):
        p = cell.paragraphs[0]
        p.add_run("Approved by ")
        p._p.append(parse_xml(
            f'<w:sdt xmlns:w="{_W[1:-1]}"><w:sdtContent>'
            '<w:r><w:t>the IT lead</w:t></w:r></w:sdtContent></w:sdt>'))
        p.add_run(" in writing.")
    path, saw = _statement_cell(tmp_path, fill)
    assert "IT lead" not in saw.text, "python-docx must drop it, or this test proves nothing"
    assert _body(path) == "Approved by the IT lead in writing."


@pytest.mark.parametrize("case, found", [
    ("nested table", "a nested table"),
    ("tracked insertion", "unaccepted tracked changes"),
    ("merged cell", "a merged cell"),
    ("roman numbering", "list numbering in the format 'lowerRoman'"),
])
def test_a_cell_it_cannot_read_faithfully_refuses_the_file(tmp_path, case, found):
    from docx.oxml import parse_xml

    def fill(cell):
        if case == "nested table":
            cell.paragraphs[0].add_run("See:")
            cell.add_table(rows=1, cols=1)
        elif case == "tracked insertion":
            cell.paragraphs[0]._p.append(parse_xml(
                f'<w:ins xmlns:w="{_W[1:-1]}" w:id="1" w:author="x">'
                '<w:r><w:t>added text</w:t></w:r></w:ins>'))
        elif case == "merged cell":
            cell.paragraphs[0].add_run("Spans two columns.")
            cell.merge(_next_cell(cell))
        elif case == "roman numbering":
            cell.paragraphs[0].add_run("First.")
            cell.paragraphs[0].style = "List Number"
            numbering = cell.part.numbering_part.element
            for fmt in numbering.iter(_W + "numFmt"):
                if fmt.get(_W + "val") == "decimal":
                    fmt.set(_W + "val", "lowerRoman")
    path, _ = _statement_cell(tmp_path, fill)
    with pytest.raises(SspNotRecognised, match=rf"s\.docx: .*{re.escape(found)}"):
        ssp_docx.parse(path)


def _next_cell(cell):
    tc = cell._tc
    return type(cell)(tc.getnext(), cell._parent)
