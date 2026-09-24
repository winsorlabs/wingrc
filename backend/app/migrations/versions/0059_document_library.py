"""Document library (roadmap item N, slice N.1): document, document_version,
document_objective_tag, plus evidence.source_document_version_id.

Versioned from day one -- see models.py:Document's own docstring for why
(roadmap item P's exact lesson: baseline mappings were built mutable, an
already-activated tenant's justification got silently rewritten by a later
reimport, and versioning had to be retrofitted across five tables and two
migrations afterward). A document IS the evidence, not a pointer to it, so
this migration creates document_version as append-only from the start --
there is no earlier, unversioned shape this ever had.

Ordering note: document.current_version_id and document_version.document_id
point at each other. document is created first WITHOUT current_version_id,
then document_version (which can now reference document.id), then
current_version_id is added to document by ALTER TABLE -- the exact same
resolution migration 0054 used for product.current_version_id /
product_baseline_version, for the identical reason (a straight-forward FK
can't be declared before the table it points at exists).

evidence.source_document_version_id is RESTRICT, explicit about it (unlike
evidence.source_product_id's implicit NO ACTION default) -- the same
"refuse to delete out from under a compliance record" precedent migration
0057 established for asset_approval.scope_entity_id. In practice
routers/documents.py never lets a document with any Evidence still
pointing at one of its versions be deleted at all, so this FK should never
actually have to refuse anything -- it exists as the same defense-in-depth
backstop that precedent already argued for.

Revision ID: 0059_document_library
Revises: 0058_rekey_placeholder_serial
Create Date: 2026-09-24
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0059_document_library"
down_revision: str | None = "0058_rekey_placeholder_serial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table}_tenant_isolation ON {table}
           USING (org_id = NULLIF(current_setting('app.current_org', true), '')::uuid)"""
    )


def upgrade() -> None:
    op.create_table(
        "document",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("doc_id", sa.String(60), nullable=False),
        sa.Column("doc_type", sa.String(20), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("cadence_months", sa.Integer(), nullable=False, server_default=sa.text("12")),
        sa.Column(
            "is_template_derived", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("template_ref", sa.String(200), nullable=True),
        # current_version_id added below by ALTER TABLE, once document_version exists.
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("org_id", "doc_id", name="uq_document_doc_id"),
        sa.CheckConstraint(
            "doc_type IN ('policy', 'procedure', 'plan', 'list', 'sop', 'form', 'other')",
            name="ck_document_doc_type",
        ),
    )
    op.create_index("ix_document_org_id", "document", ["org_id"])
    _rls("document")

    op.create_table(
        "document_version",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "document_id", UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.Text(), nullable=True),
        sa.Column(
            "is_template_derived", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("template_ref", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "approved_by_contact_id", UUID(as_uuid=True),
            sa.ForeignKey("contact.id"), nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "document_id", "version_number", name="uq_document_version_identity"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'under_review', 'approved', 'superseded')",
            name="ck_document_version_status",
        ),
    )
    op.create_index("ix_document_version_document_id", "document_version", ["document_id"])
    op.create_index("ix_document_version_org_id", "document_version", ["org_id"])
    _rls("document_version")

    op.add_column(
        "document",
        sa.Column(
            "current_version_id", UUID(as_uuid=True),
            sa.ForeignKey("document_version.id"), nullable=True,
        ),
    )
    op.create_index("ix_document_current_version_id", "document", ["current_version_id"])

    op.create_table(
        "document_objective_tag",
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column(
            "document_id", UUID(as_uuid=True),
            sa.ForeignKey("document.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column(
            "objective_id", UUID(as_uuid=True),
            sa.ForeignKey("assessment_objective.id"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("document_id", "objective_id", name="uq_document_objective_tag"),
    )
    op.create_index(
        "ix_document_objective_tag_document_id", "document_objective_tag", ["document_id"]
    )
    op.create_index("ix_document_objective_tag_org_id", "document_objective_tag", ["org_id"])
    op.create_index(
        "ix_document_objective_tag_objective_id", "document_objective_tag", ["objective_id"]
    )
    _rls("document_objective_tag")

    op.add_column(
        "evidence",
        sa.Column(
            "source_document_version_id", UUID(as_uuid=True),
            sa.ForeignKey("document_version.id", ondelete="RESTRICT"), nullable=True,
        ),
    )
    op.create_index(
        "ix_evidence_source_document_version_id", "evidence", ["source_document_version_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_evidence_source_document_version_id", table_name="evidence")
    op.drop_column("evidence", "source_document_version_id")
    op.drop_table("document_objective_tag")
    op.drop_index("ix_document_current_version_id", table_name="document")
    op.drop_column("document", "current_version_id")
    op.drop_table("document_version")
    op.drop_table("document")
