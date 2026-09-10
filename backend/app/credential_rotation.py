"""DB adapter for crypto.py's key-rotation story (see that module's
docstring for the encrypt-with-primary/decrypt-with-any design this builds
on).

One entry point: `rotate_credential_keys(session, dry_run=True)`. Walks
every `IntegrationConnection` row with a stored credential, decrypts each
(crypto.decrypt_credential tries every configured key -- there is no
per-row "decrypt with exactly this row's own label" path, and none is
needed: MultiFernet trying every configured key in order produces the
identical plaintext a targeted single-key decrypt would, as long as the
row's original encrypting key is still configured at all -- which is
exactly the precondition either approach requires), and for any row not
already labeled with the current primary, re-encrypts under the primary
and updates the label.

Fail-closed, "change nothing" semantics: every row needing rotation is
decrypt-checked *before* any row is mutated. If even one fails, the whole
call returns with zero mutations applied and zero rows committed -- a
half-rotated table (some rows on the old key, some on the new) is exactly
the state the CLI's pre-flight pg_dump exists to make recoverable from,
but the adapter itself never produces it as an ordinary outcome.

dry_run=True (the default) runs the identical decrypt-check pass and
returns the identical report, then rolls back -- no row is touched, no
audit_log entry is written. This is not a separate code path from
dry_run=False; the same loop runs either way, gated only on whether the
mutation + log_event happen and which way the transaction ends. This
mirrors engine.py's backfill_missing_control_states() exactly, including
the reasoning.

Idempotent by construction: a row already labeled with the current
primary is never touched, so a second run against an already-rotated
table finds nothing to do.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .audit import log_event
from .crypto import (
    CredentialCipherError,
    current_primary_label,
    decrypt_credential,
    encrypt_credential,
)
from .models import IntegrationConnection


def rotate_credential_keys(session: Session, *, dry_run: bool = True) -> dict:
    """Returns:
        {
          "primary_label": str,
          "already_current": [connector_key, ...],   # no action needed
          "rotated": [
              {"connector_key": str, "old_label": str, "new_label": str},
              ...
          ],   # rotated (dry_run=False) or would-be-rotated (dry_run=True)
          "failed": [
              {"connector_key": str, "old_label": str | None, "error": str},
              ...
          ],
        }

    "failed" non-empty means the fail-closed path fired: "rotated" is
    always [] in that case, and dry_run=False still leaves every row
    exactly as it was found -- see module docstring.
    """
    primary_label = current_primary_label()

    rows = session.scalars(
        select(IntegrationConnection).where(IntegrationConnection.encrypted_credential.isnot(None))
    ).all()

    already_current: list[str] = []
    needs_rotation: list[IntegrationConnection] = []
    for row in rows:
        if row.credential_key_version == primary_label:
            already_current.append(row.connector_key)
        else:
            needs_rotation.append(row)

    decrypted: dict[str, str] = {}
    failed: list[dict] = []
    for row in needs_rotation:
        try:
            decrypted[row.connector_key] = decrypt_credential(row.encrypted_credential)
        except CredentialCipherError as e:
            failed.append(
                {
                    "connector_key": row.connector_key,
                    "old_label": row.credential_key_version,
                    "error": str(e),
                }
            )

    if failed:
        # Nothing was mutated above (the loop only decrypts, never
        # writes), but roll back explicitly anyway so a caller that
        # reuses this session afterward isn't left holding an open
        # transaction on a codepath that reports failure.
        session.rollback()
        return {
            "primary_label": primary_label,
            "already_current": already_current,
            "rotated": [],
            "failed": failed,
        }

    rotated: list[dict] = []
    for row in needs_rotation:
        old_label = row.credential_key_version
        rotated.append(
            {"connector_key": row.connector_key, "old_label": old_label, "new_label": primary_label}
        )
        if dry_run:
            continue
        ciphertext, new_label = encrypt_credential(decrypted[row.connector_key])
        row.encrypted_credential = ciphertext
        row.credential_key_version = new_label
        log_event(
            session,
            org_id=None,
            action="integration_connection.key_rotated",
            entity_type="integration_connection",
            entity_id=row.id,
            before_value={"connector_key": row.connector_key, "key_version": old_label},
            after_value={"connector_key": row.connector_key, "key_version": new_label},
            context={"via": "cli"},
            actor="system",
            actor_type="system",
        )

    if dry_run:
        session.rollback()
    else:
        session.commit()

    return {
        "primary_label": primary_label,
        "already_current": already_current,
        "rotated": rotated,
        "failed": [],
    }
