"""Preserve the asset acceptance record on delete.

asset_approval.scope_entity_id was ON DELETE CASCADE (0056) -- deleting a
scope_entity silently destroyed its own compliance acceptance record with
it. That was already questionable for what models.py:AssetApproval's own
docstring calls "the evidentiary artifact"; once the record is rendered on
the asset itself (routers/scope.py:get_scope_entity_approvals, the
"acceptance record on the asset" slice), it becomes the obvious next
question -- deleting the asset must not be able to make its own approval
history disappear without at least being told.

RESTRICT, not CASCADE, and not SET NULL. SET NULL (Jarrod's other option,
noted in the same request) needs scope_entity_id to become nullable plus a
denormalized natural_key/entity_type captured at approve/reject time so a
now-orphaned row still identifies which asset it was about -- a real
schema change and a new write path in liongard_sync.py, not a small one.
RESTRICT needs neither: it just refuses the delete, which
routers/scope.py:delete_scope_entity now catches and turns into a 409
telling the caller an approval record exists. No column changes, no new
migration for existing rows -- an unconditional ALTER of the one FK.

Revision ID: 0057_asset_approval_restrict_delete
Revises: 0056_liongard_sync_approval
Create Date: 2026-09-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0057_asset_approval_restrict_delete"
down_revision: str | None = "0056_liongard_sync_approval"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "asset_approval_scope_entity_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "asset_approval", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT, "asset_approval", "scope_entity", ["scope_entity_id"], ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "asset_approval", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT, "asset_approval", "scope_entity", ["scope_entity_id"], ["id"],
        ondelete="CASCADE",
    )
