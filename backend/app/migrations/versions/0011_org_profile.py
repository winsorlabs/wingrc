"""Extend organization with profile fields for SSP header and bundle cover.

All new columns are nullable — an incomplete profile is valid; the bundle
export warns but does not refuse to generate.

CAGE code + UEI are mandatory SSP header fields for DoD contractor submissions
and are included now so the bundle schema is stable when it locks.

updated_at is added here alongside the profile columns so profile edits are
trackable without a separate migration.

Revision ID: 0011_org_profile
Revises: 0010_deactivation_and_audit
Create Date: 2026-07-10
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_org_profile"
down_revision: str | None = "0010_deactivation_and_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROFILE_COLUMNS = [
    sa.Column("cage_code", sa.String(10), nullable=True),
    sa.Column("uei", sa.String(20), nullable=True),
    sa.Column("year_established", sa.SmallInteger(), nullable=True),
    sa.Column("industry", sa.String(100), nullable=True),
    sa.Column("address_line1", sa.String(200), nullable=True),
    sa.Column("address_line2", sa.String(200), nullable=True),
    sa.Column("city", sa.String(100), nullable=True),
    sa.Column("state_or_province", sa.String(100), nullable=True),
    sa.Column("postal_code", sa.String(20), nullable=True),
    sa.Column("country", sa.String(60), nullable=True, server_default=sa.text("'US'")),
    sa.Column("phone_primary", sa.String(50), nullable=True),
    sa.Column("phone_secondary", sa.String(50), nullable=True),
    sa.Column("website", sa.String(400), nullable=True),
    sa.Column("logo_storage_key", sa.Text(), nullable=True),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=True,
        server_default=sa.text("now()"),
    ),
]


def upgrade() -> None:
    for col in _PROFILE_COLUMNS:
        op.add_column("organization", col)


def downgrade() -> None:
    for col in reversed(_PROFILE_COLUMNS):
        op.drop_column("organization", col.name)
