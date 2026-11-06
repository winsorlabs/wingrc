"""The ODP list (docs/roadmap.md, "The ODP list"; odp_template.py).

Columns 1 and 4 are the DoD memo's words, verbatim. The fixture is what
scripts/odp/extract_odp.py wrote from the committed PDF; these tests hold
it to the page in ten places, each string typed from the rendered page and
not read from the fixture, so a dropped line in a multi-line cell fails
here rather than reading as a plausible, wrong requirement.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import list_adoption, list_evidence, list_library, odp_template
from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import AuditLog, ListDefinition, ListVersion, Organization
from tests.conftest import _app_session, _authed, _grant, _make_fake_user

PDF = Path(__file__).resolve().parents[2] / odp_template.SOURCE_REF
ONE = "/orgs/{org}/list-definitions/{list}"

# Typed from the rendered pages (2026-11-05), one per row, the longest and
# the shortest among them. Superscripts are the memo's footnote marks.
FROM_THE_PAGE = {
    "03.01.01.f.02": "at most 90 days",
    "03.01.01.h.02": "the work period ends, for privileged users at a minimum",
    "03.01.05.b.01": (
        "at a minimum and if applicable: establishing system accounts and assigning privileges, "
        "configuring access authorizations, configuring settings for events to be audited, "
        "establishing vulnerability scanning parameters, establishing intrusion detection "
        "parameters, and managing audit information"
    ),
    "03.01.08.b": (
        "[Selection (one or more): lock the account or node for an at least 15-minute time "
        "period; lock the account or node until released by an administrator and notify a "
        "system administrator]"
    ),
    "03.03.01.b": (
        "at least every 12 months and after any significant¹ incidents or "
        "significant¹ changes to risks"
    ),
    "03.05.07.f": (
        "1) Must have a minimum length of 16 characters.\n"
        "2) Contains a string of characters that does not include the user’s account name "
        "or full name."
    ),
    "03.12.05.a": "requirements as described in the contract",
    "03.12.05.c": "at least every 12 months",
    "03.16.01": (
        "Guidance: At a minimum, documentation that provides user and administrator guidance "
        "for the implementation and operation of controls. The level of detail required in such "
        "documentation should be based on the degree to which organizations depend on the "
        "capabilities, functions, or mechanisms to meet risk response expectations. Requirements "
        "can include mandated configuration settings that specify allowed functions, ports, "
        "protocols, and services. Acceptance criteria for systems, system components, and system "
        "services are defined in the same manner as the criteria for any organizational "
        "acquisition or procurement."
    ),
    "03.16.03.a": (
        "1. For cloud service providers:\n"
        "(i) FedRAMP Authorized at the FedRAMP Moderate (or higher) baseline in accordance with "
        "the FedRAMP Marketplace; or\n"
        "(ii) meets security requirements established by the government equivalent to the "
        "FedRAMP Moderate (or higher) baseline.\n"
        "2. All other external service providers² must meet NIST SP 800-171 R2."
    ),
}


def _rows() -> dict[str, dict]:
    return {r["identifier"]: r for r in odp_template.load_fixture()["rows"]}


def test_columns_1_and_4_match_the_page_in_ten_places():
    rows = _rows()
    for ident, value in FROM_THE_PAGE.items():
        assert rows[ident]["dod_value"] == value, ident


def test_the_memo_has_80_odps_once_each():
    ids = [r["identifier"] for r in odp_template.load_fixture()["rows"]]
    assert len(ids) == 80
    assert len(set(ids)) == 80


def test_four_odps_are_guidance_not_a_value_as_the_memo_says():
    # The memo, page 1: "In four (4) instances the ODP has been defined as
    # guidance versus a specified value." Named here, not derived.
    assert odp_template.guidance_rows(odp_template.load_fixture()) == [
        "03.01.20.b", "03.04.06.b", "03.13.10", "03.16.01",
    ]


def test_the_two_places_the_memo_differs_from_itself_are_in_the_data():
    # Checked by eye on pages 6 and 40; the table's wording is kept, and the
    # row says so for whoever compares it to the PDF next. Named, not derived.
    rows = {r["identifier"]: r for r in odp_template.load_fixture()["rows"]}
    noted = sorted(i for i, r in rows.items() if "source_discrepancy" in r)
    assert noted == ["03.01.08.b", "03.12.05.a"]
    assert all(rows[i]["source_discrepancy"]["kept"] == "table" for i in noted)
    assert "nondisclosure" in rows["03.12.05.a"]["assignment_text"]
    assert "non-disclosure" in rows["03.12.05.a"]["source_discrepancy"]["requirement_text_reads"]
    assert rows["03.01.08.b"]["assignment_text"] == "[Assignment: organization-defined time period]"
    assert rows["03.01.08.b"]["source_discrepancy"]["requirement_text_reads"].startswith(
        "[Selection (one or more): lock the account or node for an [Assignment:"
    )


def test_the_fixture_names_the_committed_pdf():
    if not PDF.exists():
        pytest.skip("the PDF is not in this checkout (an image build has no docs/)")
    source = odp_template.load_fixture()["source"]
    assert hashlib.sha256(PDF.read_bytes()).hexdigest() == source["sha256"]


def test_every_identifier_has_one_gloss():
    glosses = odp_template.load_glosses()
    assert sorted(glosses) == sorted(_rows())
    assert all(isinstance(g, str) and g.strip() for g in glosses.values())


def test_the_list_carries_the_memo_verbatim_and_ships_our_value_empty():
    c, rows = odp_template.build_content(), _rows()
    assert c["columns"] == [
        "ODP identifier", "What this parameter sets (AI-written)",
        "Our defined value", "DoD value under Rev 3 (reference only)",
    ]
    assert c["reference_columns"] == [
        "ODP identifier", "What this parameter sets (AI-written)",
        "DoD value under Rev 3 (reference only)",
    ]
    assert [r[0] for r in c["rows"]] == list(rows)
    assert [r[3] for r in c["rows"]] == [rows[i]["dod_value"] for i in rows]
    # Empty is "not yet defined": not the DoD value, not a placeholder.
    assert {r[2] for r in c["rows"]} == {""}


def test_the_list_says_what_it_is_and_what_it_is_not():
    d = odp_template.build_content()["description"]
    assert "AI model" in d and "may contain errors" in d
    assert "not NIST's" in d
    assert "10 April 2025" in d and "15 April 2025" in d
    assert "It is not evidence that the value is enforced on any system." in d
    assert "03.01.20.b, 03.04.06.b, 03.13.10, 03.16.01" in d


def test_an_odp_list_is_populated_only_when_every_value_is_defined():
    # It was a finding (2026-11-05): with no fill-in token anywhere, an
    # unfilled copy read as populated. The DoD value is reference text and
    # is nobody's entry, so a row with only that has nothing entered.
    c = odp_template.build_content()
    cols, refs, rows = c["columns"], c["reference_columns"], c["rows"]
    ours = cols.index(odp_template.OURS_COL)

    def with_ours(values):
        return [r[:ours] + [v] + r[ours + 1:] for r, v in zip(rows, values, strict=True)]

    assert not list_library.is_populated(rows, cols, refs)
    assert list_library.unentered_rows(rows, cols, refs) == 80
    one = with_ours(["60 days"] + [""] * 79)
    assert not list_library.is_populated(one, cols, refs)
    assert list_library.unentered_rows(one, cols, refs) == 79
    assert list_library.is_populated(with_ours(["60 days"] * 80), cols, refs)
    # A stated absence is an entry; a fill-in token and whitespace are not.
    assert list_library.is_populated(
        with_ours(["[PLACEHOLDER - not applicable here]"] + ["60 days"] * 79), cols, refs
    )
    assert not list_library.is_populated(with_ours(["[DATE]"] + ["60 days"] * 79), cols, refs)
    assert not list_library.is_populated(with_ours(["  "] + ["60 days"] * 79), cols, refs)
    # Read without its reference columns, the same rows look filled: the
    # columns argument is what makes the rule right, so it must be passed.
    assert list_library.is_populated(rows), (
        "the DoD text must count as entered when no column is named reference, "
        "or this test proves nothing about the reference columns"
    )


def test_an_unfilled_odp_copy_says_why_it_is_not_offered():
    c = odp_template.build_content()
    v = SimpleNamespace(
        rows=c["rows"], columns=c["columns"], reference_columns=c["reference_columns"]
    )
    d = SimpleNamespace(is_template=False)
    assert list_evidence.not_offered_reason(d, v) == (
        "nothing entered in Our defined value yet (80 of 80 rows)"
    )


def _base(columns, rows, refs):
    return SimpleNamespace(columns=columns, rows=rows, reference_columns=refs)


def test_an_edit_cannot_change_a_reference_cell():
    cols = ["ODP identifier", "Gloss", "Ours", "DoD"]
    refs = ["ODP identifier", "Gloss", "DoD"]
    base = _base(cols, [["03.01.01.f.02", "g", "", "at most 90 days"]], refs)
    ours = [["03.01.01.f.02", "g", "60 days", "at most 90 days"]]
    assert list_library.reference_change(base, cols, ours) is None
    for rows in (
        [["03.01.01.f.02", "g", "", "90 days"]],
        [["03.01.01.f.03", "g", "", "at most 90 days"]],
        [["03.01.01.f.02", "gloss", "", "at most 90 days"]],
    ):
        assert "cannot be changed" in list_library.reference_change(base, cols, rows)
    assert "added or removed" in list_library.reference_change(base, cols, [])
    wider = [["a", "b", "c", "d", ""]]
    assert "columns" in list_library.reference_change(base, [*cols, "Note"], wider)
    # Every other list: nothing is reference.
    assert list_library.reference_change(_base(cols, [], []), cols, [["x", "y", "z", "w"]]) is None


def test_a_newer_template_brings_dod_values_and_keeps_ours_by_identifier():
    cols = ["ODP identifier", "Gloss", "Ours", "DoD"]
    refs = ["ODP identifier", "Gloss", "DoD"]
    mine = {"columns": cols, "reference_columns": refs, "rows": [
        ["03.01", "g1", "60 days", "at most 90 days"],
        ["03.02", "g2", "", "24 hours"],
        ["03.03", "g3", "weekly", "monthly"],
    ]}
    theirs = {"columns": cols, "reference_columns": refs, "rows": [
        ["03.02", "g2", "", "12 hours"],
        ["03.01", "g1", "", "at most 60 days"],
        ["03.04", "g4", "", "new"],
    ]}
    rows, dropped = list_adoption.reference_rows(mine, theirs)
    assert rows == [
        ["03.02", "g2", "", "12 hours"],
        ["03.01", "g1", "60 days", "at most 60 days"],
        ["03.04", "g4", "", "new"],
    ]
    assert dropped == 1  # 03.03's "weekly": the template dropped that ODP


# --- through the database and the API -------------------------------------


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _org(db_session, name) -> uuid.UUID:
    org = Organization(name=f"{name}-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org.id


def _seeded(db_session):
    msp = _org(db_session, "MSP")
    p = odp_template.plan(db_session, msp)
    v = odp_template.apply(db_session, msp, p)
    db_session.flush()
    return msp, session_list(db_session, msp), v


def session_list(db_session, org_id) -> ListDefinition:
    return db_session.scalar(
        select(ListDefinition).where(
            ListDefinition.org_id == org_id, ListDefinition.list_key == odp_template.LIST_KEY
        )
    )


def _client(db_session, home, also=None) -> TestClient:
    user = _make_fake_user(
        role="msp_engineer", org_id=home, email=f"{uuid.uuid4().hex[:8]}@x.com",
        display_name="Ada Adopter",
    )
    _grant(db_session, user)
    for org, r in (also or {}).items():
        _grant(db_session, user, org_id=org, role=r)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


@pytest.mark.integration
def test_the_seed_writes_the_template_once_with_an_audit_row(db_session):
    msp, d, v = _seeded(db_session)
    assert d.is_template and d.source_ref == odp_template.SOURCE_REF
    assert v.version_number == 1 and v.provenance["cause"] == "first_import"
    assert list_library.content_of(v) == odp_template.build_content()
    rows = list(db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == msp, AuditLog.action == "list.odp_template_seed")
    ))
    assert len(rows) == 1 and rows[0].context["source_sha256"] == v.provenance["source_sha256"]
    again = odp_template.plan(db_session, msp)
    assert again.action == "unchanged"
    assert odp_template.apply(db_session, msp, again) is None


@pytest.mark.integration
def test_adopted_into_a_tenant_our_value_is_empty_and_only_it_can_be_edited(db_session):
    msp, t, _ = _seeded(db_session)
    tenant = _org(db_session, "Tenant")
    client = _client(db_session, tenant, also={msp: "msp_engineer"})
    r = client.post(
        f"/orgs/{tenant}/list-definitions/adopt",
        json={"source_org_id": str(msp), "template_ids": [str(t.id)], "apply": True},
    )
    assert r.status_code == 200, r.text
    copy_id = r.json()["items"][0]["list_id"]
    current = client.get(ONE.format(org=tenant, list=copy_id)).json()["current"]
    assert current["reference_columns"] == odp_template.REFERENCE_COLUMNS
    assert {row[2] for row in current["rows"]} == {""}

    filled = [list(row) for row in current["rows"]]
    filled[0][2] = "60 days"
    ok = client.post(
        f"{ONE.format(org=tenant, list=copy_id)}/versions",
        json={"base_version_id": current["id"], "rows": filled},
    )
    assert ok.status_code == 201, ok.text

    after = client.get(ONE.format(org=tenant, list=copy_id)).json()["current"]
    reworded = [list(row) for row in after["rows"]]
    reworded[0][3] = "90 days"
    refused = client.post(
        f"{ONE.format(org=tenant, list=copy_id)}/versions",
        json={"base_version_id": after["id"], "rows": reworded},
    )
    assert refused.status_code == 422
    assert "reference text" in refused.json()["detail"]
    assert db_session.get(ListVersion, uuid.UUID(after["id"])).rows[0][3] == "at most 90 days"


@pytest.mark.integration
def test_a_persons_edit_of_the_template_is_a_conflict_the_seed_leaves_alone(db_session):
    msp, t, v1 = _seeded(db_session)
    content = list_library.content_of(v1)
    content["rows"][0][2] = "someone filled the template"
    edit = list_library.create_version(db_session, t, content, {"via": "api", "cause": "edit"})
    p = odp_template.plan(db_session, msp)
    assert p.action == "conflict"
    assert p.edit_ref == f"{odp_template.LIST_KEY}@v{edit.version_number}"
    assert odp_template.apply(db_session, msp, p) is None
    with pytest.raises(odp_template.SeedRefused):
        odp_template.apply(db_session, msp, p, overwrite_edit="odp-dod-rev3@v99")
    v = odp_template.apply(db_session, msp, p, overwrite_edit=p.edit_ref)
    assert v.rows[0][2] == "" and v.provenance["cause"] == "importer_changed"
