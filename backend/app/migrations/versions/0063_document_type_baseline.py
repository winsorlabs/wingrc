"""Add `baseline` to document.doc_type -- Lists slice, 2026-10-07.

An org-level baseline (e.g. "Windows 11 Workstation Baseline": headings, a
tool inventory, change management, a review cadence, tied to
CM.L2-3.4.1/3.4.2) is a narrative document and belongs in the library
beside policies and procedures. It is not the product baseline library
(Administration > Product Baselines), which is reference data about what a
vendor's product covers -- the shared word is what once sent the nav's
"Baselines" entry to the wrong feature.

Purely additive: widens a CHECK, touches no rows. Downgrade fails if any
baseline document exists, deliberately, rather than deleting it.
"""

from __future__ import annotations

from alembic import op

revision: str = "0063_document_type_baseline"
down_revision: str | None = "0062_document_approval_review"
branch_labels = None
depends_on = None

_OLD = "doc_type IN ('policy', 'procedure', 'plan', 'list', 'sop', 'form', 'other')"
_NEW = "doc_type IN ('policy', 'procedure', 'plan', 'baseline', 'list', 'sop', 'form', 'other')"


def upgrade() -> None:
    op.drop_constraint("ck_document_doc_type", "document", type_="check")
    op.create_check_constraint("ck_document_doc_type", "document", _NEW)


def downgrade() -> None:
    op.drop_constraint("ck_document_doc_type", "document", type_="check")
    op.create_check_constraint("ck_document_doc_type", "document", _OLD)
