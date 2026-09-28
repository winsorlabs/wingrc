"""The post-commit RLS read pattern (roadmap N.2 section 8's audit).

**What this file documents, and why it is a test rather than a note.**

`SET LOCAL app.current_org` is scoped to a transaction. `session.commit()`
ends that transaction, so any DB read issued *after* a commit runs with no
org context and, under RLS enforcement, matches zero rows. That is not a
hypothetical: `routers/scope.py` carries two comments about it (its
Liongard-environment mapping sets `updated_at` in Python and deliberately
skips a post-commit `refresh()`), and N.1's own bench run hit it a third
time in `routers/documents.py`, where it surfaced as `ObjectDeletedError`
on a row that plainly existed.

The exposure is narrower than "any attribute read after commit", because
`db.py`'s `SessionLocal` sets `expire_on_commit=False` -- already-loaded
attributes stay valid. What does NOT stay valid is:

  1. a column populated by a server-side SQL expression during an UPDATE
     (`onupdate=func.now()`), which SQLAlchemy marks expired and re-fetches
     lazily on first access; and
  2. any explicit `session.refresh()` or fresh `select()` issued after the
     commit.

`test_documents_api.py` covers case 1 for `document` (create/patch/publish
all build their response before committing). This file covers case 2, where
the failure is *silent* -- a `select()` that matches zero rows returns an
empty result rather than raising, so the endpoint answers 200 with a field
quietly set to null.

**Today this is latent, not live.** The app connects as `wingrc`, which is
both table owner and (in the compose dev setup) a superuser, and Postgres
bypasses RLS unconditionally for both -- migration `0016_app_role`'s own
docstring says the runtime cutover to `wingrc_app` has not happened. The
test harness, by contrast, does `SET ROLE wingrc_app` per request
(`conftest.py:_app_session`) precisely so this class of bug surfaces before
the cutover rather than after it -- the same reason
`docs/PLAN-auth-rbac-completion.md` flags the session-fixation RLS gap it
caught the same way.

So the assertion below is deliberately written as "this is what happens
under RLS enforcement", and it is what makes the audit result in the
roadmap writeup reproducible instead of a claim.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.engine import start_assessment
from app.main import app
from app.models import (
    AssessmentObjective,
    Control,
    ControlState,
    Framework,
    Organization,
)
from tests.conftest import _app_session, _authed, _grant

pytestmark = pytest.mark.integration


@pytest.fixture
def client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed(db_session, fake_msp_admin) -> dict:
    org = Organization(name=f"PostCommitOrg-{uuid.uuid4().hex[:8]}")
    fw = Framework(key=f"fw-pc-{uuid.uuid4().hex[:6]}", name="Test FW", version="r2")
    db_session.add_all([org, fw])
    db_session.flush()
    _grant(db_session, fake_msp_admin, org_id=org.id)

    ctrl = Control(
        framework_id=fw.id,
        control_id="AC.L2-3.1.1",
        family="AC",
        title="Access Control",
        requirement_text="Limit system access.",
        sprs_weight=5,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()
    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="Objective a.")
    db_session.add(obj)
    db_session.flush()

    assessment = start_assessment(
        db_session, org_id=org.id, framework_id=fw.id, name="Post-commit Assessment"
    )
    db_session.flush()
    control_state = db_session.scalars(
        select(ControlState).where(
            ControlState.assessment_id == assessment.id, ControlState.objective_id == obj.id
        )
    ).one()
    db_session.commit()
    return {
        "org": org,
        "assessment": assessment,
        "objective": obj,
        "control_state": control_state,
    }


def test_upsert_statements_control_state_id_is_lost_to_a_post_commit_read(
    client, db_session, fake_msp_admin
):
    """`routers/assessments.py:upsert_statements` reads `control_state`
    AFTER `session.commit()` to fill `StatementOut.control_state_id`.

    Under RLS enforcement that select matches nothing, and because a
    zero-row select is not an error, every statement comes back with
    `control_state_id: null` and a 200. The row is right there -- asserted
    directly below, from a session that still has org context -- so this is
    purely an artefact of when the read happens.

    This test asserts the CURRENT behaviour, wrong as it is, on purpose:
    N.2 section 8 asked for an audit and a report rather than forty quiet
    fixes, and the systemic options (re-issuing `SET LOCAL` on an
    `after_commit` event, versus moving the reads before the commit) are
    Jarrod's call. When that fix lands, this test is what has to be
    inverted -- and its failure is the signal that it did.
    """
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    assessment_id = d["assessment"].id

    r = client.post(
        f"/orgs/{org_id}/assessments/{assessment_id}/statements",
        json={
            "items": [
                {
                    "objective_id": str(d["objective"].id),
                    "body": "We limit system access to authorised users.",
                    "status": "draft",
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    statements = r.json()
    assert len(statements) == 1
    assert statements[0]["body"] == "We limit system access to authorised users."

    # The control_state row exists and is the one the response should have
    # named -- read here with org context still in place.
    db_session.expire_all()
    still_there = db_session.scalars(
        select(ControlState).where(ControlState.id == d["control_state"].id)
    ).one()
    assert still_there is not None

    assert statements[0]["control_state_id"] is None, (
        "EXPECTED-WRONG, and the point of this test: upsert_statements resolves "
        "control_state AFTER session.commit(), so under RLS enforcement the select "
        "matches zero rows and the field silently comes back null even though the "
        "row exists. If this assertion starts failing, the post-commit read was "
        "fixed -- invert it and delete this note rather than deleting the test."
    )


def test_document_endpoints_do_not_have_the_same_gap(client, db_session, fake_msp_admin):
    """The contrast case, so the pattern is legible rather than abstract.

    `routers/documents.py` builds every response *before* committing, so
    `updated_at` (which has `onupdate=func.now()` and is therefore expired
    by the UPDATE that sets `current_version_id`) is populated and returned
    normally rather than raising `ObjectDeletedError`.
    """
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id

    r = client.post(
        f"/orgs/{org_id}/documents",
        json={"doc_id": "AC-POL-900", "doc_type": "policy", "title": "Access Control"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["updated_at"] is not None

    document_id = r.json()["id"]
    r = client.patch(f"/orgs/{org_id}/documents/{document_id}", json={"title": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Renamed"
    assert r.json()["updated_at"] is not None
