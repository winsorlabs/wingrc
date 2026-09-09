"""Unit tests for crypto.py's credential encryption (D.1).

No DB required — pure round-trip and fail-closed behavior against
Settings.credential_encryption_keys, patched via monkeypatch.setenv +
get_settings.cache_clear() (the pattern test_session_cap.py already uses).
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.config import get_settings
from app.crypto import CredentialCipherError, decrypt_credential, encrypt_credential

_KEY_A = Fernet.generate_key().decode()
_KEY_B = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_keys(monkeypatch, value: str | None):
    if value is None:
        monkeypatch.delenv("WINGRC_CREDENTIAL_ENCRYPTION_KEYS", raising=False)
    else:
        monkeypatch.setenv("WINGRC_CREDENTIAL_ENCRYPTION_KEYS", value)
    get_settings.cache_clear()


def test_round_trip(monkeypatch):
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    ciphertext, label = encrypt_credential("super-secret-value")
    assert label == "v1"
    assert ciphertext != "super-secret-value"
    assert decrypt_credential(ciphertext) == "super-secret-value"


def test_ciphertext_does_not_contain_plaintext(monkeypatch):
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    ciphertext, _ = encrypt_credential("access-key-secret-abc123")
    assert "access-key-secret-abc123" not in ciphertext


def test_fails_closed_when_key_unset(monkeypatch):
    _set_keys(monkeypatch, None)
    with pytest.raises(CredentialCipherError):
        encrypt_credential("value")
    with pytest.raises(CredentialCipherError):
        decrypt_credential("anything")


def test_fails_closed_when_key_blank(monkeypatch):
    _set_keys(monkeypatch, "   ")
    with pytest.raises(CredentialCipherError):
        encrypt_credential("value")


def test_fails_closed_when_key_malformed(monkeypatch):
    _set_keys(monkeypatch, "v1:not-a-valid-fernet-key")
    with pytest.raises(CredentialCipherError):
        encrypt_credential("value")


def test_fails_closed_when_entry_missing_label(monkeypatch):
    _set_keys(monkeypatch, _KEY_A)  # no "label:" prefix
    with pytest.raises(CredentialCipherError):
        encrypt_credential("value")


def test_rotation_new_primary_still_decrypts_old_ciphertext(monkeypatch):
    # Encrypt under the old primary (v1).
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    ciphertext, label = encrypt_credential("value")
    assert label == "v1"

    # Rotate: v2 becomes primary, v1 kept for decrypting old rows.
    _set_keys(monkeypatch, f"v2:{_KEY_B},v1:{_KEY_A}")
    assert decrypt_credential(ciphertext) == "value"

    # New encryptions now use the new primary.
    new_ciphertext, new_label = encrypt_credential("value2")
    assert new_label == "v2"
    assert decrypt_credential(new_ciphertext) == "value2"


def test_decrypt_fails_closed_when_key_retired(monkeypatch):
    _set_keys(monkeypatch, f"v1:{_KEY_A}")
    ciphertext, _ = encrypt_credential("value")

    # v1 fully dropped from config -- ciphertext can no longer be read.
    _set_keys(monkeypatch, f"v2:{_KEY_B}")
    with pytest.raises(CredentialCipherError):
        decrypt_credential(ciphertext)
