"""Tests for app/cli.py::_reset_dev() -- the dev-DB cleanup utility.

Regression coverage for the FK-ordering gaps found while reconciling this
function against the current schema (see docs/roadmap.md's now-closed
Known defects entry): `_reset_dev()` was written before the audit/diagram/
evidence-task-linking tables existed and never deleted (or reordered
around) them, so it raised a foreign-key violation the moment a realistic
org -- one with audit history, a network diagram, or a finding with a
POA&M item -- got swept up in a reset.

Also covers the `reset-dev` command's production guard (_reset_dev_guard_error
and its use in reset_dev()): it must fail CLOSED -- refuse on an unset
WINGRC_ENVIRONMENT, not just an explicit "production" -- since an unset
variable is the actual shape of a fresh production deploy nobody configured
yet. The guard-only tests below (TestGuardError / TestResetDevCommandGuard)
don't touch a database at all, unlike the rest of this file; they still
carry the module's @pytest.mark.integration for consistency with "this is
the reset-dev test file," not because they need WINGRC_TEST_DATABASE_URL.

Also covers the incident that prompted the pre-flight-backup /
preview-counts / --orgs-only additions: `reset-dev --yes` was run against
wl-util-1's dev DB to clean up one stray verification org, and also wiped
the kept org's (Acme MSP) assessments/evidence/findings -- documented
behavior, but the confirmation prompt didn't say so and the person running
it didn't expect it. See TestPreviewCounts, TestOrgsOnly,
TestPreflightBackup, and TestYesPrintsSummary below.

Run in-container:
    docker compose exec backend pytest tests/test_cli_reset_dev.py -m integration -v
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli import (
    _as_count_sql,
    _preflight_backup,
    _preview_counts,
    _reset_dev,
    _reset_dev_guard_error,
)
from app.cli import app as cli_app
from app.engine import start_assessment
from app.models import (
    AssessmentObjective,
    AuditLog,
    Control,
    ControlState,
    Evidence,
    EvidenceTask,
    EvidenceTaskStateLink,
    Finding,
    Framework,
    Organization,
    PoamItem,
    SystemDescription,
)

pytestmark = pytest.mark.integration


def _seed_full_graph_org(
    db_session: Session, *, org: Organization | None = None, framework_key: str | None = None
) -> Organization:
    """Build a full assessment-layer graph exercising every FK path
    `_reset_dev()` has to walk safely: an audit_log row, a
    system_description diagram pointer into evidence, an
    evidence_task_state_link row, and a poa_m_item that references a
    finding -- the four gaps the original version of this fix closed.

    Defaults to a fresh throwaway org and a random test-framework key
    (original behavior, used by the two tests below that predate
    --orgs-only). Pass `org` to seed this graph against an existing org
    instead -- used by the --orgs-only test to give a *kept* org (Acme
    MSP) its own real assessment-layer data. Pass `framework_key` to seed
    against the real catalog key ('nist-800-171-r2') instead of a random
    test one -- realistic for a kept org (whose real assessments are
    always against the seeded catalog, never a test framework) and avoids
    an FK collision --orgs-only would otherwise hit: Tier 4/5 deletes
    every non-catalog framework unconditionally, so a *kept* org's
    assessment pointing at a *test* framework would survive Tier 3 (its
    deletion is skipped for the kept org under --orgs-only) only to have
    Tier 5 then try to delete the framework out from under it.
    """
    if org is None:
        org = Organization(name=f"ResetDevTestOrg-{uuid.uuid4().hex[:8]}")
        db_session.add(org)
    fw = Framework(
        key=framework_key or f"test-fw-{uuid.uuid4().hex[:8]}", name="Test FW", version="1"
    )
    db_session.add(fw)
    db_session.flush()

    ctrl = Control(
        framework_id=fw.id,
        control_id="AC.L2-3.1.1",
        family="AC",
        title="Test control",
        requirement_text="Test requirement",
        sprs_weight=1,
        sequence_order=1,
    )
    db_session.add(ctrl)
    db_session.flush()

    obj = AssessmentObjective(control_id=ctrl.id, objective_key="a", text="a")
    db_session.add(obj)
    db_session.flush()

    assessment = start_assessment(db_session, org.id, fw.id, "Reset-dev test")
    cs = db_session.scalars(
        select(ControlState).where(ControlState.assessment_id == assessment.id)
    ).first()
    assert cs is not None

    # audit_log — the originally-documented defect.
    db_session.add(
        AuditLog(
            org_id=org.id,
            actor="tester",
            actor_type="user",
            action="scope_entity.create",
            entity_type="scope_entity",
            entity_id=uuid.uuid4(),
        )
    )

    # system_description's diagram pointer into evidence.
    ev = Evidence(
        org_id=org.id,
        title="Network diagram",
        artifact_type="network_diagram",
        collected_at=datetime.now(UTC),
    )
    db_session.add(ev)
    db_session.flush()
    db_session.add(
        SystemDescription(
            org_id=org.id,
            system_name="Test system",
            system_type="minor_application",
            operational_status="operational",
            network_diagram_evidence_id=ev.id,
        )
    )

    # evidence_task_state_link — links a task to the control_state.
    task = EvidenceTask(org_id=org.id, title="Collect export", artifact_type="export")
    db_session.add(task)
    db_session.flush()
    db_session.add(EvidenceTaskStateLink(task_id=task.id, control_state_id=cs.id))

    # poa_m_item -> finding.
    finding = Finding(
        assessment_id=assessment.id,
        org_id=org.id,
        control_state_id=cs.id,
        title="Test finding",
        description="Test description",
        severity="low",
        finding_type="gap",
        status="open",
    )
    db_session.add(finding)
    db_session.flush()
    db_session.add(
        PoamItem(
            org_id=org.id,
            finding_id=finding.id,
            title="Remediate",
            description="Remediate the finding",
        )
    )

    db_session.flush()
    return org


@pytest.mark.integration
def test_reset_dev_succeeds_against_full_realistic_graph(db_session: Session):
    """The exact case that was broken: a test org with audit_log rows (plus
    the diagram/evidence-task-link/POA&M paths found alongside it) must not
    raise a foreign-key violation."""
    org = _seed_full_graph_org(db_session)

    _reset_dev(db_session)  # must not raise

    # _reset_dev() runs raw SQL DELETEs, which don't update the ORM's
    # identity map. session.get() on a PK it already has an (now-stale)
    # instance for raises ObjectDeletedError rather than returning None on
    # a miss -- a plain select() doesn't have that footgun, so use that for
    # every "is this row still there" check below, not .get().
    remaining_org = db_session.scalars(
        select(Organization).where(Organization.id == org.id)
    ).first()
    assert remaining_org is None
    remaining_audit = db_session.scalars(
        select(AuditLog).where(AuditLog.org_id == org.id)
    ).first()
    assert remaining_audit is None


@pytest.mark.integration
def test_reset_dev_preserves_acme_msp(db_session: Session):
    acme = Organization(name="Acme MSP")
    db_session.add(acme)
    db_session.flush()
    acme_id = acme.id

    other = _seed_full_graph_org(db_session)

    _reset_dev(db_session)

    remaining_acme = db_session.scalars(
        select(Organization).where(Organization.id == acme_id)
    ).first()
    assert remaining_acme is not None
    remaining_other = db_session.scalars(
        select(Organization).where(Organization.id == other.id)
    ).first()
    assert remaining_other is None


# ---------------------------------------------------------------------------
# Preview counts (2b): must match what actually gets deleted, since they're
# mechanically derived from the same _reset_dev_plan() rather than a
# hand-maintained parallel description -- see _as_count_sql.
# ---------------------------------------------------------------------------


class TestAsCountSql:
    """Pure string-surgery coverage for _as_count_sql -- no DB needed."""

    def test_delete_without_where(self):
        assert (
            _as_count_sql("DELETE FROM raci_assignment")
            == "SELECT COUNT(*) FROM raci_assignment"
        )

    def test_delete_with_where(self):
        sql = "DELETE FROM organization WHERE name != 'Acme MSP'"
        assert _as_count_sql(sql) == "SELECT COUNT(*) FROM organization WHERE name != 'Acme MSP'"

    def test_delete_with_nested_where_in_subquery(self):
        sql = (
            "DELETE FROM audit_log WHERE org_id IN ("
            "SELECT id FROM organization WHERE name != 'Acme MSP')"
        )
        assert _as_count_sql(sql) == (
            "SELECT COUNT(*) FROM audit_log WHERE org_id IN ("
            "SELECT id FROM organization WHERE name != 'Acme MSP')"
        )

    def test_update_with_where(self):
        sql = (
            "UPDATE system_description SET network_diagram_evidence_id = NULL "
            "WHERE network_diagram_evidence_id IS NOT NULL"
        )
        assert _as_count_sql(sql) == (
            "SELECT COUNT(*) FROM system_description "
            "WHERE network_diagram_evidence_id IS NOT NULL"
        )

    def test_unrecognized_statement_raises(self):
        with pytest.raises(ValueError, match="Don't know how to count"):
            _as_count_sql("TRUNCATE organization")


@pytest.mark.integration
def test_preview_counts_match_actual_deletion(db_session: Session):
    """The real assertion behind 2b: run the counts, run the reset, the
    numbers must be identical -- not just plausible."""
    acme = Organization(name="Acme MSP")
    db_session.add(acme)
    db_session.flush()
    _seed_full_graph_org(db_session, org=acme, framework_key="nist-800-171-r2")
    _seed_full_graph_org(db_session)  # a second, throwaway test org

    counts_before = _preview_counts(db_session)
    deleted = _reset_dev(db_session)

    assert counts_before == deleted
    # Sanity: this isn't a trivially-all-zero comparison.
    assert sum(deleted.values()) > 0


@pytest.mark.integration
def test_preview_counts_orgs_only_match_actual_deletion(db_session: Session):
    acme = Organization(name="Acme MSP")
    db_session.add(acme)
    db_session.flush()
    _seed_full_graph_org(db_session, org=acme, framework_key="nist-800-171-r2")
    _seed_full_graph_org(db_session)

    counts_before = _preview_counts(db_session, orgs_only=True)
    deleted = _reset_dev(db_session, orgs_only=True)

    assert counts_before == deleted
    assert sum(deleted.values()) > 0


# ---------------------------------------------------------------------------
# --orgs-only (2e): test orgs go away entirely; every other org's
# assessment layer -- including the kept org's -- is left alone.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_orgs_only_preserves_kept_orgs_assessment_layer(db_session: Session):
    acme = Organization(name="Acme MSP")
    db_session.add(acme)
    db_session.flush()
    _seed_full_graph_org(db_session, org=acme, framework_key="nist-800-171-r2")

    other = _seed_full_graph_org(db_session)

    acme_cs_before = db_session.scalar(
        select(func.count()).select_from(ControlState).where(ControlState.org_id == acme.id)
    )
    acme_ev_before = db_session.scalar(
        select(func.count()).select_from(Evidence).where(Evidence.org_id == acme.id)
    )
    assert acme_cs_before > 0
    assert acme_ev_before > 0

    deleted = _reset_dev(db_session, orgs_only=True)

    # Real assertion: the kept org's assessment layer is untouched.
    assert (
        db_session.scalar(
            select(func.count()).select_from(ControlState).where(ControlState.org_id == acme.id)
        )
        == acme_cs_before
    )
    assert (
        db_session.scalar(
            select(func.count()).select_from(Evidence).where(Evidence.org_id == acme.id)
        )
        == acme_ev_before
    )
    remaining_acme = db_session.scalars(
        select(Organization).where(Organization.id == acme.id)
    ).first()
    assert remaining_acme is not None

    # The test org is gone entirely -- identity and assessment layer both.
    remaining_other = db_session.scalars(
        select(Organization).where(Organization.id == other.id)
    ).first()
    assert remaining_other is None
    other_cs_remaining = db_session.scalar(
        select(func.count()).select_from(ControlState).where(ControlState.org_id == other.id)
    )
    assert other_cs_remaining == 0
    assert deleted["organization (test)"] == 1


# ---------------------------------------------------------------------------
# reset-dev's production guard: fails CLOSED (unset/unrecognized both
# refuse), matches this repo's established "development"/"production"
# WINGRC_ENVIRONMENT convention, and --yes never bypasses it.
# ---------------------------------------------------------------------------


class TestGuardError:
    """Pure-function coverage for _reset_dev_guard_error -- no DB, no CLI
    invocation, just the allowlist/normalization logic itself."""

    def test_unset_refuses(self):
        msg = _reset_dev_guard_error(None)
        assert msg is not None
        assert "unset" in msg
        assert "WINGRC_ENVIRONMENT" in msg

    def test_production_refuses(self):
        msg = _reset_dev_guard_error("production")
        assert msg is not None
        assert "'production'" in msg

    @pytest.mark.parametrize(
        "bad_value", ["prod", "PRODUCTION", "staging", "dev", "test", "local", "typo"]
    )
    def test_unrecognized_value_refuses(self, bad_value):
        assert _reset_dev_guard_error(bad_value) is not None

    @pytest.mark.parametrize(
        "good_value", ["development", "Development", "DEVELOPMENT", "  development  "]
    )
    def test_development_case_and_whitespace_insensitive_allows(self, good_value):
        assert _reset_dev_guard_error(good_value) is None


class TestResetDevCommandGuard:
    """Invocation-level coverage: the guard actually runs inside reset_dev()
    before anything DB-touching, and --yes cannot skip it. SessionLocal is
    monkeypatched so these never open a real connection -- a refused
    invocation should never call it at all (proving guard-before-session
    ordering), and an allowed one is stopped by a sentinel exception right
    after SessionLocal() so it can't reach out to a real database either.
    """

    runner = CliRunner()

    def test_refuses_on_unset_env_even_with_yes(self, monkeypatch):
        monkeypatch.delenv("WINGRC_ENVIRONMENT", raising=False)
        called = []
        monkeypatch.setattr("app.cli.SessionLocal", lambda: called.append(True))

        result = self.runner.invoke(cli_app, ["reset-dev", "--yes"])

        assert result.exit_code == 1
        assert "Refusing to run" in result.output
        assert called == []

    def test_refuses_on_production_even_with_yes(self, monkeypatch):
        monkeypatch.setenv("WINGRC_ENVIRONMENT", "production")
        called = []
        monkeypatch.setattr("app.cli.SessionLocal", lambda: called.append(True))

        result = self.runner.invoke(cli_app, ["reset-dev", "--yes"])

        assert result.exit_code == 1
        assert "Refusing to run" in result.output
        assert called == []

    def test_refuses_on_unrecognized_value(self, monkeypatch):
        monkeypatch.setenv("WINGRC_ENVIRONMENT", "staging")
        called = []
        monkeypatch.setattr("app.cli.SessionLocal", lambda: called.append(True))

        result = self.runner.invoke(cli_app, ["reset-dev", "--yes"])

        assert result.exit_code == 1
        assert "Refusing to run" in result.output
        assert called == []

    def test_proceeds_past_guard_for_development(self, monkeypatch):
        """Proves execution gets past the guard for the one allowed value --
        does not exercise the real reset (that's _reset_dev's own coverage
        above). SessionLocal raises a sentinel the instant it's called, so
        nothing here ever reaches a real database."""
        monkeypatch.setenv("WINGRC_ENVIRONMENT", "development")

        def _sentinel():
            raise RuntimeError("sentinel: passed guard")

        monkeypatch.setattr("app.cli.SessionLocal", _sentinel)

        result = self.runner.invoke(cli_app, ["reset-dev", "--yes"])

        assert "Refusing to run" not in result.output
        assert result.exception is not None
        assert "sentinel: passed guard" in str(result.exception)


# ---------------------------------------------------------------------------
# 2a: pre-flight pg_dump runs before anything is deleted, and a failed dump
# aborts the whole command without deleting anything -- no backup, no wipe.
# ---------------------------------------------------------------------------


class TestPreflightBackupGating:
    """Invocation-level coverage that a failed pre-flight backup stops
    reset-dev before _reset_dev() is ever called. Uses the real db_session
    fixture (not a SessionLocal sentinel) so "nothing was deleted" can be
    asserted against actual rows, not just "a function wasn't called."
    """

    runner = CliRunner()

    def test_backup_failure_aborts_without_deleting(self, monkeypatch, db_session: Session):
        monkeypatch.setenv("WINGRC_ENVIRONMENT", "development")
        monkeypatch.setattr("app.cli.SessionLocal", lambda: db_session)
        # reset_dev()'s `finally: session.close()` must not close the
        # shared fixture session out from under this test's own later use
        # of db_session (and the fixture's own teardown rollback).
        monkeypatch.setattr(db_session, "close", lambda: None)

        def _boom(_db_url):
            raise RuntimeError("pg_dump: simulated failure")

        monkeypatch.setattr("app.cli._preflight_backup", _boom)

        reset_dev_called = []
        monkeypatch.setattr(
            "app.cli._reset_dev", lambda *a, **k: reset_dev_called.append(True)
        )

        org = _seed_full_graph_org(db_session)
        # Commit (not just flush) so this seed data survives reset_dev()'s
        # own error-path `session.rollback()` below -- under this fixture's
        # create_savepoint mode, commit() only releases a SAVEPOINT (the
        # real transaction still rolls back at test teardown), but an
        # uncommitted flush() would be undone by that same rollback,
        # making "nothing was deleted" untestable.
        db_session.commit()

        result = self.runner.invoke(cli_app, ["reset-dev", "--yes"])

        assert result.exit_code == 1
        assert "Pre-flight backup failed" in result.output
        assert reset_dev_called == [], "the wipe must not run when the backup failed"

        # The real assertion: nothing was actually deleted.
        remaining = db_session.scalars(
            select(Organization).where(Organization.id == org.id)
        ).first()
        assert remaining is not None

    def test_backup_success_precedes_reset_dev_call(self, monkeypatch, db_session: Session):
        """Proves ordering -- backup, then delete -- not just that a
        failure aborts. A successful (stubbed) backup must be followed by
        the real _reset_dev() call."""
        monkeypatch.setenv("WINGRC_ENVIRONMENT", "development")
        monkeypatch.setattr("app.cli.SessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)

        order = []
        monkeypatch.setattr(
            "app.cli._preflight_backup",
            lambda _db_url: (order.append("backup"), Path("/tmp/fake.dump"))[1],
        )

        real_reset_dev = _reset_dev

        def _tracking_reset_dev(session, orgs_only=False):
            order.append("reset")
            return real_reset_dev(session, orgs_only)

        monkeypatch.setattr("app.cli._reset_dev", _tracking_reset_dev)

        result = self.runner.invoke(cli_app, ["reset-dev", "--yes"])

        assert result.exit_code == 0, result.output
        assert order == ["backup", "reset"]


@pytest.mark.integration
class TestPreflightBackupReal:
    """Exercises the real pg_dump binary against a real reachable Postgres
    (WINGRC_TEST_DATABASE_URL) -- the actual mechanism, not a stub. Only
    meaningful where that binary and a live server both exist (the
    container image built from backend/Dockerfile); skipped otherwise
    rather than failing a plain local run."""

    def test_creates_a_nonempty_dump_file(self, tmp_path, monkeypatch):
        test_url = os.environ.get("WINGRC_TEST_DATABASE_URL")
        if not test_url:
            pytest.skip("WINGRC_TEST_DATABASE_URL not set")
        monkeypatch.setenv("WINGRC_RESET_DEV_BACKUP_DIR", str(tmp_path))

        try:
            dest = _preflight_backup(make_url(test_url))
        except RuntimeError as e:
            # CI's `integration` job runs pytest directly on the bare
            # ubuntu-latest runner against a postgres:18 *service*
            # container -- unlike backend/Dockerfile (which copies a
            # version-matched pg_dump out of the postgres:18 image, see
            # that file's own comment), the runner's own system pg_dump is
            # whatever ubuntu-latest ships, which is typically older than
            # 18 and refuses to dump a newer server. That's a real gap in
            # this specific test environment, not in the shipped mechanism
            # -- the actual Docker image this ships in has already been
            # verified end-to-end (a real dump, a real restore into a
            # scratch database) on an isolated stack. Skip rather than
            # fail here, matching this class's own stated intent above;
            # any other pg_dump failure still fails the test for real.
            if "server version mismatch" in str(e):
                pytest.skip(f"pg_dump on this runner doesn't match the server: {e}")
            raise

        assert dest.parent == tmp_path
        assert dest.exists()
        assert dest.stat().st_size > 0
        assert dest.name.startswith("reset-dev-")

    def test_pg_dump_missing_raises_runtime_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WINGRC_RESET_DEV_BACKUP_DIR", str(tmp_path))
        monkeypatch.setenv("PATH", str(tmp_path))  # a PATH with no pg_dump on it

        with pytest.raises(RuntimeError, match="pg_dump"):
            _preflight_backup(make_url("postgresql://nobody:nowhere@localhost:1/nope"))


# ---------------------------------------------------------------------------
# 2d: --yes skips the confirmation *prompt*, not the reporting.
# ---------------------------------------------------------------------------


class TestYesPrintsSummary:
    runner = CliRunner()

    def test_yes_prints_deletion_summary(self, monkeypatch, db_session: Session):
        monkeypatch.setenv("WINGRC_ENVIRONMENT", "development")
        monkeypatch.setattr("app.cli.SessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)
        monkeypatch.setattr(
            "app.cli._preflight_backup", lambda _db_url: Path("/tmp/fake.dump")
        )

        _seed_full_graph_org(db_session)

        result = self.runner.invoke(cli_app, ["reset-dev", "--yes"])

        assert result.exit_code == 0, result.output
        assert "Proceed?" not in result.output  # prompt is skipped
        assert "Dev DB reset complete." in result.output
        assert "Rows affected:" in result.output
        assert "raci_assignment" in result.output  # a real per-tier line, not just a label
