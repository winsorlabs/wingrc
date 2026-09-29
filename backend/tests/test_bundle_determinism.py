"""Bundle export determinism.

**The lesson this file exists to encode: a passing test that depends on
unspecified ordering is not a passing test.**

`test_lifecycle.py`'s three-export comparison is the assertion underneath
baseline versioning, multi-tool coverage, the acceptance record and the SSP
inventory -- every one of those slices was verified against it. It was
passing on row-order luck: the bundle's contributors query had no
`ORDER BY`, so two exports of identical data could render
`Tools: Datto RMM, RocketCyber...` one time and
`Tools: RocketCyber..., Datto RMM` the next. A green suite meant "the rows
happened to come back in the same order twice", which is a different claim
from "the export is reproducible", and it hid a real defect for weeks.

So this file does three things the lifecycle walk cannot:

1. **Ties on purpose.** The fixture gives several contacts the same name,
   several evidence rows the same `collected_at`, one contact several
   documentation roles, one control state several RACI holders of the same
   letter, and one objective several contributing products. Two tools is a
   weak test for ordering; rows that genuinely tie are a real one.
2. **Perturbs between exports.** Repetition alone is a weak lever, because
   a seq scan over an unchanged table tends to return the same order all
   day. What actually moves physical row order is writing: an UPDATE
   rewrites tuples and can reorder a heap scan. So the strongest check here
   exports, writes something unrelated, and exports again.
3. **Guards the audit structurally**, so the next person who adds an
   unordered query to a render path gets a red run on their first attempt
   rather than their fifth.
"""

from __future__ import annotations

import hashlib
import io
import pathlib
import re
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.bundle_service import render_bundle, snapshot_bundle
from app.engine import recompute_sprs
from app.models import (
    Assessment,
    AssessmentObjective,
    AuditLog,
    BaselineControl,
    Contact,
    ContactDocumentationRole,
    Control,
    ControlState,
    ControlStateContributor,
    Evidence,
    EvidenceStateLink,
    Framework,
    Organization,
    Product,
    ProductBaselineVersion,
    RaciAssignment,
)
from app.storage import NullStorageClient

pytestmark = pytest.mark.integration

_SECTIONS = ("ssp/02_implementation.html", "evidence/manifest.html",
             "ssp/03_personnel.html", "ssp/05_customer_responsibility_matrix.html")


def _section_hashes(session, org_id, assessment_id) -> dict[str, str]:
    """Hash the rendered sections, not the whole ZIP: `generated_at` is
    stamped per export by design, so the ZIP legitimately differs every
    time. The sections are the content the point-in-time promise is about.
    """
    snap = snapshot_bundle(
        session, storage=NullStorageClient(), org_id=org_id, assessment_id=assessment_id
    )
    zip_bytes, _, _, _ = render_bundle(snap)
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    out = {}
    for suffix in _SECTIONS:
        name = next((n for n in zf.namelist() if n.endswith(suffix)), None)
        if name is None:
            continue
        body = re.sub(rb"generated [^<]*", b"", zf.read(name))
        out[suffix] = hashlib.sha256(body).hexdigest()
    return out


