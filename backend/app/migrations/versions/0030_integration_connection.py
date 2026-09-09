"""Integration connection (D.1 — Liongard connector credential storage).

Deployment-wide table (not org-scoped) — see models.py's IntegrationConnection
docstring for why: Liongard's API key is scoped to the whole MSP instance,
not per client, so there is one row per connector *type* per WinGRC
deployment, same tier as deployment_settings/product/framework. No RLS
policy is created here, matching those tables (only tables carrying a real
org_id column get one — see 0002_assessment_engine.py's `_enable_rls` call
sites).

encrypted_credential is write-only over the API (routers/integrations.py
never returns it) and encrypted at rest via crypto.py before it ever
reaches this column — see that module for the fail-closed key handling.

Revision ID: 0030_integration_connection
Revises: 0029_diagram_evidence_slots
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0030_integration_connection"
down_revision: str | None = "0029_diagram_evidence_slots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_connection",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("connector_key", sa.String(60), nullable=False, unique=True),
        sa.Column(
            "config", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("encrypted_credential", sa.Text(), nullable=True),
        sa.Column("credential_key_version", sa.String(40), nullable=True),
        sa.Column("credential_hint", sa.String(4), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("integration_connection")
