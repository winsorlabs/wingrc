"""G.9 -- product_document: attach real files to a baseline library product.

Closes a provenance chain that was previously just a free-text claim: the
YAML's `source_docs:` field (e.g. `"RocketCyber_SIEM_and_SOC_Baseline.docx
(Winsors Labs MSP baseline, v1.0)"`) names a source document with no file
behind it anywhere in this app. This table lets an admin attach the actual
artifact -- the vendor's CRM, the MSP's own baseline doc, KB exports --
while `source_docs` stays exactly as authored (it's the record of what the
mapping was written from; the upload is the artifact, not a replacement
for that string). `source_docs_ref` is a free-text pointer to which
source_docs entry this upload corresponds to -- no relational FK, since
source_docs is an unstructured JSONB list on Product with no natural key
of its own.

Deployment-wide, like Product itself -- no org_id, no RLS. Follows
Evidence's storage-key conventions (models.py:Evidence) rather than
inventing new ones: storage_key encodes the id so path traversal isn't
possible via a crafted filename, the original name is kept in `title`.

Revision ID: 0036_product_document
Revises: 0035_org_liongard_environment
Create Date: 2026-09-12
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0036_product_document"
down_revision: str | None = "0035_org_liongard_environment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_document",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("product.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="other"),
        # Which source_docs[] string (Product-level free text) this upload
        # corresponds to -- a pointer, not a foreign key; see module docstring.
        sa.Column("source_docs_ref", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("sha256_hash", sa.String(64), nullable=True),
        sa.Column(
            "uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "kind IN ('crm', 'baseline_doc', 'kb_export', 'other')",
            name="ck_product_document_kind",
        ),
    )


def downgrade() -> None:
    op.drop_table("product_document")
