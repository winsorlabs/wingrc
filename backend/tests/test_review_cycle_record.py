"""Review cycles attach only what attests (docs/roadmap.md, 2026-10-19).

What these pin:
- A completed cycle's record is attached to AC.L2-3.1.1[a]/[c], as before.
- A deadline close or an undeliverable one writes and stores its record
  and attaches nothing; the reviewer statuses that are the record's
  content are unchanged.
- Unattached, the record is still findable: on the cycle's page, through
  HTTP, and in the bundle's unlinked evidence. It is not offered.
- The evidence ✕ never deletes a cycle's record.
- The detach of records attached before this: dry-run matches what it
  does; each link is archived, not deleted, with one audit row and the
  reason; one a product's deactivation archived is taken too, so no
  reactivation brings it back; the record and its bytes survive; a second
  run has nothing to do and re-archives nothing; a completed cycle's links
  stay; the bundle carries the record as archived.
- None of it moves control_state or SPRS.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app import review_cycles, scheduler
from app.bundle_service import snapshot_bundle
from app.models import (
    Assessment,
    AuditLog,
    ControlState,
    Evidence,
    EvidenceStateLink,
    Product,
    ReviewCycle,
    ReviewCycleReviewer,
)
from tests.test_review_cycles import (  # noqa: F401 -- fixtures
    _client_as,
    _open_url,
    _seed_customer_poc,
    _seed_org_with_catalog,
    _to_current_user,
    client,
    fake_msp_admin,
    storage,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _sweep_storage(monkeypatch, storage):  # noqa: F811
    monkeypatch.setattr(scheduler, "get_storage_client", lambda: storage)


def _overdue_cycle(db_session, d, *, notified: bool) -> ReviewCycle:
    org_id = d["org"].id
    cycle = ReviewCycle(
        id=uuid.uuid4(), org_id=org_id,
        opened_at=datetime.now(UTC) - timedelta(days=25),
        due_at=datetime.now(UTC) - timedelta(days=4),
        status="open", cadence_months=6, opened_by="scheduler",
    )
    db_session.add(cycle)
    db_session.flush()
    asked = datetime.now(UTC) - timedelta(days=25)
    db_session.add(ReviewCycleReviewer(
        id=uuid.uuid4(), cycle_id=cycle.id, org_id=org_id,
        reviewer_name="Never Responded", reviewer_email="ghost@example.com",
        reviewer_side="client", status="requested",
        requested_at=asked, notified_at=asked if notified else None,
    ))
    db_session.commit()
    return cycle


def _states(db_session, d):
    db_session.expire_all()
    rows = db_session.execute(
        select(ControlState.id, ControlState.status)
        .where(ControlState.assessment_id == d["assessment"].id)
    ).all()
    sprs = db_session.scalar(
        select(Assessment.sprs_score).where(Assessment.id == d["assessment"].id)
    )
    return sorted((str(i), s) for i, s in rows), sprs


def _record(db_session, cycle) -> Evidence:
    return db_session.scalars(
        select(Evidence).where(Evidence.source_review_cycle_id == cycle.id)
    ).one()


def _links(db_session, ev):
    return db_session.scalars(
        select(EvidenceStateLink).where(EvidenceStateLink.evidence_id == ev.id)
    ).all()


def test_a_completed_cycle_attaches_its_record_and_its_page_says_where(
    client, db_session, storage, fake_msp_admin  # noqa: F811
):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    poc = _seed_customer_poc(db_session, org_id=d["org"].id)
    cycle_id = client.post(_open_url(d["org"].id)).json()["id"]
    assert client.post(f"{_open_url(d['org'].id)}/{cycle_id}/attest", json={}).status_code == 200
    poc_client = _client_as(
        db_session, storage, _to_current_user(poc, d["org"].id), org_id=d["org"].id
    )
    r = poc_client.post(f"{_open_url(d['org'].id)}/{cycle_id}/attest", json={})
    assert r.status_code == 200

    cycle = db_session.get(ReviewCycle, uuid.UUID(cycle_id))
    db_session.refresh(cycle)
    assert cycle.status == "completed", "fixture: both reviewers attested"
    ev = _record(db_session, cycle)
    assert len(_links(db_session, ev)) == 2

    record = poc_client.get(f"{_open_url(d['org'].id)}/{cycle_id}").json()["record"]
    assert record["evidence_id"] == str(ev.id)
    assert record["attached_to"] == [
        {"assessment_name": "A1", "objective_key": "AC.L2-3.1.1[a]"},
        {"assessment_name": "A1", "objective_key": "AC.L2-3.1.1[c]"},
    ]

    c_link = next(lk for lk in _links(db_session, ev) if lk.control_state_id != (
        db_session.scalar(select(ControlState.id).where(
            ControlState.assessment_id == d["assessment"].id,
            ControlState.objective_id == d["obj_a"].id,
        ))
    ))
    c_link.is_archived = True
    db_session.commit()
    record = poc_client.get(f"{_open_url(d['org'].id)}/{cycle_id}").json()["record"]
    assert record["attached_to"] == [
        {"assessment_name": "A1", "objective_key": "AC.L2-3.1.1[a]"},
    ], "an archived link is not attached"


@pytest.mark.parametrize(
    ("notified", "status", "reviewer", "outcome"),
    [
        (True, "closed_unattested", "no_response", "without full attestation"),
        (False, "closed_undeliverable", "not_notified", "no review was actually attempted"),
    ],
)
def test_a_close_without_attestation_records_and_attaches_nothing(
    client, db_session, storage, fake_msp_admin, notified, status, reviewer, outcome  # noqa: F811
):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    before = _states(db_session, d)
    assert before[0], "fixture: an in_progress assessment with [a]/[c], or no link is possible"
    cycle = _overdue_cycle(db_session, d, notified=notified)

    assert scheduler._review_cycle_sweep(db_session)["cycles_closed"] == 1
    db_session.refresh(cycle)
    assert cycle.status == status
    assert [r.status for r in db_session.scalars(
        select(ReviewCycleReviewer).where(ReviewCycleReviewer.cycle_id == cycle.id)
    )] == [reviewer], "the reviewer statuses are the record's content"

    ev = _record(db_session, cycle)
    assert ev.artifact_type == "attestation" and ev.kind == "file"
    assert outcome in storage.files[ev.storage_key].decode(), "the record is stored, and says so"
    assert _links(db_session, ev) == [], "a record of nobody attesting attaches nothing"
    assert _states(db_session, d) == before

    page = client.get(f"{_open_url(d['org'].id)}/{cycle.id}").json()["record"]
    assert page["evidence_id"] == str(ev.id) and page["attached_to"] == []
    assert page["download_url"], "findable on the cycle's page"

    snap = snapshot_bundle(db_session, storage, d["org"].id, d["assessment"].id)
    assert [e.evidence_id for e in snap.unlinked_evidence] == [ev.id], (
        "and in the bundle's unlinked evidence"
    )


def test_another_org_cannot_read_a_cycles_record(
    client, db_session, storage, fake_msp_admin  # noqa: F811
):
    from tests.conftest import _make_fake_user

    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    cycle = _overdue_cycle(db_session, d, notified=True)
    scheduler._review_cycle_sweep(db_session)
    ev = _record(db_session, cycle)

    ira = _make_fake_user(email="ira@example.com")
    other = _seed_org_with_catalog(db_session, ira)
    outsider = _client_as(db_session, storage, ira, org_id=other["org"].id)
    # Org B's own path, org A's ids: not found.
    assert outsider.get(f"{_open_url(other['org'].id)}/{cycle.id}").status_code == 404
    assert outsider.get(f"/orgs/{other['org'].id}/evidence/{ev.id}/download").status_code == 404
    # Org A's path: not a member.
    assert outsider.get(f"{_open_url(d['org'].id)}/{cycle.id}").status_code == 403
    assert outsider.get(f"/orgs/{d['org'].id}/evidence/{ev.id}/download").status_code == 403


def test_the_evidence_x_never_deletes_a_cycles_record(
    client, db_session, storage, fake_msp_admin  # noqa: F811
):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    cycle = _overdue_cycle(db_session, d, notified=True)
    ev = review_cycles.close_cycle(db_session, storage, cycle=cycle, status="completed")
    db_session.commit()
    links = _links(db_session, ev)
    assert len(links) == 2, "fixture: a completed record attached to [a] and [c]"
    for lk in links:
        url = (f"/orgs/{d['org'].id}/assessments/{d['assessment'].id}/control-states/"
               f"{lk.control_state_id}/evidence/{ev.id}")
        assert client.delete(url).status_code == 204
    db_session.expire_all()
    assert db_session.get(Evidence, ev.id) is not None, "the last unlink keeps the record"
    assert ev.storage_key in storage.files, "and its bytes"


def _legacy_attached(db_session, storage, d, *, status="closed_unattested"):  # noqa: F811
    """A record closed the way close_cycle did before 2026-10-19:
    attached to [a]/[c] whatever the status."""
    cycle = _overdue_cycle(db_session, d, notified=True)
    ev = review_cycles.close_cycle(db_session, storage, cycle=cycle, status=status)
    for cs in review_cycles._mapped_control_states(db_session, org_id=d["org"].id):
        db_session.add(EvidenceStateLink(evidence_id=ev.id, control_state_id=cs.id))
    db_session.commit()
    return cycle, ev


def test_the_detach_archives_only_unattested_links_and_keeps_every_record(
    client, db_session, storage, fake_msp_admin  # noqa: F811
):
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    org_id = d["org"].id
    unattested_cycle, unattested = _legacy_attached(db_session, storage, d)
    done = review_cycles.close_cycle(
        db_session, storage, cycle=_overdue_cycle(db_session, d, notified=True), status="completed"
    )
    db_session.commit()
    before = _states(db_session, d)
    stored = storage.files[unattested.storage_key]
    assert len(_links(db_session, unattested)) == 2 and len(_links(db_session, done)) == 2, (
        "fixture: one record of each kind attached, or the detach proves nothing"
    )
    # One of the two as a deactivation left it: archived, with the product
    # whose reactivation would restore it.
    tool = Product(
        framework_id=d["fw"].id, key=f"prod-{uuid.uuid4().hex[:6]}", name="Tool",
        provider="Vendor", category="ESP", asset_type="SPA", role="test",
    )
    db_session.add(tool)
    db_session.flush()
    by_tool = sorted(_links(db_session, unattested), key=lambda lk: str(lk.id))[0]
    by_tool.is_archived, by_tool.archived_by_product = True, tool.id
    tool_archived_at = by_tool.archived_at = datetime.now(UTC) - timedelta(days=2)
    db_session.commit()

    planned = [(f["evidence"].id, f["objective_key"])
               for f in review_cycles.unattested_links(db_session, org_id=org_id)]
    assert planned == [(unattested.id, "AC.L2-3.1.1[a]"), (unattested.id, "AC.L2-3.1.1[c]")]
    done_now = review_cycles.detach_unattested(db_session, org_id=org_id, actor="test")
    assert [(f["evidence"].id, f["objective_key"]) for f in done_now] == planned, (
        "the dry run says what the run does"
    )
    db_session.commit()

    db_session.expire_all()
    archived = _links(db_session, unattested)
    assert len(archived) == 2, "archived, not deleted: the link rows stay"
    assert all(lk.is_archived and lk.archived_at is not None for lk in archived)
    assert all(lk.archived_by_product is None for lk in archived), (
        "no product's reactivation may restore a detached link"
    )
    assert next(lk for lk in archived if lk.id == by_tool.id).archived_at == tool_archived_at, (
        "already archived: its date stays the deactivation's"
    )
    stamps = {lk.id: lk.archived_at for lk in archived}
    assert len(_links(db_session, done)) == 2
    assert not any(lk.is_archived for lk in _links(db_session, done)), (
        "a completed cycle's links stay live"
    )
    kept = db_session.get(Evidence, unattested.id)
    assert kept is not None and kept.source_review_cycle_id == unattested_cycle.id
    assert storage.files[kept.storage_key] == stored, "the record's bytes, read back, unchanged"
    assert _states(db_session, d) == before, "detaching moves no status and no SPRS"

    audit = db_session.execute(
        select(AuditLog.before_value, AuditLog.context).where(
            AuditLog.entity_id == unattested.id,
            AuditLog.action == "evidence.attestation_detach",
        )
    ).all()
    assert sorted(b["objective_key"] for b, _ in audit) == ["AC.L2-3.1.1[a]", "AC.L2-3.1.1[c]"]
    assert {c["reason"] for _, c in audit} == {review_cycles.DETACH_REASON}
    assert {c["review_cycle_id"] for _, c in audit} == {str(unattested_cycle.id)}

    assert {b["archived_by_product"] for b, _ in audit} == {None, str(tool.id)}

    assert review_cycles.unattested_links(db_session, org_id=org_id) == [], "a second run: none"
    assert review_cycles.detach_unattested(db_session, org_id=org_id, actor="test") == []
    db_session.commit()
    db_session.expire_all()
    assert {lk.id: lk.archived_at for lk in _links(db_session, unattested)} == stamps, (
        "a second run re-archives nothing"
    )
    assert db_session.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.entity_id == unattested.id,
            AuditLog.action == "evidence.attestation_detach",
        )
    ) == 2

    snap = snapshot_bundle(db_session, storage, org_id, d["assessment"].id)
    [carried] = [e for e in snap.unlinked_evidence if e.evidence_id == unattested.id]
    assert carried.archived_from == ("AC.L2-3.1.1[a]", "AC.L2-3.1.1[c]"), (
        "after the detach the bundle carries the record, as archived, from where"
    )
    assert any("review cycle that closed without attestation" in r
               for r in carried.archived_reasons)
    assert done.id not in [e.evidence_id for e in snap.unlinked_evidence]


def test_the_migration_backfill_names_each_records_cycle(
    db_session, storage, fake_msp_admin  # noqa: F811
):
    """0070's backfill, run against a record written before the column."""
    from importlib import import_module

    match = import_module("app.migrations.versions.0070_evidence_review_cycle")._MATCH
    d = _seed_org_with_catalog(db_session, fake_msp_admin)
    cycle, ev = _legacy_attached(db_session, storage, d)
    other = review_cycles.close_cycle(
        db_session, storage, cycle=_overdue_cycle(db_session, d, notified=False),
        status="closed_unattested",
    )
    db_session.execute(text(
        "UPDATE evidence SET source_review_cycle_id = NULL WHERE org_id = :o"
    ), {"o": d["org"].id})
    db_session.execute(text("UPDATE evidence e SET source_review_cycle_id = c.id " + match))
    db_session.expire_all()
    assert db_session.get(Evidence, ev.id).source_review_cycle_id == cycle.id
    assert db_session.get(Evidence, other.id).source_review_cycle_id is not None
    assert db_session.get(Evidence, other.id).source_review_cycle_id != cycle.id
