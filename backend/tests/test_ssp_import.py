"""ssp_import.preview_import and `wingrc ssp-import-preview`: read a
customer's SSP against one assessment and write nothing.

The source says "Met" on every row. The assertions that matter are that
none of control_state, its status, the SPRS score or implementation_statement
changes, and that a target the command cannot see is refused rather than
read as empty (docs/roadmap.md, "Importing implementation statements from
a customer's SSP"; the visibility rule from "A zero nobody checked").
"""

from __future__ import annotations

import uuid

import pytest
from docx import Document
from sqlalchemy import func, select

from app.engine import start_assessment
from app.models import (
    AssessmentObjective,
    AuditLog,
    Control,
    ControlState,
    Framework,
    ImplementationStatement,
    Organization,
    SprsSnapshot,
)
from app.rls import set_current_org
from app.ssp_import import TargetNotVisible, preview_import, render

pytestmark = pytest.mark.integration

_TEXTS = {
    "a": "Authorized users are identified.",
    "b": "Processes acting on behalf of authorized users are identified.",
    "c": "Devices (and other systems) authorized to connect to the system are identified.",
}


@pytest.fixture
def target(db_session):
    tag = uuid.uuid4().hex[:6]
    org = Organization(name=f"SspOrg-{tag}")
    other = Organization(name=f"SspOther-{tag}")
    fw = Framework(key=f"fw-ssp-{tag}", name="Test FW", version="r2")
    db_session.add_all([org, other, fw])
    db_session.flush()
    ctrl = Control(framework_id=fw.id, control_id="AC.L2-3.1.1", family="AC", title="t",
                   requirement_text="r", sprs_weight=5, sequence_order=1)
    db_session.add(ctrl)
    db_session.flush()
    objs = {}
    for key, text in _TEXTS.items():
        objs[key] = AssessmentObjective(control_id=ctrl.id, objective_key=key, text=text)
        db_session.add(objs[key])
    db_session.flush()
    assessment = start_assessment(db_session, org.id, fw.id, f"SSP {tag}")
    db_session.add(ImplementationStatement(
        org_id=org.id, objective_id=objs["c"].id, assessment_id=assessment.id,
        body="Already written in WinGRC.", status="reviewed",
    ))
    elsewhere = start_assessment(db_session, other.id, fw.id, f"Other {tag}")
    db_session.commit()
    return org, assessment, other, elsewhere


def _file(tmp_path):
    doc = Document()
    doc.add_paragraph("8.1 Access Control")
    t = doc.add_table(rows=1, cols=5)
    for cell, h in zip(t.rows[0].cells, ["Control", "Status", "Notes",
                                         "Implementation Statement", "RACI"], strict=True):
        cell.text = h
    for key in "abc":
        for cell, v in zip(t.add_row().cells, [_TEXTS[key], "Met", "-", f"Statement {key}.",
                                               "Pat Doe pat@example.com"], strict=True):
            cell.text = v
    for cell, v in zip(t.add_row().cells, ["Our firewall is very good.", "Met", "-", "x", "-"],
                       strict=True):
        cell.text = v
    path = tmp_path / "ExampleSSP.docx"
    doc.save(str(path))
    return path


def _state(db_session, assessment):
    statuses = sorted(db_session.scalars(
        select(ControlState.status).where(ControlState.assessment_id == assessment.id)))
    db_session.refresh(assessment)
    count = lambda m, col: db_session.scalar(  # noqa: E731
        select(func.count()).select_from(m).where(col == assessment.id))
    return (statuses, assessment.sprs_score,
            count(ImplementationStatement, ImplementationStatement.assessment_id),
            count(SprsSnapshot, SprsSnapshot.assessment_id),
            db_session.scalar(select(func.count()).select_from(AuditLog)))


def test_the_preview_writes_nothing_though_the_source_says_met(db_session, target, tmp_path):
    org, assessment, _, _ = target
    before = _state(db_session, assessment)
    assert "met" not in before[0], (
        "fixture must start with nothing met, or this test proves nothing")

    result = preview_import(db_session, _file(tmp_path), org_id=org.id,
                            assessment_id=assessment.id)

    assert _state(db_session, assessment) == before
    assert result.plan.would_create == ("AC.L2-3.1.1[a]", "AC.L2-3.1.1[b]")
    assert result.plan.already_has_statement == ("AC.L2-3.1.1[c]",)
    lines = render(result)
    assert "  Met: 4" in lines
    assert "  row 4 [8.1 Access Control]: Our firewall is very good." in lines
    assert lines[-1] == "PREVIEW ONLY -- nothing was written."
    assert not any("pat@example.com" in line for line in lines), "RACI content must not be printed"


def test_the_report_is_the_same_twice(db_session, target, tmp_path):
    org, assessment, _, _ = target
    path = _file(tmp_path)
    first = render(preview_import(db_session, path, org_id=org.id, assessment_id=assessment.id))
    second = render(preview_import(db_session, path, org_id=org.id, assessment_id=assessment.id))
    assert first == second


def test_another_orgs_assessment_is_refused(db_session, target, tmp_path):
    org, _, _, elsewhere = target
    with pytest.raises(TargetNotVisible, match="not visible"):
        preview_import(db_session, _file(tmp_path), org_id=org.id, assessment_id=elsewhere.id)


def test_as_the_app_role_it_sees_its_own_org_and_refuses_one_rls_hides(
    db_session, target, tmp_path
):
    """Live runs the CLI as `wingrc_app`. With the org set, the command sees
    that org's assessment and its existing statement; an assessment RLS
    hides is refused, never read as having no statements."""
    org, assessment, other, elsewhere = target
    db_session.execute(select(1))
    db_session.connection().exec_driver_sql("SET ROLE wingrc_app")
    try:
        result = preview_import(db_session, _file(tmp_path), org_id=org.id,
                                assessment_id=assessment.id)
        assert result.plan.already_has_statement == ("AC.L2-3.1.1[c]",)
        set_current_org(db_session, org.id)
        assert db_session.scalars(select(ControlState).where(
            ControlState.assessment_id == elsewhere.id)).first() is None, (
            "fixture must have RLS hide the other org's rows, or this test proves nothing")
        with pytest.raises(TargetNotVisible):
            preview_import(db_session, _file(tmp_path), org_id=other.id,
                           assessment_id=assessment.id)
    finally:
        db_session.connection().exec_driver_sql("RESET ROLE")
