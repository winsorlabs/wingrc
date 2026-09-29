"""The post-commit RLS read pattern -- now closed by app/rls.py.

**History, because the inversion below only makes sense with it.** This
file was written during N.2 to record an audit result reproducibly: it
asserted the *wrong* behaviour on purpose, with a note saying that fixing
the bug meant inverting the assertion. That fix has landed (`app/rls.py`'s
after_begin hook), so the assertions now say what should happen, and this
file's job has changed from documenting a bug to guarding its return.

**Why it is a test rather than a note.**

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
the failure was *silent* -- a `select()` that matches zero rows returns an
empty result rather than raising, so the endpoint answered 200 with a field
quietly set to null. That silence is precisely why the fix is a session
hook and not a convention: a convention only protects against mistakes
someone can notice, and this one looked exactly like success.

**Why it was latent rather than live.** The app still connects as
`wingrc`, which is both table owner and (in the compose dev setup) a
superuser, and Postgres bypasses RLS unconditionally for both unless
`FORCE ROW LEVEL SECURITY` is set, which it is not -- migration
`0016_app_role`'s own docstring says the runtime cutover to `wingrc_app`
has not happened. The test harness, by contrast, does `SET ROLE
wingrc_app` per request (`conftest.py:_app_session`) precisely so this
class of bug surfaces before the cutover rather than after it, the same
reason `docs/PLAN-auth-rbac-completion.md` flags the session-fixation RLS
gap it caught the same way. That harness is what makes the tests below
meaningful: they fail without the hook.
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
        "control": ctrl,
        "objective": obj,
        "control_state": control_state,
    }


def test_upsert_statements_resolves_control_state_after_its_commit(
    client, db_session, fake_msp_admin
):
    """`routers/assessments.py:upsert_statements` reads `control_state`
    AFTER `session.commit()` to fill `StatementOut.control_state_id`.

    Before `app/rls.py`, that select matched nothing under RLS and --
    because a zero-row select is not an error -- every statement came back
    with `control_state_id: null` and a 200. The row was there the whole
    time; it was purely an artefact of *when* the read happened.

    Asserting the real id is the point. If the after_begin hook is ever
    removed or mis-registered this goes back to null, and it fails here
    rather than in a customer's SSP.
    """
    d = _seed(db_session, fake_msp_admin)
    org_id = d["org"].id
    assessment_id = d["assessment"].id

    # PUT .../controls/{control_db_id}/statements -- the statements endpoint
    # is keyed on the control's internal id, not the assessment alone.
    r = client.put(
        f"/orgs/{org_id}/assessments/{assessment_id}/controls/{d['control'].id}/statements",
        json=[
            {
                "objective_id": str(d["objective"].id),
                "body": "We limit system access to authorised users.",
                "status": "draft",
            }
        ],
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

    assert statements[0]["control_state_id"] == str(d["control_state"].id), (
        "upsert_statements resolves control_state AFTER session.commit(); this is "
        "the real id only because rls.py's after_begin hook re-applied the org to "
        "the new transaction. A null here means the hook is gone or mis-registered, "
        "and the failure mode it guards is silent -- a 200 with the field quietly "
        "nulled -- which is why this is asserted rather than trusted."
    )


def test_document_endpoints_do_not_have_the_same_gap(client, db_session, fake_msp_admin):
    """The other half of the class, still worth holding.

    `routers/documents.py` builds every response *before* committing, so
    `updated_at` (which has `onupdate=func.now()` and is therefore expired
    by the UPDATE that sets `current_version_id`) is populated without
    needing the hook at all. Belt and braces: the hook now covers this
    shape too, but N.1's explicit ordering is still the clearer code, and
    this asserts it keeps working.
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
