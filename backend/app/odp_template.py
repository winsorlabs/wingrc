"""The ODP list: NIST SP 800-171 Rev 3's organization-defined parameters,
as an MSP template a tenant adopts (docs/roadmap.md, "The ODP list").

The source is not NIST's. It is a DoD CIO memorandum, "Department of
Defense Organization-Defined Parameters for National Institute of Standards
and Technology Special Publication 800-171 Revision 3", signed by David W.
McKeown on 10 April 2025, which sets as DoD policy the value of each ODP
"in preparation to implement reference (a) as the minimum requirement for
contractors". So the DoD column is the value DoD will require, which is
the column's whole worth.

Four columns:

| Column | Source | Who writes it |
|---|---|---|
| ODP identifier | the memo's tables, verbatim | the source only |
| What this parameter sets | odp_glosses.yaml, AI-written | the source only |
| Our defined value | the organization | its editors |
| DoD value under Rev 3 | the memo's tables, verbatim | the source only |

The identifier and DoD value are never paraphrased: a DoD-mandated value
reworded is a changed requirement, and a reader acts on it. They come from
odp_dod_rev3.json, which scripts/odp/extract_odp.py writes from the
committed PDF and reproduces exactly. The three source columns are a
version's `reference_columns` (migration 0073), which no edit may change
(list_library.reference_change).

"Our defined value" ships empty. An empty cell is "not yet defined", which
is the true state of a new tenant, and is neither a template example
(`[PLACEHOLDER]`) nor knowably absent (`[PLACEHOLDER - reason]`). Filling
it with the DoD value would read as a parameter someone defined when
nobody did.

There is no mapping to Rev 2 practice numbers (Jarrod, 2026-11-05): Rev 2
leaves these values to the organization, so there is nothing to
reconcile, and the identifiers stay as the memo prints them.

Defining is not implementing: this list evidences that a value was
defined, never that it is enforced on any system. The list's own text
says so, wherever it is copied, exported or bundled.

Written to an org only by `wingrc lists-seed-odp`, dry-run by default,
with an audit row per write. A person's edit of the template is a
conflict the command leaves alone unless that exact edit is named, as
`lists-import` does.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import log_event
from .list_library import content_of, create_version
from .models import ListDefinition, ListVersion

SEEDS = Path(__file__).resolve().parent / "seeds"
FIXTURE = SEEDS / "odp_dod_rev3.json"
GLOSSES = SEEDS / "odp_glosses.yaml"

LIST_KEY = "odp-dod-rev3"
SOURCE_REF = "docs/reference/OrgDefinedParmsNISTSP800-171.pdf"
ID_COL = "ODP identifier"
GLOSS_COL = "What this parameter sets (AI-written)"
OURS_COL = "Our defined value"
DOD_COL = "DoD value under Rev 3 (reference only)"
COLUMNS = [ID_COL, GLOSS_COL, OURS_COL, DOD_COL]
REFERENCE_COLUMNS = [ID_COL, GLOSS_COL, DOD_COL]

# Read from the memo, page 1: the signature block's date, and the
# clearance stamp, which is an image on the page, read by eye (2026-11-05).
SIGNED_BY = (
    "David W. McKeown, Performing the Duties of the Deputy DoD CIO for Cybersecurity and DoD "
    "Chief Information Security Officer"
)
SIGNED_ON = "10 April 2025"
CLEARED = (
    "Cleared for open publication 15 April 2025, Department of Defense Office of "
    "Prepublication and Security Review"
)
# The memo's own words: "In four (4) instances the ODP has been defined as
# guidance versus a specified value." Each such value begins "Guidance:".
GUIDANCE_PREFIX = "Guidance:"
EXPECTED_GUIDANCE = 4
SUPERSCRIPT = str.maketrans(
    "0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹"
)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def load_glosses() -> dict[str, str]:
    return yaml.safe_load(GLOSSES.read_text(encoding="utf-8"))


def guidance_rows(fixture: dict) -> list[str]:
    return [r["identifier"] for r in fixture["rows"] if r["dod_value"].startswith(GUIDANCE_PREFIX)]


def description(fixture: dict) -> str:
    source = fixture["source"]
    guidance = ", ".join(guidance_rows(fixture))
    # The fixture keeps each footnote as printed, "1 Significant: ..."; its
    # number is written as the mark the values carry.
    footnotes = " ".join(n[0].translate(SUPERSCRIPT) + n[1:] for n in source["footnotes"])
    return (
        f"{OURS_COL} is the value in force for this organization. Fill it in for each "
        "parameter; an empty cell means the parameter is not yet defined.\n\n"
        "The DoD value column is reference only. It is copied verbatim from the DoD CIO memorandum "
        f"\"{source['title']}\", signed by {SIGNED_BY} on {SIGNED_ON}. {CLEARED}. "
        f"File {SOURCE_REF}, SHA-256 {source['sha256']}. These are the values the Department "
        "of Defense has set as policy for contractors under Rev 3; they are not NIST's, and "
        "they are not this organization's until it defines them as its own. In "
        f"{len(guidance_rows(fixture))} rows ({guidance}) the memo gives guidance instead of a "
        f"value; those begin \"{GUIDANCE_PREFIX}\".\n\n"
        "What this parameter sets was written by an AI model to explain each parameter. It may "
        "contain errors, omissions or misreadings. The memo's identifier and value are the "
        "authority.\n\n"
        "This list evidences that a value was defined. It is not evidence that the value is "
        "enforced on any system.\n\n"
        f"Footnotes in the memo: {footnotes}"
    )


def build_content() -> dict:
    fixture, glosses = load_fixture(), load_glosses()
    return {
        "title": "Organization-Defined Parameters (NIST SP 800-171 Rev 3)",
        "description": description(fixture),
        "responsible": "",
        "review_cadence": "",
        "columns": list(COLUMNS),
        "reference_columns": list(REFERENCE_COLUMNS),
        "rows": [
            [r["identifier"], glosses[r["identifier"]], "", r["dod_value"]]
            for r in fixture["rows"]
        ],
    }


def provenance(cause: str) -> dict:
    return {
        "via": "odp_seed",
        "cause": cause,
        "source_file": SOURCE_REF,
        "source_sha256": load_fixture()["source"]["sha256"],
        "fixture_sha256": sha256_of(FIXTURE),
        "glosses_sha256": sha256_of(GLOSSES),
    }


class SeedRefused(ValueError):
    """An apply named an edit to overwrite that is not the current one."""


@dataclass
class SeedPlan:
    action: str  # create | new_version | unchanged | conflict
    content: dict
    definition: ListDefinition | None = None
    current: ListVersion | None = None
    changed: list[str] = field(default_factory=list)

    @property
    def edit_ref(self) -> str | None:
        if self.action != "conflict" or self.current is None:
            return None
        return f"{LIST_KEY}@v{self.current.version_number}"


def plan(session: Session, org_id: uuid.UUID) -> SeedPlan:
    content = build_content()
    d = session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id, ListDefinition.list_key == LIST_KEY
        )
    )
    if d is None:
        return SeedPlan("create", content)
    current = session.get(ListVersion, d.current_version_id)
    have = content_of(current)
    changed = [k for k in content if have.get(k) != content[k]]
    if not changed:
        return SeedPlan("unchanged", content, d, current)
    if (current.provenance or {}).get("via") == "api":
        return SeedPlan("conflict", content, d, current, changed)
    return SeedPlan("new_version", content, d, current, changed)


def apply(
    session: Session, org_id: uuid.UUID, p: SeedPlan, overwrite_edit: str | None = None
) -> ListVersion | None:
    """Writes what `p` planned, with an audit row. A conflict is written
    only when `overwrite_edit` names that exact edit."""
    if overwrite_edit is not None and overwrite_edit != p.edit_ref:
        raise SeedRefused(
            f"{overwrite_edit} is not the edit this would replace "
            f"({p.edit_ref or 'there is none'}). Nothing was written."
        )
    if p.action == "unchanged" or (p.action == "conflict" and overwrite_edit is None):
        return None
    if p.action == "create":
        d = ListDefinition(
            org_id=org_id, list_key=LIST_KEY, is_template=True, source_ref=SOURCE_REF
        )
        session.add(d)
        session.flush()
        cause = "first_import"
    else:
        d = p.definition
        prior = (p.current.provenance or {}).get("source_sha256")
        same_source = prior == load_fixture()["source"]["sha256"]
        cause = "importer_changed" if same_source else "source_changed"
    before = p.current.version_number if p.current else None
    v = create_version(session, d, p.content, provenance(cause))
    log_event(
        session, org_id=org_id, action="list.odp_template_seed", entity_type="list_definition",
        entity_id=d.id,
        before_value={"version_number": before},
        after_value={"version_number": v.version_number, "row_count": len(v.rows)},
        context={
            "via": "cli", "list_key": LIST_KEY, "cause": cause, "changed_fields": p.changed,
            "replaces_edit": overwrite_edit, **{k: v.provenance[k] for k in (
                "source_file", "source_sha256", "fixture_sha256", "glosses_sha256",
            )},
        },
        actor="cli:lists-seed-odp", actor_type="system",
    )
    return v
