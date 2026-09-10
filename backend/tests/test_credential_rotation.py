"""Integration tests for credential_rotation.py's rotate_credential_keys().

Covers the task's explicit list:
  - round-trip under two keys (crypto.py-level, already covered by
    test_crypto.py's test_rotation_new_primary_still_decrypts_old_ciphertext
    -- not re-proven here)
  - after rotation, ciphertext carries the new primary's label and
    decrypts with that key alone (the old key fully removed from config)
  - idempotent: a second run finds nothing to do
  - fail-closed: a credential whose key is absent causes a reported
    failure and leaves EVERY row untouched, including ones that would
    otherwise have rotated cleanly -- "change nothing" is whole-run, not
    per-row
  - the audit_log entry carries key labels only, never key material or
    the credential plaintext

Requires a running Postgres database (WINGRC_TEST_DATABASE_URL); skipped
otherwise via the db_session fixture.
"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from app.config import get_settings
from app.credential_rotation import rotate_credential_keys
from app.crypto import decrypt_credential, encrypt_credential
from app.models import AuditLog, IntegrationConnection

pytestmark = pytest.mark.integration

_KEY_A = Fernet.generate_key().decode()
_KEY_B = Fernet.generate_key().decode()
_KEY_UNKNOWN = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_keys(monkeypatch, value: str) -> None:
    monkeypatch.setenv("WINGRC_CREDENTIAL_ENCRYPTION_KEYS", value)
    get_settings.cache_clear()


def _seed_connection(db_session, *, connector_key: str, plaintext: str) -> IntegrationConnection:
    """Encrypts under whatever key config is active right now -- caller
    sets that up first via _set_keys()."""
    ciphertext, label = encrypt_credential(plaintext)
    row = IntegrationConnection(
        connector_key=connector_key,
        config={},
        encrypted_credential=ciphertext,
        credential_key_version=label,
        credential_hint=plaintext[-4:],
    )
    db_session.add(row)
    db_session.flush()
    # Commit (not just flush): rotate_credential_keys()'s own
    # dry_run=True and fail-closed paths call session.rollback(), which
    # would otherwise expunge a flush-only row from the session/DB before
    # the test ever gets to assert against it.
    db_session.commit()
    return row


def test_rotation_updates_label_and_decrypts_under_new_key_alone(db_session, monkeypatch):
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    row = _seed_connection(db_session, connector_key="liongard", plaintext="old-secret")

    _set_keys(monkeypatch, f"v2:{_KEY_B},v1:{_KEY_A}")
    result = rotate_credential_keys(db_session, dry_run=False)

    assert result["failed"] == []
    assert result["rotated"] == [
        {"connector_key": "liongard", "old_label": "v1", "new_label": "v2"}
    ]

    db_session.refresh(row)
    assert row.credential_key_version == "v2"

    # The old key is now fully gone from config -- ciphertext must still
    # decrypt, proving the rotation actually moved it onto v2 rather than
    # just relabeling while leaving the old ciphertext in place.
    _set_keys(monkeypatch, f"v2:{_KEY_B}")
    assert decrypt_credential(row.encrypted_credential) == "old-secret"


def test_rotation_is_idempotent(db_session, monkeypatch):
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    _seed_connection(db_session, connector_key="liongard", plaintext="old-secret")

    _set_keys(monkeypatch, f"v2:{_KEY_B},v1:{_KEY_A}")
    first = rotate_credential_keys(db_session, dry_run=False)
    assert len(first["rotated"]) == 1

    second = rotate_credential_keys(db_session, dry_run=False)
    assert second["rotated"] == []
    assert second["already_current"] == ["liongard"]
    assert second["failed"] == []


def test_dry_run_writes_nothing(db_session, monkeypatch):
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    row = _seed_connection(db_session, connector_key="liongard", plaintext="old-secret")
    original_ciphertext = row.encrypted_credential

    _set_keys(monkeypatch, f"v2:{_KEY_B},v1:{_KEY_A}")
    result = rotate_credential_keys(db_session, dry_run=True)

    assert result["rotated"] == [
        {"connector_key": "liongard", "old_label": "v1", "new_label": "v2"}
    ]
    db_session.refresh(row)
    assert row.credential_key_version == "v1"
    assert row.encrypted_credential == original_ciphertext

    no_entries = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "integration_connection.key_rotated")
    ).all()
    assert no_entries == []


def test_fail_closed_leaves_every_row_untouched_when_one_credential_is_undecryptable(
    db_session, monkeypatch
):
    """Two rows: one encrypted under a key still in the config (would
    rotate cleanly on its own), one encrypted under a key that's been
    fully lost. The whole run must refuse -- including for the row that
    would otherwise have succeeded."""
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    good_row = _seed_connection(db_session, connector_key="liongard", plaintext="good-secret")

    _set_keys(monkeypatch, f"lost:{_KEY_UNKNOWN}")
    bad_row = _seed_connection(db_session, connector_key="datto-rmm", plaintext="orphaned-secret")

    good_ciphertext = good_row.encrypted_credential
    bad_ciphertext = bad_row.encrypted_credential

    # Rotate to v2, but the config never included _KEY_UNKNOWN at all --
    # bad_row's ciphertext can never be recovered under this config.
    _set_keys(monkeypatch, f"v2:{_KEY_B},v1:{_KEY_A}")

    for dry_run in (True, False):
        result = rotate_credential_keys(db_session, dry_run=dry_run)
        assert result["rotated"] == [], f"dry_run={dry_run}"
        assert len(result["failed"]) == 1, f"dry_run={dry_run}"
        assert result["failed"][0]["connector_key"] == "datto-rmm"

    db_session.refresh(good_row)
    db_session.refresh(bad_row)
    assert good_row.credential_key_version == "v1"
    assert good_row.encrypted_credential == good_ciphertext
    assert bad_row.credential_key_version == "lost"
    assert bad_row.encrypted_credential == bad_ciphertext

    no_entries = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "integration_connection.key_rotated")
    ).all()
    assert no_entries == []


def test_audit_log_entry_carries_labels_only_never_key_material_or_credential(
    db_session, monkeypatch
):
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    row = _seed_connection(
        db_session, connector_key="liongard", plaintext=json.dumps({"access_key_secret": "s3cr3t"})
    )

    _set_keys(monkeypatch, f"v2:{_KEY_B},v1:{_KEY_A}")
    rotate_credential_keys(db_session, dry_run=False)

    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "integration_connection.key_rotated")
    ).one()
    assert entry.entity_id == row.id
    assert entry.before_value == {"connector_key": "liongard", "key_version": "v1"}
    assert entry.after_value == {"connector_key": "liongard", "key_version": "v2"}
    assert entry.actor == "system"
    assert entry.actor_type == "system"

    blob = json.dumps(entry.before_value) + json.dumps(entry.after_value)
    assert "s3cr3t" not in blob
    assert _KEY_A not in blob
    assert _KEY_B not in blob