@pytest.fixture
def tied_fixture(db_session):
    """An assessment whose every rendered collection contains ties."""
    org = Organization(name=f"TieOrg-{uuid.uuid4().hex[:8]}")
    fw = Framework(key=f"fw-tie-{uuid.uuid4().hex[:6]}", name="Tie FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC", title="Access Control",
        requirement_text="Limit access.", sprs_weight=5, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    objectives = [
        AssessmentObjective(control_id=ctrl.id, objective_key=k, text=f"Objective {k}.")
        for k in ("a", "b", "c")
    ]
    db_session.add_all(objectives)
    db_session.flush()

    assessment = Assessment(
        org_id=org.id, framework_id=fw.id, name="Tie Assessment", status="in_progress"
    )
    db_session.add(assessment)
    db_session.flush()

    states = [
        ControlState(
            assessment_id=assessment.id, org_id=org.id, objective_id=o.id, status="not_met"
        )
        for o in objectives
    ]
    db_session.add_all(states)
    db_session.flush()

    # TIE 1: four contacts, two pairs sharing a name.
    contacts = []
    for name, email in [
        ("Alex Morgan", "alex1@example.com"),
        ("Alex Morgan", "alex2@example.com"),
        ("Sam Rivers", "sam1@example.com"),
        ("Sam Rivers", "sam2@example.com"),
    ]:
        c = Contact(org_id=org.id, name=name, email=email, affiliation="customer")
        contacts.append(c)
    db_session.add_all(contacts)
    db_session.flush()

    # TIE 2: one contact holding several documentation roles.
    db_session.add_all(
        [
            ContactDocumentationRole(contact_id=contacts[0].id, role=r)
            for r in ("it_admin", "security_officer", "system_owner")
        ]
    )

    # TIE 3: RACI -- several holders of the SAME letter on one control state.
    db_session.add_all(
        [
            RaciAssignment(control_state_id=states[0].id, contact_id=c.id, raci_letter="R")
            for c in contacts
        ]
    )

    # TIE 4: evidence rows sharing an identical collected_at, as a real task
    # collect produces (several rows written in one transaction).
    same_instant = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
    evidences = []
    for i in range(5):
        e = Evidence(
            org_id=org.id, kind="reference", title=f"Shared evidence {i}",
            artifact_type="document", reference_location=f"DOC-{i}",
            collected_at=same_instant,
        )
        evidences.append(e)
    db_session.add_all(evidences)
    db_session.flush()
    for e in evidences:
        db_session.add(EvidenceStateLink(evidence_id=e.id, control_state_id=states[0].id))

    # TIE 5: several products contributing to the same control state.
    products = []
    for key, pname in [("prod-a", "Alpha Tool"), ("prod-b", "Beta Tool"), ("prod-c", "Gamma Tool")]:
        p = Product(
            framework_id=fw.id, key=f"{key}-{uuid.uuid4().hex[:4]}", name=pname,
            provider="Vendor", category="EDR", asset_type="SPA", role="Coverage.",
            is_published=True,
        )
        products.append(p)
    db_session.add_all(products)
    db_session.flush()
    for p in products:
        v = ProductBaselineVersion(product_id=p.id, version_number=1)
        db_session.add(v)
        db_session.flush()
        p.current_version_id = v.id
        bc = BaselineControl(
            product_id=p.id, baseline_version_id=v.id, control_id=ctrl.id,
            objectives=["a"], classification="shared", coverage_basis="customer_system",
            candidate_state="pending_evidence",
        )
        db_session.add(bc)
        db_session.flush()
        db_session.add(
            ControlStateContributor(
                control_state_id=states[0].id, product_id=p.id, baseline_control_id=bc.id
            )
        )

    db_session.commit()
    return {"org": org, "assessment": assessment, "states": states, "evidences": evidences}


def test_repeated_exports_are_byte_identical(tied_fixture, db_session):
    """Five exports of unchanged data, all identical.

    Five rather than two: two is what caught the original defect and it
    caught it only intermittently. Five is cheap here and makes a
    single-flip escape unlikely; the perturbation test below is the one
    that actually forces order to move.
    """
    org_id = tied_fixture["org"].id
    aid = tied_fixture["assessment"].id
    first = _section_hashes(db_session, org_id, aid)
    assert first, "no sections rendered -- the fixture produced an empty bundle"
    for run in range(2, 6):
        assert _section_hashes(db_session, org_id, aid) == first, (
            f"export {run} differs from export 1 over unchanged data"
        )


def test_export_is_unchanged_by_an_unrelated_write(tied_fixture, db_session):
    """Export, write something unrelated, export again.

    This is the lever that matters. An UPDATE rewrites tuples and can
    change the order a heap scan returns them in, which is exactly how the
    original defect surfaced -- between two exports separated by other
    work, not between two back-to-back ones.
    """
    org_id = tied_fixture["org"].id
    aid = tied_fixture["assessment"].id
    before = _section_hashes(db_session, org_id, aid)

    # Rewrite in place every table whose ordering this slice fixed: same
    # values, new tuple versions. An UPDATE writes a new row version and
    # leaves the old one dead, which is what moves a heap scan's output
    # order -- perturbing only one table would leave the other orderings
    # untested.
    for stmt in (
        "UPDATE evidence SET title = title WHERE org_id = :o",
        "UPDATE contact SET name = name WHERE org_id = :o",
        "UPDATE raci_assignment SET raci_letter = raci_letter WHERE control_state_id IN "
        "(SELECT id FROM control_state WHERE org_id = :o)",
        "UPDATE contact_documentation_role SET role = role WHERE contact_id IN "
        "(SELECT id FROM contact WHERE org_id = :o)",
        "UPDATE control_state_contributor SET product_id = product_id WHERE "
        "control_state_id IN (SELECT id FROM control_state WHERE org_id = :o)",
    ):
        db_session.execute(text(stmt), {"o": str(org_id)})
    # An audit row and an SPRS recompute: both real things that happen
    # between two exports in production.
    db_session.add(
        AuditLog(
            org_id=org_id, actor="system", actor_type="system", action="test.perturb",
            entity_type="organization", entity_id=org_id,
        )
    )
    recompute_sprs(db_session, aid)
    db_session.commit()

    assert _section_hashes(db_session, org_id, aid) == before, (
        "an unrelated write changed what the bundle renders -- a collection "
        "reaching the output is not totally ordered"
    )


def test_ties_really_exist_in_the_fixture(tied_fixture, db_session):
    """Guards the guard: if the fixture stopped producing ties, the tests
    above would pass vacuously and prove nothing."""
    org_id = tied_fixture["org"].id
    dup_names = db_session.execute(
        text(
            "SELECT count(*) FROM (SELECT name FROM contact WHERE org_id = :o "
            "GROUP BY name HAVING count(*) > 1) t"
        ),
        {"o": str(org_id)},
    ).scalar()
    assert dup_names >= 2, "fixture should contain contacts with duplicate names"

    tied_evidence = db_session.execute(
        text(
            "SELECT count(*) FROM (SELECT collected_at FROM evidence WHERE org_id = :o "
            "GROUP BY collected_at HAVING count(*) > 1) t"
        ),
        {"o": str(org_id)},
    ).scalar()
    assert tied_evidence >= 1, "fixture should contain evidence sharing collected_at"

    raci = db_session.scalars(
        select(RaciAssignment).where(
            RaciAssignment.control_state_id == tied_fixture["states"][0].id
        )
    ).all()
    assert len({r.raci_letter for r in raci}) == 1 and len(raci) >= 3, (
        "fixture should contain several RACI holders of the same letter"
    )


# ---------------------------------------------------------------------------
# Structural guard on the audit itself
# ---------------------------------------------------------------------------

_APP = pathlib.Path(__file__).resolve().parent.parent / "app"

# `bundle_service.py` IS the snapshot builder: every query in it feeds the
# ZIP and the SSP PDF, so it is scanned wholesale.
_SNAPSHOT_MODULE = "bundle_service.py"

# Elsewhere only specific functions reach an artifact, and scanning those
# modules wholesale would flag ordinary API queries that render nothing.
# A guard that cries wolf gets silenced rather than fixed, so these are
# named individually:
#   review_cycles.close_cycle -> _render_attestation_html, which is
#     uploaded to storage and recorded as Evidence.
#   repo.list_entities        -> render.py's .xlsx list exports.
_RENDER_PATH_FUNCTIONS = (
    ("review_cycles.py", "def close_cycle"),
    ("repo.py", "def list_entities"),
)


def _select_blocks(src: str):
    for m in re.finditer(r"(?:session|db)\.(?:execute|scalars)\(", src):
        start = m.start()
        i, depth = m.end() - 1, 0
        while i < len(src):
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        yield src[start : i + 1], src[:start].count("\n") + 1


def test_every_snapshot_query_is_ordered_or_explicitly_exempt():
    """The next person to add an unordered query to the snapshot builder
    fails here, on their first run.

    Exemption is deliberate and must be written down: put
    `DETERMINISM-EXEMPT: <reason>` in or near the query. The three that
    carry it today are a single-row lookup guaranteed by a UNIQUE
    constraint, a query whose consumer sorts, and an UPDATE.
    """
    src = (_APP / _SNAPSHOT_MODULE).read_text(encoding="utf-8")
    lines = src.split("\n")
    offenders: list[str] = []
    for block, ln in _select_blocks(src):
        if "select(" not in block and "update(" not in block:
            continue
        if "order_by" in block or "DETERMINISM-EXEMPT" in block:
            continue
        # The marker may sit just above the query or just below it, since a
        # reason sometimes reads better next to the consumer.
        nearby = "\n".join(lines[max(0, ln - 11) : ln + 10])
        if "DETERMINISM-EXEMPT" in nearby:
            continue
        offenders.append(f"{_SNAPSHOT_MODULE}:{ln}: {lines[ln - 1].strip()[:70]}")

    assert not offenders, (
        "Queries in the snapshot builder must be totally ordered, or carry a "
        "DETERMINISM-EXEMPT comment saying why order cannot affect output. An "
        "unordered collection reaching a rendered artifact makes that artifact "
        "nondeterministic -- which is how the contributors bug hid behind green "
        "runs for weeks. Offenders:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("module,func", _RENDER_PATH_FUNCTIONS)
def test_named_render_path_function_orders_its_queries(module, func):
    """The artifact-producing functions outside the snapshot builder.

    Scoped to the function rather than the module on purpose: most queries
    in these files serve API responses and never reach a stored artifact.
    """
    src = (_APP / module).read_text(encoding="utf-8")
    start = src.index(func)
    nxt = src.find("\ndef ", start + 1)
    body = src[start : nxt if nxt != -1 else len(src)]
    selects = body.count("select(")
    ordered = body.count("order_by") + body.count("DETERMINISM-EXEMPT")
    assert selects, f"{module}:{func} has no queries -- has it been refactored?"
    assert ordered >= selects, (
        f"{module}:{func} renders a stored artifact, so every collection it "
        f"reads must be totally ordered: found {selects} select(s) but only "
        f"{ordered} order_by/exemption(s). Counting rather than parsing "
        f"because these build their statements in steps; it is crude, but it "
        f"cannot be satisfied by an unrelated ordering elsewhere in the file."
    )
