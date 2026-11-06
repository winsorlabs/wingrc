# Reference documents

This folder holds external published documents, kept exactly as their
publisher released them. Data in WinGRC that is derived from one of them
cites it here. Examples are SPRS weights, objective text, assessment
guidance and scoring rules.

CLAUDE.md's hard rule says reference data is verified against
authoritative sources, not recalled or taken from summaries. This folder
is where those sources live, so that a claim like "this weight comes from
the DoD Assessment Methodology" can be checked against the document
itself, at the version it was taken from.

## The rules

- **Verbatim.** A file here is the exact file the publisher published,
  as downloaded. It is not re-saved, printed to PDF, converted, OCR'd,
  trimmed or reformatted. Its SHA-256 must match the one recorded in the
  index below.
- **Never edited.** Not to fix a typo, add a note or highlight a passage.
  Commentary goes in the derived data or in `docs/roadmap.md`, never in
  the document.
- **Never replaced.** A new revision is added as a new file beside the
  old one. The old file stays, because data derived from it still cites
  it, and that citation must keep pointing at what was actually read.
- **Cited by what is derived from it.** Data taken from a document names
  it by file name and SHA-256, plus the section, table or page it came
  from. `backend/app/seeds/cmmc_official_guidance.yaml`'s header is the
  pattern: title, version, date, source URL and hash.
- **Indexed.** Every file has a row in the index below, added in the
  same commit as the file.

## What belongs here

Published documents that WinGRC's reference data is derived from. For
example:
- NIST SP 800-171 Rev 2 and SP 800-171A;
- the CMMC Assessment Guide Level 2;
- 32 CFR Part 170;
- the DoD Assessment Methodology.

## What does not

- **Anything the publisher's terms do not let us redistribute.** This
  repository is public. US Government works are public domain. For a
  copyrighted document, such as most vendor material, record its title,
  version, URL, retrieval date and SHA-256 in the index, and leave the
  file out.
- **A customer's or tenant's documents.** These are not published
  documents, and they never belong in the repository.
- **Our own writing about a document.** Summaries, extracts and notes go
  elsewhere. Only the publisher's file goes here.

A vendor document, such as a product's customer responsibility matrix,
can be recorded here when a baseline is derived from it. It is not
an authority, though: CLAUDE.md's "Never auto-credit a vendor CRM"
applies to what is read from it.

## Adding a document

1. Download it from the publisher's own site, not a mirror. Some
   government sites refuse scripted downloads, so use a browser if
   needed (`scripts/cmmc_guidance/README.md` describes the DoD CIO case).
2. Record its SHA-256 (`sha256sum <file>`, or
   `Get-FileHash -Algorithm SHA256 <file>` in PowerShell).
3. Commit it under a name that says what it is and which version, such
   as `nist-sp-800-171a-rev2.pdf`, with its row in the index.
4. Cite it from the data derived from it, by file name and hash.

## Index

| File | Title | Publisher | Version / date | Source URL | Retrieved | SHA-256 |
|---|---|---|---|---|---|---|
| `OrgDefinedParmsNISTSP800-171.pdf` | Department of Defense Organization-Defined Parameters for National Institute of Standards and Technology Special Publication 800-171 Revision 3 | DoD CIO; memorandum signed by David W. McKeown, Performing the Duties of the Deputy DoD CIO for Cybersecurity | Signed 10 April 2025; cleared for open publication 15 April 2025 (DoD Office of Prepublication and Security Review, stamp on page 1); 53 pages | Not recorded: supplied by Jarrod, committed 2026-11-05 | 2026-11-05 | `fd83ecf4e0998f0b29d5e6f222e7364ce67eb6fdedb5ac4f34ec4310ba495916` |

Data derived from the ODP memo: `backend/app/seeds/odp_dod_rev3.json`
(by `scripts/odp/extract_odp.py`), which the ODP list template cites
(`backend/app/odp_template.py`).
