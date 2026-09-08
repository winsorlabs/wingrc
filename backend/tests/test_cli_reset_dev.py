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

Run in-container:
    docker compose exec backend pytest tests/test_cli_reset_dev.py -m integration -v
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli import _reset_dev, _reset_dev_guard_error
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


def _seed_full_graph_org(db_session: Session) -> Organization:
    """Build a throwaway org exercising every FK path `_reset_dev()` has to
    walk safely: an audit_log row, a system_description diagram pointer
    into evidence, an evidence_task_state_link row, and a poa_m_item that
    references a finding -- the four gaps this fix closes.
    """
    org = Organization(name=f"ResetDevTestOrg-{uuid.uuid4().hex[:8]}")
    fw = Framework(key=f"test-fw-{uuid.uuid4().hex[:8]}", name="Test FW", version="1")
    db_session.add_all([org, fw])
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
