"""List library L.7: an org adopts a template, and later takes a newer one.

Templates stay in the org that holds them; a copy lands in the adopting
org, records the template and version it came from, and does not move when
the template does. Taking a newer template version never overwrites a
person's work unless that exact edit is named.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import list_adoption, list_library
from app.auth import get_current_user
from app.db import get_session
from app.list_library import add_tag, history_of, tag_origins
from app.list_templates import plan_import
from app.main import app
from app.models import AuditLog, ListDefinition, ListVersion, Organization
from tests.conftest import _app_session, _authed, _grant, _make_fake_user
from tests.list_template_fixtures import build_library

LISTS = "/orgs/{org}/list-definitions"
ONE = "/orgs/{org}/list-definitions/{list}"

EXAMPLES = [
    ["[PLACEHOLDER]", "A. Visitor", "[PLACEHOLDER]"],
    ["2026-01-01", "[PLACEHOLDER]", "J. Escort"],
]


# ---------------------------------------------------------------------------
# The populated rule (PLAN section 5), stated once for L.4 to use
# ---------------------------------------------------------------------------


def test_a_row_is_an_example_while_it_carries_the_bare_placeholder():
    assert list_library.is_example_row(["[PLACEHOLDER]", "x"])
    assert list_library.is_example_row([" [PLACEHOLDER] "])
    # A filled document's knowably-absent value is a value, not an example.
    assert not list_library.is_example_row(["[PLACEHOLDER - no badge system]", "x"])
    assert not list_library.is_example_row(["see [PLACEHOLDER] note", "x"])


def test_populated_means_rows_and_none_of_them_an_example():
    assert not list_library.is_populated([])
    assert not list_library.is_populated(EXAMPLES)
    assert not list_library.is_populated([["2026-10-01", "A", "B"], EXAMPLES[0]])
    assert list_library.is_populated([["2026-10-01", "A", "B"]])


def test_a_copy_never_shares_its_templates_key():
    assert list_adoption.copy_key("3-10-3a-visitor-log") == "3-10-3a-visitor-log-own"
    assert len(list_adoption.copy_key("k" * 120)) <= 120


# ---------------------------------------------------------------------------
# Through the database and real HTTP (RLS enforced per request)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _org(db_session, name="Org") -> uuid.UUID:
    org = Organization(name=f"{name}-{uuid.uuid4().hex[:8]}")
    db_session.add(org)
    db_session.flush()
    return org.id


def _template(
    db_session, org_id, title="Visitor Log", columns=("Date", "Visitor", "Escort"),
    rows=None, tags=("PE.L2-3.10.3",),
) -> ListDefinition:
    d = ListDefinition(
        org_id=org_id, list_key=f"3-10-3a-{title.lower().replace(' ', '-')}", is_template=True,
        source_ref=f"PE/3.10.3/{title}.xlsx#3.10.3a",
    )
    db_session.add(d)
    db_session.flush()
    list_library.create_version(
        db_session, d,
        {
            "title": title, "description": "Everyone escorted through the CUI area.",
            "responsible": "MSP Engineer / Customer Security Officer",
            "review_cadence": "Monthly", "columns": list(columns),
            "rows": [list(r) for r in (EXAMPLES if rows is None else rows)],
        },
        {"source_file": f"PE/3.10.3/{title}.xlsx", "source_sha256": "0" * 64,
         "cause": "first_import"},
    )
    for k in tags:
        add_tag(db_session, d, k, "template_import", None)
    db_session.flush()
    return d


def _client(db_session, home, role="msp_engineer", also=None, name="Ada Adopter") -> TestClient:
    """A person with membership in `home` and, optionally, other orgs
    (`also`: {org_id: role})."""
    user = _make_fake_user(
        role=role, org_id=home, email=f"{uuid.uuid4().hex[:8]}@x.com", display_name=name
    )
    _grant(db_session, user)
    for org, r in (also or {}).items():
        _grant(db_session, user, org_id=org, role=r)
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, user)
    return TestClient(app)


def _adopt(client, dest, source, ids, apply=True):
    return client.post(
        f"{LISTS.format(org=dest)}/adopt",
        json={"source_org_id": str(source), "template_ids": [str(i) for i in ids], "apply": apply},
    )


def _audit(db_session, org_id, action):
    return list(
        db_session.scalars(
            select(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action == action)
        )
    )


def _versions(db_session, list_id) -> int:
    db_session.expire_all()
    return len(history_of(db_session, list_id))


@pytest.mark.integration
def test_adopting_across_orgs_copies_the_template_and_leaves_it_untouched(db_session):
    msp, tenant = _org(db_session, "MSP"), _org(db_session, "Tenant")
    t = _template(db_session, msp)
    client = _client(db_session, tenant, also={msp: "msp_engineer"})

    shown = _adopt(client, tenant, msp, [t.id], apply=False)
    assert shown.status_code == 200, shown.text
    item = shown.json()["items"][0]
    assert (item["action"], item["list_id"], item["example_rows"]) == ("create", None, 2)
    assert db_session.scalar(
        select(ListDefinition).where(ListDefinition.org_id == tenant)
    ) is None, "showing the set must write nothing"

    r = _adopt(client, tenant, msp, [t.id])
    assert r.status_code == 200, r.text
    copy_id = uuid.UUID(r.json()["items"][0]["list_id"])
    copy = db_session.get(ListDefinition, copy_id)
    assert (copy.org_id, copy.is_template, copy.template_list_id) == (tenant, False, t.id)
    assert copy.template_list_version_id == t.current_version_id
    assert copy.list_key == f"{t.list_key}-own"

    detail = client.get(ONE.format(org=tenant, list=copy_id)).json()
    assert detail["current"]["title"] == "Visitor Log"
    assert detail["current"]["rows"] == EXAMPLES
    assert detail["current"]["responsible"] == "MSP Engineer / Customer Security Officer"
    assert detail["current"]["cause"] == "adopted_from_template"
    p = detail["current"]["provenance"]
    assert (p["template_list_id"], p["template_version_id"], p["template_version_number"]) == (
        str(t.id), str(t.current_version_id), 1
    )
    assert p["saved_by"] == "Ada Adopter"
    assert detail["control_keys"] == ["PE.L2-3.10.3"]
    assert tag_origins(db_session, copy_id) == {"PE.L2-3.10.3": "template_adoption"}
    assert detail["adopted_from"] == {
        "template_list_id": str(t.id), "template_org_id": str(msp), "same_org": False,
        "template_title": "Visitor Log", "template_key": t.list_key,
        "based_on_version_number": 1, "head_version_number": 1, "update_available": False,
    }
    # Starts unfinished: its rows are the template's examples, so it is not
    # populated -- L.4 offers only populated lists as evidence.
    assert detail["current"]["example_rows"] == 2
    assert not list_library.is_populated(detail["current"]["rows"])

    assert _versions(db_session, t.id) == 1
    assert tag_origins(db_session, t.id) == {"PE.L2-3.10.3": "template_import"}
    [dest] = _audit(db_session, tenant, "list.adopt")
    [src] = _audit(db_session, msp, "list.template_adopted")
    assert dest.entity_id == copy_id and dest.context["template_list_id"] == str(t.id)
    assert src.entity_id == t.id and src.after_value["copy_list_id"] == str(copy_id)
    assert src.after_value["into_org_id"] == str(tenant)

    again = _adopt(client, tenant, msp, [t.id]).json()["items"][0]
    assert (again["action"], again["list_id"]) == ("already_adopted", str(copy_id))
    assert len(_audit(db_session, tenant, "list.adopt")) == 1


@pytest.mark.integration
def test_the_msp_sees_how_many_copies_sit_at_which_version_but_not_whose(db_session):
    msp, a, b = _org(db_session, "MSP"), _org(db_session, "A"), _org(db_session, "B")
    t = _template(db_session, msp)
    _adopt(_client(db_session, a, also={msp: "msp_admin"}), a, msp, [t.id])
    app.dependency_overrides.clear()
    _adopt(_client(db_session, b, also={msp: "msp_admin"}), b, msp, [t.id])
    app.dependency_overrides.clear()
    msp_only = _client(db_session, msp, role="msp_admin")
    detail = msp_only.get(ONE.format(org=msp, list=t.id)).json()
    assert detail["distribution"] == [{"version_number": 1, "copies": 2, "this_org": False}]
    assert detail["own_copy_id"] is None
    [summary] = [x for x in msp_only.get(LISTS.format(org=msp)).json() if x["id"] == str(t.id)]
    assert summary["adopted_copies"] == 2
    # Knowing there are copies is not reading them.
    assert msp_only.get(LISTS.format(org=a)).status_code == 403


@pytest.mark.integration
def test_an_msp_that_is_its_own_tenant_adopts_within_one_org(db_session):
    org = _org(db_session, "Winsorlabs")
    t = _template(db_session, org)
    client = _client(db_session, org, role="msp_admin")
    r = _adopt(client, org, org, [t.id])
    assert r.status_code == 200, r.text
    assert r.json()["same_org"] is True
    copy_id = r.json()["items"][0]["list_id"]

    listed = {x["id"]: x for x in client.get(LISTS.format(org=org)).json()}
    assert listed[str(t.id)]["is_template"] is True
    assert listed[copy_id]["is_template"] is False
    assert listed[copy_id]["adopted_from"]["same_org"] is True
    template = client.get(ONE.format(org=org, list=t.id)).json()
    assert template["own_copy_id"] == copy_id
    assert template["distribution"] == [{"version_number": 1, "copies": 1, "this_org": True}]
    assert len(_audit(db_session, org, "list.adopt")) == 1
    assert _audit(db_session, org, "list.template_adopted") == []


@pytest.mark.integration
def test_an_import_of_the_same_library_leaves_a_same_org_copy_alone(db_session, tmp_path):
    root = build_library(tmp_path)
    plan = plan_import(
        root / "New Lists", root / "Archive" / "Lists", root / "Changelog" / "Lists"
    )
    org = _org(db_session, "Winsorlabs")
    list_library.apply_import(db_session, org, plan)
    db_session.flush()
    t = db_session.scalars(
        select(ListDefinition).where(ListDefinition.org_id == org, ListDefinition.is_template)
        .order_by(ListDefinition.list_key)
    ).first()
    client = _client(db_session, org, role="msp_admin")
    copy_id = uuid.UUID(_adopt(client, org, org, [t.id]).json()["items"][0]["list_id"])
    diff = list_library.diff_import(db_session, org, plan)
    assert {a.action for a in diff.actions} == {"unchanged"}
    assert copy_id not in {a.definition.id for a in diff.actions}


@pytest.mark.integration
def test_adopting_across_orgs_needs_membership_in_both(db_session):
    msp, tenant = _org(db_session, "MSP"), _org(db_session, "Tenant")
    t = _template(db_session, msp)

    tenant_only = _client(db_session, tenant)
    r = _adopt(tenant_only, tenant, msp, [t.id])
    assert r.status_code == 403 and "not a member" in r.json()["detail"]
    assert tenant_only.get(LISTS.format(org=msp)).status_code == 403
    assert tenant_only.get(ONE.format(org=tenant, list=t.id)).status_code == 404
    app.dependency_overrides.clear()

    msp_only = _client(db_session, msp)
    assert _adopt(msp_only, tenant, msp, [t.id]).status_code == 403
    app.dependency_overrides.clear()

    # A role that may not define lists in the template's org does not
    # borrow the right from its role in this one.
    poc_at_msp = _client(db_session, tenant, also={msp: "customer_poc"})
    assert _adopt(poc_at_msp, tenant, msp, [t.id]).status_code == 403
    assert db_session.scalar(
        select(ListDefinition).where(ListDefinition.org_id == tenant)
    ) is None


@pytest.mark.integration
def test_a_list_that_is_not_a_template_is_not_adoptable(db_session):
    msp, tenant = _org(db_session, "MSP"), _org(db_session, "Tenant")
    t = _template(db_session, msp)
    own = _template(db_session, msp, title="Kept")
    own.is_template = False
    db_session.flush()
    client = _client(db_session, tenant, also={msp: "msp_engineer"})
    r = _adopt(client, tenant, msp, [t.id, own.id])
    assert r.status_code == 404, r.text
    assert db_session.scalar(
        select(ListDefinition).where(ListDefinition.org_id == tenant)
    ) is None, "one bad template refuses the whole set"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("msp_admin", True),
        ("consultant_admin", True),
        ("msp_engineer", True),
        ("customer_poc", False),
        ("c3pao_assessor", False),
    ],
)
def test_who_may_adopt(db_session, role, allowed):
    msp, tenant = _org(db_session, "MSP"), _org(db_session, "Tenant")
    t = _template(db_session, msp)
    client = _client(db_session, tenant, role=role, also={msp: "msp_engineer"})
    r = _adopt(client, tenant, msp, [t.id])
    assert r.status_code == (200 if allowed else 403), r.text


def _adopted(db_session):
    """An MSP template, a tenant copy of it, and a person in both orgs."""
    msp, tenant = _org(db_session, "MSP"), _org(db_session, "Tenant")
    t = _template(db_session, msp)
    client = _client(db_session, tenant, also={msp: "msp_engineer"}, name="Ada Adopter")
    copy_id = _adopt(client, tenant, msp, [t.id]).json()["items"][0]["list_id"]
    return msp, tenant, t, client, copy_id


def _edit_template(client, msp, t, columns, rows):
    current = client.get(ONE.format(org=msp, list=t.id)).json()["current"]
    r = client.post(
        f"{ONE.format(org=msp, list=t.id)}/versions",
        json={"base_version_id": current["id"], "columns": columns, "rows": rows},
    )
    assert r.status_code == 201, r.text


def _edit_copy(client, tenant, copy_id, rows, columns=None):
    base = ONE.format(org=tenant, list=copy_id)
    current = client.get(base).json()["current"]
    body = {"base_version_id": current["id"], "rows": rows}
    if columns is not None:
        body["columns"] = columns
    r = client.post(f"{base}/versions", json=body)
    assert r.status_code == 201, r.text


def _take(client, tenant, copy_id, apply=True, overwrite_edit=None):
    return client.post(
        f"{ONE.format(org=tenant, list=copy_id)}/take-template-version",
        json={"apply": apply, "overwrite_edit": overwrite_edit},
    )


NEW_EXAMPLES = [["[PLACEHOLDER]", "B. Visitor", "[PLACEHOLDER]", "[PLACEHOLDER]"]]
NEW_COLUMNS = ["Date", "Visitor", "Escort", "Badge"]


@pytest.mark.integration
def test_a_template_update_shows_as_available_and_does_nothing_until_taken(db_session):
    msp, tenant, t, client, copy_id = _adopted(db_session)
    _edit_template(client, msp, t, NEW_COLUMNS, NEW_EXAMPLES)

    detail = client.get(ONE.format(org=tenant, list=copy_id)).json()
    assert detail["adopted_from"]["based_on_version_number"] == 1
    assert detail["adopted_from"]["head_version_number"] == 2
    assert detail["adopted_from"]["update_available"] is True
    assert detail["current"]["rows"] == EXAMPLES, "editing the template never moves the copy"

    shown = _take(client, tenant, copy_id, apply=False)
    assert shown.status_code == 200, shown.text
    assert shown.json()["columns_added"] == ["Badge"]
    assert shown.json()["rows_from"] == "template"
    assert shown.json()["edit_ref"] is None
    assert _versions(db_session, uuid.UUID(copy_id)) == 1, "showing the change writes nothing"

    taken = _take(client, tenant, copy_id)
    assert taken.status_code == 200, taken.text
    assert taken.json()["version_number"] == 2
    v2 = client.get(ONE.format(org=tenant, list=copy_id)).json()
    assert v2["current"]["rows"] == NEW_EXAMPLES
    assert v2["current"]["cause"] == "adopted_from_template"
    p = v2["current"]["provenance"]
    assert (p["template_version_number"], p["previous_template_version_number"]) == (2, 1)
    assert v2["adopted_from"]["update_available"] is False
    assert _take(client, tenant, copy_id).status_code == 409
    assert len(_audit(db_session, tenant, "list.template_version_take")) == 1
    assert len(_audit(db_session, msp, "list.template_version_taken")) == 1


@pytest.mark.integration
def test_taking_an_update_carries_the_orgs_own_rows_into_the_new_columns(db_session):
    msp, tenant, t, client, copy_id = _adopted(db_session)
    filled = [["2026-10-01", "A. Visitor", "J. Escort"]]
    _edit_copy(client, tenant, copy_id, filled)
    _edit_template(client, msp, t, NEW_COLUMNS, NEW_EXAMPLES)

    shown = _take(client, tenant, copy_id, apply=False).json()
    assert (shown["rows_from"], shown["edit_ref"], shown["dropped_cells"]) == ("copy", None, 0)
    assert _take(client, tenant, copy_id).status_code == 200
    current = client.get(ONE.format(org=tenant, list=copy_id)).json()["current"]
    assert current["columns"] == NEW_COLUMNS
    assert current["rows"] == [["2026-10-01", "A. Visitor", "J. Escort", ""]]
    assert list_library.is_populated(current["rows"])


@pytest.mark.integration
def test_taking_an_update_over_a_persons_edit_needs_that_edit_named(db_session):
    msp, tenant, t, client, copy_id = _adopted(db_session)
    _edit_copy(
        client, tenant, copy_id, [["2026-10-01", "A. Visitor", "J. Escort"]],
        columns=["Date", "Visitor", "Host"],
    )
    _edit_template(client, msp, t, NEW_COLUMNS, NEW_EXAMPLES)
    copy = uuid.UUID(copy_id)
    ref = f"{t.list_key}-own@v2"

    shown = _take(client, tenant, copy_id, apply=False).json()
    assert shown["edit_ref"] == ref
    assert shown["person_changed"] == ["columns"]
    assert shown["dropped_cells"] == 1  # "J. Escort", under the renamed column

    refused = _take(client, tenant, copy_id)
    assert refused.status_code == 409 and ref in refused.json()["detail"]
    wrong = _take(client, tenant, copy_id, overwrite_edit=f"{t.list_key}-own@v1")
    assert wrong.status_code == 409
    assert _versions(db_session, copy) == 2, "a refusal writes nothing"

    taken = _take(client, tenant, copy_id, overwrite_edit=ref)
    assert taken.status_code == 200, taken.text
    v3 = client.get(ONE.format(org=tenant, list=copy_id)).json()["current"]
    assert v3["columns"] == NEW_COLUMNS
    assert v3["rows"] == [["2026-10-01", "A. Visitor", "", ""]]
    assert v3["provenance"]["replaces_edit_in_version"] == 2
    v2 = client.get(f"{ONE.format(org=tenant, list=copy_id)}/versions/2").json()
    assert v2["rows"] == [["2026-10-01", "A. Visitor", "J. Escort"]], "version 2 keeps every value"


@pytest.mark.integration
def test_a_template_dropping_a_filled_column_needs_the_edit_named(db_session):
    msp, tenant, t, client, copy_id = _adopted(db_session)
    _edit_copy(client, tenant, copy_id, [["2026-10-01", "A. Visitor", "J. Escort"]])
    _edit_template(client, msp, t, ["Date", "Visitor"], [["[PLACEHOLDER]", "[PLACEHOLDER]"]])
    shown = _take(client, tenant, copy_id, apply=False).json()
    assert (shown["person_changed"], shown["dropped_cells"]) == ([], 1)
    assert shown["edit_ref"] == f"{t.list_key}-own@v2"
    assert _take(client, tenant, copy_id).status_code == 409


@pytest.mark.integration
def test_taking_an_update_manages_the_templates_tags_and_never_a_persons(db_session):
    msp, tenant, t, client, copy_id = _adopted(db_session)
    copy = uuid.UUID(copy_id)
    base = ONE.format(org=tenant, list=copy_id)
    assert client.post(f"{base}/tags", json={"control_key": "3.10.4"}).status_code == 201
    tbase = ONE.format(org=msp, list=t.id)
    assert client.post(f"{tbase}/tags", json={"control_key": "3.10.5"}).status_code == 201
    assert client.delete(f"{tbase}/tags/PE.L2-3.10.3").status_code == 200
    _edit_template(client, msp, t, NEW_COLUMNS, NEW_EXAMPLES)

    assert _take(client, tenant, copy_id).status_code == 200
    assert tag_origins(db_session, copy) == {
        "PE.L2-3.10.4": "api", "PE.L2-3.10.5": "template_adoption",
    }


@pytest.mark.integration
def test_taking_an_update_is_refused_while_evidence_rests_on_a_tag_it_drops(
    db_session, monkeypatch
):
    msp, tenant, t, client, copy_id = _adopted(db_session)
    tbase = ONE.format(org=msp, list=t.id)
    assert client.post(f"{tbase}/tags", json={"control_key": "3.10.5"}).status_code == 201
    assert client.delete(f"{tbase}/tags/PE.L2-3.10.3").status_code == 200
    _edit_template(client, msp, t, NEW_COLUMNS, NEW_EXAMPLES)
    monkeypatch.setattr(
        list_adoption, "tag_removal_blockers",
        lambda s, list_id, key: ["Evidence 'Q3 visitors' in assessment 'FY26'"]
        if key == "PE.L2-3.10.3" else [],
    )
    r = _take(client, tenant, copy_id)
    assert r.status_code == 409 and "Q3 visitors" in r.json()["detail"]
    assert _versions(db_session, uuid.UUID(copy_id)) == 1


@pytest.mark.integration
def test_taking_an_update_needs_membership_in_the_templates_org(db_session):
    msp, tenant, t, client, copy_id = _adopted(db_session)
    _edit_template(client, msp, t, NEW_COLUMNS, NEW_EXAMPLES)
    app.dependency_overrides.clear()
    tenant_only = _client(db_session, tenant, role="msp_engineer", name="Tess Tenant")
    detail = tenant_only.get(ONE.format(org=tenant, list=copy_id)).json()
    # The tenant sees that a newer version exists; reading or taking it is
    # for someone who stands in both orgs.
    assert detail["adopted_from"]["update_available"] is True
    assert _take(tenant_only, tenant, copy_id).status_code == 403
    app.dependency_overrides.clear()
    poc = _client(db_session, tenant, role="customer_poc", also={msp: "msp_engineer"})
    assert _take(poc, tenant, copy_id).status_code == 403


@pytest.mark.integration
def test_template_heads_and_distribution_answer_only_for_the_current_org(db_session):
    from app.rls import set_current_org

    msp, tenant, t, client, copy_id = _adopted(db_session)
    db_session.execute(select(1))
    db_session.connection().exec_driver_sql("SET ROLE wingrc_app")
    try:
        set_current_org(db_session, tenant)
        assert set(list_adoption.template_heads(db_session)) == {uuid.UUID(copy_id)}
        assert list_adoption.template_distribution(db_session) == {}
        set_current_org(db_session, msp)
        assert list_adoption.template_heads(db_session) == {}
        assert set(list_adoption.template_distribution(db_session)) == {t.id}
    finally:
        db_session.connection().exec_driver_sql("RESET ROLE")


@pytest.mark.integration
def test_adopting_and_taking_updates_never_touch_sprs_or_control_state(db_session):
    from app.engine import start_assessment
    from app.models import AssessmentObjective, Control, ControlState, Framework

    msp, tenant = _org(db_session, "MSP"), _org(db_session, "Tenant")
    fw = Framework(key=f"fw-la-{uuid.uuid4().hex[:6]}", name="LA FW", version="r2")
    db_session.add(fw)
    db_session.flush()
    ctrl = Control(
        framework_id=fw.id, control_id="PE.L2-3.10.3", family="PE", title="Escort",
        requirement_text="Escort visitors.", sprs_weight=1, sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    db_session.add(AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Escort"))
    db_session.flush()
    a = start_assessment(db_session, tenant, fw.id, "LA SPRS")
    db_session.commit()

    def snapshot():
        db_session.expire_all()
        return (
            db_session.get(type(a), a.id).sprs_score,
            sorted(
                (s.id, s.status)
                for s in db_session.scalars(
                    select(ControlState).where(ControlState.assessment_id == a.id)
                )
            ),
        )

    before = snapshot()
    t = _template(db_session, msp)
    client = _client(db_session, tenant, also={msp: "msp_engineer"})
    copy_id = _adopt(client, tenant, msp, [t.id]).json()["items"][0]["list_id"]
    _edit_template(client, msp, t, NEW_COLUMNS, NEW_EXAMPLES)
    assert _take(client, tenant, copy_id).status_code == 200
    assert snapshot() == before
    assert db_session.scalar(select(ListVersion.id).where(ListVersion.org_id == tenant))
