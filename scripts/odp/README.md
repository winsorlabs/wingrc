# ODP extraction

Writes `backend/app/seeds/odp_dod_rev3.json` from the DoD CIO memorandum
"Department of Defense Organization-Defined Parameters for National
Institute of Standards and Technology Special Publication 800-171 Revision
3" (`docs/reference/OrgDefinedParmsNISTSP800-171.pdf`, indexed in
`docs/reference/README.md`). The ODP list template is built from that file
(`backend/app/odp_template.py`). Not app runtime code: re-run it only when
DoD revises the memo, which it says it will "as necessary".

## Running it

```
python -m venv .venv-odp
.venv-odp/bin/pip install pdfplumber==0.11.7     # not an app dependency
.venv-odp/bin/python scripts/odp/extract_odp.py docs/reference/OrgDefinedParmsNISTSP800-171.pdf
.venv-odp/bin/python scripts/odp/extract_odp.py docs/reference/OrgDefinedParmsNISTSP800-171.pdf --check
```

`--check` exits 0 only if re-extracting the committed PDF reproduces the
committed JSON byte for byte and every check below passes. A revised memo
is a new file beside the old one (`docs/reference/README.md`'s rules),
then a run against it, then the diff of the JSON read before committing.

## What it checks

- Every identifier is of the form `03.xx.xx`, `03.xx.xx.a` or
  `03.xx.xx.a.01`, appears once, and has a value.
- Every identifier the requirement text names, as `(03.xx.xx...)` after
  an `[Assignment: ...]` or `[Selection ...]` marker, has a table row, and
  every row has such a marker.
- Each row's assignment text equals the requirement text's marker. That
  checks the line joining against a second copy of the same words. The
  memo differs from itself in two places, each checked by eye against the
  page and listed in `KNOWN_DIFFERENCES`:
  - 03.01.08.b: the table quotes only the assignment nested inside the
    requirement's selection;
  - 03.12.05.a: the table prints "nondisclosure", the requirement text
    "non-disclosure".

  **The table's wording is kept, and the committed data says so**
  (2026-11-06). Each of the two rows carries `source_discrepancy`: what
  the requirement text reads, `"kept": "table"`, and what differs. A
  reader comparing either row to the PDF finds the difference already
  noticed, rather than taking it for an extraction error. Only the
  assignment text differs; neither row's DoD value does.

## What it found (2026-11-05)

- **80 ODPs**, from 51 tables, one per page, on pages 3 to 53. The tables
  hold 81 physical rows: 03.03.01.a's value runs over a page break, and
  the second half is a row with no identifier, appended to the first.
  The 80 match the 80 identifiers in the requirement text exactly.
- **Four are guidance, not a value**, as the memo says ("In four (4)
  instances..."): 03.01.20.b, 03.04.06.b, 03.13.10 and 03.16.01. Each
  value begins "Guidance:".
- **Two footnotes**, marked in the values as a superscript (¹
  "Significant", ² "External Service Providers"). They are kept in the
  JSON's `source.footnotes`, verbatim.
- **The clearance** ("Cleared for open publication, Apr 15, 2025") is a
  stamp image on page 1, not text, so it was read by eye and is recorded
  in `odp_template.py`, not extracted.

The docstring of `extract_odp.py` states every change it makes to the
text: line joins, collapsed spaces, list items on their own lines, and
footnote marks as superscripts. Nothing else is changed.
`backend/tests/test_odp_list.py` holds ten values to strings typed from
the rendered pages.
