"""AI research of vendor platform documentation: allow product_document.kind
= 'web_research' for pages fetched by importers/research.py's SSRF-safe
fetcher, alongside the existing crm/baseline_doc/kb_export/other kinds.

No new columns -- see models.py:ProductDocument's own docstring on
kind='web_research' for why the existing title/source_docs_ref/uploaded_at
shape already carries everything (page title, fetched URL, retrieval
timestamp) this new kind needs.

Revision ID: 0053_product_doc_web_research
Revises: 0052_control_state_contributor
Create Date: 2026-09-17
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0053_product_doc_web_research"
down_revision: str | None = "0052_control_state_contributor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CONSTRAINT = "kind IN ('crm', 'baseline_doc', 'kb_export', 'other')"
_NEW_CONSTRAINT = "kind IN ('crm', 'baseline_doc', 'kb_export', 'other', 'web_research')"


def upgrade() -> None:
    op.drop_constraint("ck_product_document_kind", "product_document", type_="check")
    op.create_check_constraint("ck_product_document_kind", "product_document", _NEW_CONSTRAINT)


def downgrade() -> None:
    # Any row already saved as kind='web_research' would violate the
    # narrower constraint -- reassign those to 'other' first so downgrade
    # never fails with a stale row it can no longer represent. This is
    # lossy in the same deliberate way every kind-based downgrade in this
    # codebase already is: the artifact and its title/source_docs_ref/
    # uploaded_at survive intact, only the specific "this came from web
    # research" distinction is lost.
    op.execute("UPDATE product_document SET kind = 'other' WHERE kind = 'web_research'")
    op.drop_constraint("ck_product_document_kind", "product_document", type_="check")
    op.create_check_constraint("ck_product_document_kind", "product_document", _OLD_CONSTRAINT)
