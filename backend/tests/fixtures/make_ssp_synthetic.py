"""Writes ssp_synthetic.docx: an SSP in the redacted sample's shape, invented.

Run from backend/: ``python tests/fixtures/make_ssp_synthetic.py``. The
.docx is committed; test_ssp_docx.py reads it, and checks that this
script still writes the same rows, so an edit here without a re-run
fails.

Nothing in it comes from a customer's document. The shape is the
sample's (section heading, family heading, the five columns); every
statement, note and name is written here, and each row is planted for
one case, so the file says what it tests:

  1-2  exact; near-duplicate statements under AC.L2-3.1.1, which is the
       sample's shape and fine (nothing detects it, by decision)
  3    "including" where 800-171A says "(and other systems)": unmatched
  4    "Privileged accounts are identified." under Access Control
  5    an SI objective's text under Access Control: family mismatch
  6    text in no catalog, "-" in every other cell
  7    a text two AU objectives share: ambiguous
  8    row 4's text again, under Identification and Authentication
"""

from __future__ import annotations

import sys
from pathlib import Path

from docx import Document

HEADER = ("Control", "Status", "Notes", "Implementation Statement", "RACI")
_RACI = "R: Sam Placeholder <sam@example.invalid>; A: Lee Placeholder <lee@example.invalid>"
_S1 = ("Accounts are created in the identity provider by the IT lead after a "
       "signed access request, and reviewed each quarter.")

SECTIONS: tuple[tuple[str, tuple[tuple[str, str, str, str, str], ...]], ...] = (
    ("8.1 Access Control", (
        ("Authorized users are identified.", "Met", "-", _S1, _RACI),
        ("Processes acting on behalf of authorized users are identified.", "Met", "-",
         _S1.replace("access request", "access-request"), _RACI),
        ("Devices including other systems authorized to connect to the system are identified.",
         "Met", "Inventory reconciled at each quarterly review.",
         _S1.replace("Accounts are", "Devices are"), _RACI),
        ("Privileged accounts are identified.", "Met", "-",
         "Administrator accounts are separate accounts, listed in the access register.", _RACI),
        ("The time within which to identify system flaws is specified.", "Met", "-",
         "Vulnerability scan findings are triaged within five business days.", _RACI),
        ("Visitors sign the front-desk log.", "Not Met", "-", "-", "-"),
    )),
    ("8.3 Audit and Accountability", (
        ("Audit records, once created, contain the defined content.", "Met", "-",
         "The log platform's schema is fixed and checked at onboarding.", _RACI),
    )),
    ("8.5 Identification and Authentication", (
        ("Privileged accounts are identified.", "Met", "-",
         "Privileged identities carry an admin- prefix in the directory.", _RACI),
    )),
)


def build(path: Path) -> Path:
    doc = Document()
    doc.add_paragraph("8. NIST SP 800-171 Rev 2 Security Controls")
    for heading, rows in SECTIONS:
        doc.add_paragraph(heading)
        table = doc.add_table(rows=1, cols=len(HEADER))
        for cell, text in zip(table.rows[0].cells, HEADER, strict=True):
            cell.text = text
        for row in rows:
            for cell, text in zip(table.add_row().cells, row, strict=True):
                cell.text = text
    doc.save(str(path))
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("ssp_synthetic.docx")
    print(build(out))
