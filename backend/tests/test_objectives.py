"""Integration tests for the practitioner-notes edit/revert router
(migration 0032, backend/app/routers/objectives.py).

Covers: RBAC (msp_admin only, no c3pao_assessor/msp_engineer exception),
edit round-trip, reseed-doesn't-clobber-an-edited-note (the seed-side half
of this lives in test_catalog_seed.py -- this file only exercises the
router), revert restores the frozen AI original and clears the edited
markers (making the row reseed-eligible again), and both endpoints write
an audit_log entry with actor attribution and the full before/after text.

Run in-container:
    docker compose exec backend pytest tests/test_objectives.py -m integration -v
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import get_current_user
from app.db import get_session
from app.main import app
from app.models import AssessmentObjective, AuditLog, Control, Framework
from tests.conftest import _app_session, _authed, _make_fake_user

pytestmark = pytest.mark.integration


@pytest.fixture
def admin_client(db_session, fake_msp_admin):
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, fake_msp_admin)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def engineer_client(db_session):
    engineer = _make_fake_user(role="msp_engineer")
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, engineer)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def assessor_client(db_session):
    assessor = _make_fake_user(role="c3pao_assessor")
    app.dependency_overrides[get_session] = _app_session(db_session)
    app.dependency_overrides[get_current_user] = _authed(db_session, assessor)
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_objective(db_session, *, notes: str | None = "AI-drafted note.") -> AssessmentObjective:
    fw = Framework(key=f"fw-{uuid.uuid4().hex}", name="Test FW", version="r2")
    db_session.add(fw)
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id=f"AC.L2-{uuid.uuid4().hex[:6]}",
        family="AC",
        title="Test control",
        requirement_text="Test requirement",
        sprs_weight=1,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(
        control_id=ctrl.id,
        objective_key="a",
        text="Authorized users are identified.",
        practitioner_notes=notes,
        practitioner_notes_original=notes,
    )
    db_session.add(obj)
    db_session.flush()
    return obj


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def test_engineer_cannot_edit(engineer_client, db_session):
    obj = _seed_objective(db_session)
    r = engineer_client.patch(
        f"/objectives/{obj.id}/practitioner-notes", json={"text": "rewritten"}
    )
    assert r.status_code == 403


def test_assessor_cannot_edit(assessor_client, db_session):
    """c3pao_assessor stays read-only here too -- no write exception was
    carved for it (see routers/objectives.py's module docstring)."""
    obj = _seed_objective(db_session)
    r = assessor_client.patch(
        f"/objectives/{obj.id}/practitioner-notes", json={"text": "rewritten"}
    )
    assert r.status_code == 403


def test_engineer_cannot_revert(engineer_client, db_session):
    obj = _seed_objective(db_session)
    r = engineer_client.post(f"/objectives/{obj.id}/practitioner-notes/revert")
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------------


def test_edit_round_trip(admin_client, db_session):
    obj = _seed_objective(db_session, notes="Original AI text.")
    r = admin_client.patch(
        f"/objectives/{obj.id}/practitioner-notes", json={"text": "Rewritten by an admin."}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["practitioner_notes"] == "Rewritten by an admin."
    assert body["practitioner_notes_edited_at"] is not None
    assert body["practitioner_notes_edited_by"]["status"] == "active"

    db_session.refresh(obj)
    assert obj.practitioner_notes == "Rewritten by an admin."
    assert obj.practitioner_notes_edited_at is not None
    assert obj.practitioner_notes_edited_by is not None
    # The frozen original is untouched by an edit -- only revert reads it.
    assert obj.practitioner_notes_original == "Original AI text."


def test_edit_rejects_blank_text(admin_client, db_session):
    obj = _seed_objective(db_session)
    r = admin_client.patch(f"/objectives/{obj.id}/practitioner-notes", json={"text": "   "})
    assert r.status_code == 422


def test_edit_unknown_objective_404s(admin_client):
    r = admin_client.patch(
        f"/objectives/{uuid.uuid4()}/practitioner-notes", json={"text": "x"}
    )
    assert r.status_code == 404


def test_edit_writes_audit_event_with_before_and_after(admin_client, db_session):
    obj = _seed_objective(db_session, notes="Original AI text.")
    admin_client.patch(
        f"/objectives/{obj.id}/practitioner-notes", json={"text": "Rewritten by an admin."}
    )
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "practitioner_notes.edit")
    ).first()
    assert entry is not None
    assert entry.entity_id == obj.id
    assert entry.before_value["practitioner_notes"] == "Original AI text."
    assert entry.after_value["practitioner_notes"] == "Rewritten by an admin."
    assert entry.actor_type == "user"


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


def test_revert_restores_ai_original_and_clears_edit_markers(admin_client, db_session):
    obj = _seed_objective(db_session, notes="Original AI text.")
    admin_client.patch(
        f"/objectives/{obj.id}/practitioner-notes", json={"text": "Mangled by mistake."}
    )
    r = admin_client.post(f"/objectives/{obj.id}/practitioner-notes/revert")
    assert r.status_code == 200
    body = r.json()
    assert body["practitioner_notes"] == "Original AI text."
    assert body["practitioner_notes_edited_at"] is None
    assert body["practitioner_notes_edited_by"] is None

    db_session.refresh(obj)
    assert obj.practitioner_notes == "Original AI text."
    assert obj.practitioner_notes_edited_at is None
    assert obj.practitioner_notes_edited_by is None
    # Reverting clears the edited markers, which is exactly the signal
    # seeds/catalog.py's _should_write_practitioner_notes checks -- a
    # reverted row is reseed-eligible again, same as one never touched.


def test_revert_without_a_prior_edit_rejected(admin_client, db_session):
    obj = _seed_objective(db_session, notes="Original AI text.")
    r = admin_client.post(f"/objectives/{obj.id}/practitioner-notes/revert")
    assert r.status_code == 400


def test_revert_writes_audit_event(admin_client, db_session):
    obj = _seed_objective(db_session, notes="Original AI text.")
    admin_client.patch(
        f"/objectives/{obj.id}/practitioner-notes", json={"text": "Mangled by mistake."}
    )
    admin_client.post(f"/objectives/{obj.id}/practitioner-notes/revert")
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "practitioner_notes.revert")
    ).first()
    assert entry is not None
    assert entry.entity_id == obj.id
    assert entry.before_value["practitioner_notes"] == "Mangled by mistake."
    assert entry.after_value["practitioner_notes"] == "Original AI text."
