"""Re-key scope_entity rows stuck on an OEM/BIOS placeholder serial.

_device_natural_key() (importers/liongard.py) preferred SerialNumber over
Hostname with no defense against known BIOS/OEM placeholder values --
"System Serial Number" (ASUS), "To Be Filled By O.E.M.", "Default string",
"None", "0123456789", "Not Applicable". Two devices sharing one of these
would reconcile as the same entity and one would silently vanish from the
scope graph -- see importers/liongard.py's own _PLACEHOLDER_SERIALS for
the fix that stops this going forward. This migration re-keys any row
already stuck there.

Confirmed live before writing this (docs/roadmap.md's "component
inventory and acceptance status" / device-identity entry has the full
survey): exactly one row, WinsorLabs' WL-DT26 ("Jarrods Desktop"), keyed
on the literal string "System Serial Number" -- no duplicate natural_key
ever existed anywhere (uq_scope_entity_identity would have refused one),
so nothing has actually been silently overwritten yet. This is a
preventive fix, not a repair of already-lost data.

Re-keying is a straight UPDATE of natural_key on the existing row --
asset_approval/review_cycle_item reference it by scope_entity_id (a
stable UUID), never by natural_key, so this cannot orphan an acceptance
record or re-queue an already-decided device (liongard_sync_result_change
isn't a persisted FK to scope_entity at all -- a natural-key text match
computed fresh at pull time; the next pull computes the same corrected
key via the code fix and reconciles this row as CHANGED/UNCHANGED, never
NEW again). uq_scope_entity_identity (org_id, entity_type, natural_key)
makes a collision with some other existing row a hard failure here, not
silent corruption, if one somehow exists.

A row with no usable Hostname to fall back to is left alone and this
migration raises instead of silently skipping it -- confirmed live that
this doesn't happen today (WL-DT26 has a Hostname), but if some future
deploy's data somehow has one, it needs a human, not a guess.

Revision ID: 0058_rekey_placeholder_serial
Revises: 0057_asset_approval_restrict
Create Date: 2026-09-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058_rekey_placeholder_serial"
down_revision: str | None = "0057_asset_approval_restrict"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors importers/liongard.py's _PLACEHOLDER_SERIALS exactly, as a
# separate literal here rather than an import -- migrations must never
# depend on application code that can change independently of schema
# history; a later edit to that constant must not silently rewrite what
# an already-applied migration did.
_PLACEHOLDER_SERIALS = (
    "system serial number",
    "to be filled by o.e.m.",
    "default string",
    "none",
    "0123456789",
    "not applicable",
)


def upgrade() -> None:
    conn = op.get_bind()
    stmt = sa.text(
        "SELECT id, natural_key, attributes->>'Hostname' AS hostname "
        "FROM scope_entity "
        "WHERE entity_type = 'device' AND lower(trim(natural_key)) IN :placeholders"
    ).bindparams(sa.bindparam("placeholders", expanding=True))
    rows = conn.execute(stmt, {"placeholders": list(_PLACEHOLDER_SERIALS)}).fetchall()

    for row in rows:
        hostname = (row.hostname or "").strip()
        if not hostname:
            raise RuntimeError(
                f"scope_entity {row.id} is keyed on placeholder serial "
                f"{row.natural_key!r} with no Hostname to fall back to -- needs a "
                "human, not this migration's guess."
            )
        conn.execute(
            sa.text(
                "UPDATE scope_entity SET natural_key = :hostname, updated_at = now() "
                "WHERE id = :id"
            ),
            {"hostname": hostname, "id": row.id},
        )


def downgrade() -> None:
    # Deliberately a no-op: the placeholder value carried no real identity
    # to restore, and by the time anyone downgrades, real activity
    # (approvals, RACI, evidence) may already reference this row under its
    # corrected key.
    pass
