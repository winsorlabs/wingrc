"""Add SHA-256 hash column to evidence table.

Stores the SHA-256 hex digest of uploaded file bytes, computed at upload time
and carried into bundle exports for CMMC artifact hashing (DoD-CIO-00008).

Nullable by design:
  - kind='file' rows uploaded before this migration get their hash lazily on
    first bundle export (bytes already in MinIO; hash is computed from the
    fetched bytes and written back in the same transaction).
  - kind='reference' rows remain NULL permanently — no bytes to hash.

Revision ID: 0014_evidence_sha256
Revises: 0013_contact_documentation_roles
Create Date: 2026-07-13
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_evidence_sha256"
down_revision: str | None = "0013_contact_documentation_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evidence", sa.Column("sha256_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("evidence", "sha256_hash")
