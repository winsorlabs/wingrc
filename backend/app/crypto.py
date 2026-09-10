"""Symmetric encryption for third-party credentials stored at rest.

WinGRC is self-hosted (see CLAUDE.md's item-D rationale, and ROADMAP.md D.1):
the MSP runs its own Postgres, so an encrypted-at-rest Liongard key sitting
in it is the MSP holding its own credential, in the same box that already
holds its clients' CUI scoping data — not WinGRC-the-vendor holding a
customer's key. WinGRC-the-vendor never sees it. (A future hosted,
multi-tenant WinGRC would need a materially different answer — per-tenant
key derivation or KMS, not one deployment-wide key — tracked separately in
docs/roadmap.md; this module is a deliberate self-hosted-appropriate
design, not a permanent one.)

The encryption key is deploy-time config (WINGRC_CREDENTIAL_ENCRYPTION_KEYS
-> Settings.credential_encryption_keys), never persisted in the database.
Fail-closed, same posture as the reset-dev production guard: a missing or
malformed key refuses the operation outright — it never falls back to
storing or reading plaintext.

Config format: "label1:fernetkey1,label2:fernetkey2,...". The first entry
is the *primary* key — every new encryption uses it, and its label is
stored alongside the ciphertext (IntegrationConnection.credential_key_version)
so a rotation (prepend a new primary, keep old labels around for decrypt)
is a config change, not a data migration: MultiFernet tries every
configured key in order, so ciphertext written under a retired primary
keeps decrypting without a backfill, right up until that label is actually
dropped from the list.

That decrypt-with-any/encrypt-with-primary split is the whole rotation
story this module was designed to make cheap — but nothing here actually
re-encrypts existing rows onto the new primary; leaving old ciphertext on
a retired key indefinitely is exactly what makes fully dropping that key
unsafe later. `credential_rotation.py`'s `rotate_credential_keys()` (CLI:
`wingrc rotate-credential-keys`) is the re-encryption step: walks every
IntegrationConnection row, re-encrypts anything not already under the
current primary, and updates credential_key_version -- the part that
makes "drop the old label from this config string" actually safe to do.

Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .config import get_settings


class CredentialCipherError(RuntimeError):
    """Key config is missing/malformed, or ciphertext can't be decrypted
    with any configured key. Callers must treat this as fail-closed —
    never fall back to storing or reading plaintext on this error."""


def _parse_keys() -> list[tuple[str, str]]:
    raw = get_settings().credential_encryption_keys
    if not raw or not raw.strip():
        raise CredentialCipherError(
            "WINGRC_CREDENTIAL_ENCRYPTION_KEYS is not set — refusing to store "
            "or read third-party credentials without an encryption key."
        )
    pairs: list[tuple[str, str]] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise CredentialCipherError(
                f"Malformed WINGRC_CREDENTIAL_ENCRYPTION_KEYS entry {entry!r} — "
                "expected 'label:fernetkey'."
            )
        label, key = entry.split(":", 1)
        label, key = label.strip(), key.strip()
        if not label or not key:
            raise CredentialCipherError(
                f"Malformed WINGRC_CREDENTIAL_ENCRYPTION_KEYS entry {entry!r}."
            )
        pairs.append((label, key))
    if not pairs:
        raise CredentialCipherError("WINGRC_CREDENTIAL_ENCRYPTION_KEYS has no usable entries.")
    return pairs


def current_primary_label() -> str:
    """The label of the key encrypt_credential() would use right now --
    exposed on its own (no key material) so callers like
    credential_rotation.py can decide "does this row already carry the
    primary label" without reaching into _parse_keys() themselves. Raises
    CredentialCipherError under the same fail-closed conditions as every
    other function here (missing/malformed key config).
    """
    return _parse_keys()[0][0]


def encrypt_credential(plaintext: str) -> tuple[str, str]:
    """Encrypts with the primary (first-listed) key.

    Returns (ciphertext, key_version_label). Raises CredentialCipherError if
    the key config is missing or malformed — never returns plaintext.
    """
    label, key = _parse_keys()[0]
    try:
        fernet = Fernet(key.encode())
    except (ValueError, TypeError) as e:
        raise CredentialCipherError(f"Malformed Fernet key for label {label!r}: {e}") from e
    token = fernet.encrypt(plaintext.encode()).decode()
    return token, label


def decrypt_credential(ciphertext: str) -> str:
    """Tries every configured key in order (MultiFernet).

    Raises CredentialCipherError if the key config is missing/malformed, or
    if no configured key can decrypt the ciphertext (e.g. it was encrypted
    under a label that has since been fully retired from the config).
    """
    pairs = _parse_keys()
    try:
        fernets = [Fernet(key.encode()) for _, key in pairs]
    except (ValueError, TypeError) as e:
        raise CredentialCipherError(f"Malformed Fernet key in configuration: {e}") from e
    try:
        return MultiFernet(fernets).decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise CredentialCipherError(
            "Could not decrypt stored credential with any configured key."
        ) from e
