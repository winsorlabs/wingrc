"""Tests for `wingrc rotate-credential-keys` (app/cli.py).

Mirrors test_cli_reset_dev.py's pre-flight-backup ordering tests --
_preflight_backup stubbed/tracked -- since this command reuses the same
helper and the same safety property matters here for the same reason: a
rotation that dies mid-way with some rows on the old key and some on the
new must be recoverable from a backup.

One deliberate difference from reset_dev's own tests: this command does
`from .db import SessionLocal as _SL` INSIDE the function body (matching
backfill-missing-control-states-cmd's own --db-url-override pattern), not
`SessionLocal()` via the name imported at module level into app.cli the
way reset_dev() does -- so the fixture session is substituted by patching
app.db.SessionLocal (what that local import actually re-reads at call
time), not app.cli.SessionLocal (which reset_dev() reads, but this
command never touches).

Run in-container:
    docker compose exec backend pytest tests/test_cli_rotate_credential_keys.py -m integration -v
"""
from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from app.cli import app as cli_app
from app.config import get_settings
from app.crypto import encrypt_credential
from app.models import IntegrationConnection

pytestmark = pytest.mark.integration

_KEY_A = Fernet.generate_key().decode()
_KEY_B = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_keys(monkeypatch, value: str) -> None:
    monkeypatch.setenv("WINGRC_CREDENTIAL_ENCRYPTION_KEYS", value)
    get_settings.cache_clear()


def _seed_connection(db_session, *, connector_key: str = "liongard") -> IntegrationConnection:
    ciphertext, label = encrypt_credential("some-secret")
    row = IntegrationConnection(
        connector_key=connector_key,
        config={},
        encrypted_credential=ciphertext,
        credential_key_version=label,
        credential_hint="cret",
    )
    db_session.add(row)
    db_session.flush()
    db_session.commit()  # survives this command's own error-path rollback
    return row


class TestPreflightBackupOrdering:
    runner = CliRunner()

    def test_backup_failure_aborts_without_rotating(self, monkeypatch, db_session: Session):
        _set_keys(monkeypatch, f"v1:{_KEY_A}")
        row = _seed_connection(db_session)
        _set_keys(monkeypatch, f"v2:{_KEY_B},v1:{_KEY_A}")

        monkeypatch.setattr("app.db.SessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)

        def _boom(_db_url):
            raise RuntimeError("pg_dump: simulated failure")

        monkeypatch.setattr("app.cli._preflight_backup", _boom)

        result = self.runner.invoke(cli_app, ["rotate-credential-keys", "--apply"])

        assert result.exit_code == 1, result.output
        assert "Pre-flight backup failed" in result.output

        # The real assertion: nothing was actually rotated.
        db_session.refresh(row)
        assert row.credential_key_version == "v1"

    def test_backup_success_precedes_rotation(self, monkeypatch, db_session: Session):
        """Proves ordering -- backup, then rotate -- not just that a
        successful (stubbed) backup is followed by a real write
        eventually. Tracks both steps in one shared list, mirroring
        test_cli_reset_dev.py's test_backup_success_precedes_reset_dev_call."""
        _set_keys(monkeypatch, f"v1:{_KEY_A}")
        row = _seed_connection(db_session)
        _set_keys(monkeypatch, f"v2:{_KEY_B},v1:{_KEY_A}")

        monkeypatch.setattr("app.db.SessionLocal", lambda: db_session)
        monkeypatch.setattr(db_session, "close", lambda: None)

        order = []
        monkeypatch.setattr(
            "app.cli._preflight_backup",
            lambda _db_url: (order.append("backup"), Path("/tmp/fake.dump"))[1],
        )

        import app.credential_rotation as credential_rotation_module

        real_rotate = credential_rotation_module.rotate_credential_keys

        def _tracking_rotate(session, *, dry_run):
            if not dry_run:
                order.append("rotate")
            return real_rotate(session, dry_run=dry_run)

        # The CLI command does `from .credential_rotation import
        # rotate_credential_keys` INSIDE the function body, so it re-reads
        # this module attribute at call time -- patching it here (not on
        # app.cli, which never holds a module-level reference to it) is
        # what actually gets picked up.
        monkeypatch.setattr(credential_rotation_module, "rotate_credential_keys", _tracking_rotate)

        result = self.runner.invoke(cli_app, ["rotate-credential-keys", "--apply"])

        assert result.exit_code == 0, result.output
        assert order == ["backup", "rotate"]
        db_session.refresh(row)
        assert row.credential_key_version == "v2", "rotation should have applied"


def test_dry_run_by_default_writes_nothing(monkeypatch, db_session: Session):
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    row = _seed_connection(db_session)
    _set_keys(monkeypatch, f"v2:{_KEY_B},v1:{_KEY_A}")

    monkeypatch.setattr("app.db.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    backup_called = []
    monkeypatch.setattr(
        "app.cli._preflight_backup", lambda _u: backup_called.append(True)
    )

    result = CliRunner().invoke(cli_app, ["rotate-credential-keys"])

    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output
    assert backup_called == [], "a dry run must never touch pg_dump"
    db_session.refresh(row)
    assert row.credential_key_version == "v1"


def test_nothing_to_rotate_skips_backup(monkeypatch, db_session: Session):
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    _seed_connection(db_session)
    # No second key added -- the seeded row is already on the (only,
    # therefore primary) configured key.

    monkeypatch.setattr("app.db.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    backup_called = []
    monkeypatch.setattr(
        "app.cli._preflight_backup", lambda _u: backup_called.append(True)
    )

    result = CliRunner().invoke(cli_app, ["rotate-credential-keys", "--apply"])

    assert result.exit_code == 0, result.output
    assert "Nothing to rotate" in result.output
    assert backup_called == [], "no write means no backup is needed"


def test_fail_closed_reports_and_exits_nonzero_without_attempting_backup(
    monkeypatch, db_session: Session
):
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    row = _seed_connection(db_session, connector_key="datto-rmm")
    # Rotate to a config that never included the key datto-rmm's row was
    # actually encrypted under -- it's now permanently undecryptable.
    _set_keys(monkeypatch, f"v2:{_KEY_B}")

    monkeypatch.setattr("app.db.SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    backup_called = []
    monkeypatch.setattr(
        "app.cli._preflight_backup", lambda _u: backup_called.append(True)
    )

    result = CliRunner().invoke(cli_app, ["rotate-credential-keys", "--apply"])

    assert result.exit_code == 1, result.output
    assert "datto-rmm" in result.output
    assert "FAILED to decrypt" in result.output
    assert backup_called == [], "must fail before ever reaching the backup step"

    db_session.refresh(row)
    assert row.credential_key_version == "v1", "nothing written on the fail-closed path"
