# noqa: B008
"""Deployment-tier baseline library management (G.9).

Endpoints:
  GET    /admin/products                              Library view
  GET    /admin/products/{product_id}                 Tool detail (baseline mapping, read-only)
  GET    /admin/products/{product_id}/footprint       Which orgs have it, at what status
  POST   /admin/products/import/dry-run               Validate + preview a baseline YAML upload
  POST   /admin/products/import/apply                 Write a validated import (is_published=False)
  POST   /admin/products/{product_id}/publish         Expose to tenants
  POST   /admin/products/{product_id}/unpublish       Hide from tenants
  GET    /admin/products/{product_id}/documents       List attached documents
  POST   /admin/products/{product_id}/documents       Upload one
  GET    /admin/products/{product_id}/documents/{document_id}/download  Redirect to presigned URL
  DELETE /admin/products/{product_id}/documents/{document_id}  Remove

Role gate: require_role("msp_admin", "consultant_admin"), router-wide,
matching routers/integrations.py exactly -- both are deployment-wide
compliance-data configuration, not identity administration. See this
file's own "Read on consultant_admin" note below for why this one is a
materially stronger version of that same tension, reported rather than
resolved -- the gate is unchanged here on purpose; Jarrod decides.

This screen manages the library only. It never writes OrgProduct --
activation stays exactly where it is (routers/assessments.py,
engine.py:activate_org_product), fired from inside an org. See
ROADMAP.md/docs/PLAN-gui-restructure.md's G.9 entry for the "why not
assignment from here" reasoning.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
import tempfile
import uuid
from datetime import UTC, datetime
from typing import Any

import yaml as _yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..ai import get_ai_provider
from ..audit import log_event
from ..auth import CurrentUser, actor_type_for, require_role
from ..baseline import to_yaml_dict
from ..baseline_import import apply_import as _apply_baseline_import
from ..baseline_import import build_preview, parse_yaml, validate
from ..crypto import CredentialCipherError
from ..db import get_session
from ..importers.document import DocumentIngestError, ingest_document
from ..models import (
    BaselineControl,
    BaselineEvidenceSpec,
    Control,
    Framework,
    Product,
    ProductDocument,
)
from ..routers.evidence import (
    _ALLOWED_EXTENSIONS,
    _ALLOWED_MIME_TYPES,
    _MAX_FILE_BYTES,
    _safe_filename,
    _verify_magic_bytes,
)
from ..seeds.baselines import _FRAMEWORK_KEY
from ..storage import StorageClient, download_filename, get_storage_client

router = APIRouter(
    prefix="/admin/products",
    tags=["admin-products"],
    dependencies=[Depends(require_role("msp_admin", "consultant_admin"))],
)

_VALID_DOCUMENT_KINDS = frozenset({"crm", "baseline_doc", "kb_export", "other"})

# Narrower than routers/evidence.py's general evidence-upload allowlist --
# this endpoint only ever feeds documents to importers/document.py's text
# extraction, which only understands PDF and Word.
_INGEST_ALLOWED_EXTENSIONS = frozenset({".pdf", ".doc", ".docx"})
_INGEST_ALLOWED_MIME_TYPES = frozenset({
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})
_MAX_INGEST_FILES = 2


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ProductLibraryOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    provider: str
    category: str
    asset_type: str
    framework_name: str
    is_published: bool
    control_count: int
    objective_count: int


class EvidenceSpecOut(BaseModel):
    id: uuid.UUID
    artifact_description: str
    evidence_type: str
    kb_reference: str | None


class BaselineControlOut(BaseModel):
    control_id: str
    family: str
    title: str
    objectives: list[str]
    classification: str
    coverage_basis: str
    candidate_state: str
    provider_contribution: str | None
    customer_action: str | None
    note: str | None
    scope_note: str | None
    evidence_specs: list[EvidenceSpecOut]


class ProductDocumentOut(BaseModel):
    id: uuid.UUID
    title: str
    kind: str
    source_docs_ref: str | None
    mime_type: str | None
    file_size_bytes: int | None
    uploaded_at: datetime


class ProductDetailOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    provider: str
    category: str
    asset_type: str
    role: str
    assumed_config: list[str]
    is_published: bool
    source_docs: list[str] = Field(default_factory=list)
    # Permanent AI-generation provenance (models.py:Product) -- non-null
    # forever once an ingestion sets them, never cleared by a later
    # hand-edited re-import. See ai_generated_at's own docstring on the
    # model for why this mirrors practitioner_notes_generated_at/_model
    # rather than a dismissible flag.
    ai_generated_at: datetime | None = None
    ai_generated_model: str | None = None
    baseline_controls: list[BaselineControlOut]
    documents: list[ProductDocumentOut]


class FootprintRowOut(BaseModel):
    org_id: uuid.UUID
    org_name: str
    status: str


class ControlChangeOut(BaseModel):
    control_id: str
    change_type: str
    classification: str
    coverage_basis: str
    field_diffs: dict[str, list[Any]]


class ImportPreviewOut(BaseModel):
    problems: list[str]
    product_key: str
    product_is_new: bool
    product_name: str
    control_changes: list[ControlChangeOut]
    affected_org_count: int
    affected_org_names: list[str]


class ImportApplyOut(BaseModel):
    product_id: uuid.UUID
    product_key: str
    baseline_controls: int
    evidence_specs: int


class DocumentIngestOut(BaseModel):
    yaml: str
    preview: ImportPreviewOut


class PublishOut(BaseModel):
    id: uuid.UUID
    is_published: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_framework_and_controls(session: Session) -> tuple[Framework | None, dict[str, Control]]:
    fw = session.scalars(select(Framework).where(Framework.key == _FRAMEWORK_KEY)).first()
    if fw is None:
        return None, {}
    ctrl_lookup = {
        c.control_id: c
        for c in session.scalars(select(Control).where(Control.framework_id == fw.id))
    }
    return fw, ctrl_lookup


def _document_out(d: ProductDocument) -> ProductDocumentOut:
    return ProductDocumentOut(
        id=d.id,
        title=d.title,
        kind=d.kind,
        source_docs_ref=d.source_docs_ref,
        mime_type=d.mime_type,
        file_size_bytes=d.file_size_bytes,
        uploaded_at=d.uploaded_at,
    )


def _get_product(session: Session, product_id: uuid.UUID) -> Product:
    product = session.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


# ---------------------------------------------------------------------------
# Library view
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ProductLibraryOut])
def list_products(session: Session = Depends(get_session)) -> list[ProductLibraryOut]:
    products = session.scalars(select(Product).order_by(Product.name)).all()
    if not products:
        return []
    product_ids = [p.id for p in products]
    fw_names = {
        f.id: f.name
        for f in session.scalars(
            select(Framework).where(Framework.id.in_({p.framework_id for p in products}))
        )
    }
    control_counts = dict(
        session.execute(
            select(BaselineControl.product_id, func.count())
            .where(BaselineControl.product_id.in_(product_ids))
            .group_by(BaselineControl.product_id)
        ).all()
    )
    objective_counts = dict(
        session.execute(
            select(
                BaselineControl.product_id,
                func.coalesce(func.sum(func.jsonb_array_length(BaselineControl.objectives)), 0),
            )
            .where(BaselineControl.product_id.in_(product_ids))
            .group_by(BaselineControl.product_id)
        ).all()
    )
    return [
        ProductLibraryOut(
            id=p.id,
            key=p.key,
            name=p.name,
            provider=p.provider,
            category=p.category,
            asset_type=p.asset_type,
            framework_name=fw_names.get(p.framework_id, ""),
            is_published=p.is_published,
            control_count=control_counts.get(p.id, 0),
            objective_count=int(objective_counts.get(p.id, 0)),
        )
        for p in products
    ]


@router.get("/{product_id}", response_model=ProductDetailOut)
def get_product_detail(
    product_id: uuid.UUID, session: Session = Depends(get_session)
) -> ProductDetailOut:
    product = _get_product(session, product_id)
    bcs = session.scalars(
        select(BaselineControl).where(BaselineControl.product_id == product_id)
    ).all()
    controls = {
        c.id: c
        for c in session.scalars(
            select(Control).where(Control.id.in_({bc.control_id for bc in bcs}))
        )
    } if bcs else {}
    specs_by_bc: dict[uuid.UUID, list[BaselineEvidenceSpec]] = {}
    if bcs:
        for spec in session.scalars(
            select(BaselineEvidenceSpec).where(
                BaselineEvidenceSpec.baseline_control_id.in_([bc.id for bc in bcs])
            )
        ):
            specs_by_bc.setdefault(spec.baseline_control_id, []).append(spec)
    docs = session.scalars(
        select(ProductDocument)
        .where(ProductDocument.product_id == product_id)
        .order_by(ProductDocument.uploaded_at)
    ).all()

    def _sort_key(bc: BaselineControl) -> str:
        ctrl = controls.get(bc.control_id)
        return ctrl.control_id if ctrl else ""

    baseline_out = []
    for bc in sorted(bcs, key=_sort_key):
        ctrl = controls.get(bc.control_id)
        baseline_out.append(
            BaselineControlOut(
                control_id=ctrl.control_id if ctrl else str(bc.control_id),
                family=ctrl.family if ctrl else "",
                title=ctrl.title if ctrl else "",
                objectives=bc.objectives or [],
                classification=bc.classification,
                coverage_basis=bc.coverage_basis,
                candidate_state=bc.candidate_state,
                provider_contribution=bc.provider_contribution,
                customer_action=bc.customer_action,
                note=bc.note,
                scope_note=bc.scope_note,
                evidence_specs=[
                    EvidenceSpecOut(
                        id=s.id,
                        artifact_description=s.artifact_description,
                        evidence_type=s.evidence_type,
                        kb_reference=s.kb_reference,
                    )
                    for s in specs_by_bc.get(bc.id, [])
                ],
            )
        )

    return ProductDetailOut(
        id=product.id,
        key=product.key,
        name=product.name,
        provider=product.provider,
        category=product.category,
        asset_type=product.asset_type,
        role=product.role,
        assumed_config=product.assumed_config or [],
        is_published=product.is_published,
        source_docs=product.source_docs or [],
        ai_generated_at=product.ai_generated_at,
        ai_generated_model=product.ai_generated_model,
        baseline_controls=baseline_out,
        documents=[_document_out(d) for d in docs],
    )


@router.get("/{product_id}/footprint", response_model=list[FootprintRowOut])
def get_product_footprint(
    product_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[FootprintRowOut]:
    """Cross-org read via auth.product_deployment_footprint() (migration
    0037) -- org_product carries RLS, and this view's whole point is
    seeing every org's row for one product in a single query, which no
    per-request app.current_org value can express. Same SECURITY DEFINER
    pattern as M.2/M.5.
    """
    _get_product(session, product_id)
    rows = session.execute(
        text("SELECT org_id, org_name, status FROM auth.product_deployment_footprint(:pid)"),
        {"pid": product_id},
    ).all()
    return [FootprintRowOut(org_id=r.org_id, org_name=r.org_name, status=r.status) for r in rows]


# ---------------------------------------------------------------------------
# Import: dry-run (no writes) then apply (revalidated, then written)
# ---------------------------------------------------------------------------


def _empty_preview(problem: str) -> ImportPreviewOut:
    return ImportPreviewOut(
        problems=[problem],
        product_key="",
        product_is_new=True,
        product_name="",
        control_changes=[],
        affected_org_count=0,
        affected_org_names=[],
    )


@router.post("/import/dry-run", response_model=ImportPreviewOut)
async def import_dry_run(
    file: UploadFile = File(...), session: Session = Depends(get_session)
) -> ImportPreviewOut:
    raw = await file.read()
    data, err = parse_yaml(raw)
    if err:
        return _empty_preview(err)

    fw, ctrl_lookup = _load_framework_and_controls(session)
    if fw is None:
        return _empty_preview(
            f"Framework '{_FRAMEWORK_KEY}' not found -- run 'wingrc seed-catalog' first."
        )

    preview = build_preview(session, data, ctrl_lookup)
    return _preview_out(preview)


@router.post("/import/apply", response_model=ImportApplyOut, status_code=201)
async def import_apply(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_role("msp_admin", "consultant_admin")),
) -> ImportApplyOut:
    """Revalidates before writing -- see baseline_import.py's module
    docstring for why this isn't a formality (dry-run and apply are two
    independent stateless calls; nothing ties apply to a prior dry-run
    having actually happened).
    """
    raw = await file.read()
    data, err = parse_yaml(raw)
    if err:
        raise HTTPException(status_code=422, detail=err)

    fw, ctrl_lookup = _load_framework_and_controls(session)
    if fw is None:
        raise HTTPException(
            status_code=409,
            detail=f"Framework '{_FRAMEWORK_KEY}' not found -- run 'wingrc seed-catalog' first.",
        )

    problems = validate(session, data, ctrl_lookup)
    if problems:
        raise HTTPException(status_code=422, detail={"problems": problems})

    result = _apply_baseline_import(session, data, ctrl_lookup, fw)
    product = session.scalars(
        select(Product).where(Product.key == data["product"]["key"])
    ).one()

    log_event(
        session,
        org_id=None,
        action="product.import",
        entity_type="product",
        entity_id=product.id,
        after_value={
            "key": product.key,
            "baseline_controls": result["baseline_controls"],
            "evidence_specs": result["evidence_specs"],
            "is_published": product.is_published,
        },
        context={"via": "api"},
        actor=str(current_user.id),
        actor_type=actor_type_for(current_user),
    )
    session.commit()
    return ImportApplyOut(
        product_id=product.id,
        product_key=product.key,
        baseline_controls=result["baseline_controls"],
        evidence_specs=result["evidence_specs"],
    )


def _preview_out(preview) -> ImportPreviewOut:
    return ImportPreviewOut(
        problems=preview.problems,
        product_key=preview.product_key,
        product_is_new=preview.product_is_new,
        product_name=preview.product_name,
        control_changes=[
            ControlChangeOut(
                control_id=c.control_id,
                change_type=c.change_type,
                classification=c.classification,
                coverage_basis=c.coverage_basis,
                field_diffs={k: list(v) for k, v in c.field_diffs.items()},
            )
            for c in preview.control_changes
        ],
        affected_org_count=preview.affected_org_count,
        affected_org_names=preview.affected_org_names,
    )


@router.post("/import/from-documents", response_model=DocumentIngestOut)
async def import_from_documents(
    files: list[UploadFile] = File(...),
    product_key: str = Form(...),
    category: str = Form("ESP"),
    asset_type: str = Form("SPA"),
    framework: str = Form("NIST 800-171 Rev 2 / CMMC L2"),
    session: Session = Depends(get_session),
) -> DocumentIngestOut:
    """Run the document-ingestion pipeline (importers/document.py) against
    1-2 uploaded vendor documents and feed its output through the SAME
    dry-run preview path a hand-authored YAML upload uses
    (baseline_import.build_preview) -- this endpoint writes nothing.
    /import/apply (above) is still the only write path, and it still forces
    is_published=False on any import via reset_published=True, AI-sourced
    or not -- nothing an AI produces reaches a tenant without a human
    reviewing this exact preview and then calling apply.

    coverage_basis is deliberately left unset by the ingestion pipeline for
    every provider_satisfies/shared entry, so the returned preview will
    always report a problem for those until a reviewer edits the YAML to
    set it -- see baseline.py:ControlEntry.coverage_basis's docstring for
    why that gap is intentional, not a bug.
    """
    if not (1 <= len(files) <= _MAX_INGEST_FILES):
        raise HTTPException(
            status_code=422,
            detail=f"Upload 1 to {_MAX_INGEST_FILES} documents (CRM and/or MSP baseline).",
        )
    if not product_key.strip():
        raise HTTPException(status_code=422, detail="product_key is required.")

    tmp_paths: list[str] = []
    try:
        for f in files:
            raw_name = _safe_filename(f.filename or "upload")
            ext = os.path.splitext(raw_name)[1].lower()
            if ext not in _INGEST_ALLOWED_EXTENSIONS:
                raise HTTPException(
                    status_code=415,
                    detail=(
                        f"File extension {ext!r} not permitted. "
                        f"Allowed: {sorted(_INGEST_ALLOWED_EXTENSIONS)}"
                    ),
                )
            data = await f.read()
            if len(data) > _MAX_FILE_BYTES:
                raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")
            mime = (
                f.content_type
                or mimetypes.guess_type(raw_name)[0]
                or "application/octet-stream"
            )
            if mime not in _INGEST_ALLOWED_MIME_TYPES:
                raise HTTPException(
                    status_code=415,
                    detail=(
                        f"Content-Type {mime!r} not permitted. "
                        f"Allowed: {sorted(_INGEST_ALLOWED_MIME_TYPES)}"
                    ),
                )
            if not _verify_magic_bytes(data, mime):
                raise HTTPException(
                    status_code=415,
                    detail=f"File bytes do not match declared Content-Type {mime!r}",
                )
            fd, path = tempfile.mkstemp(suffix=ext)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            tmp_paths.append(path)

        try:
            ai_provider = get_ai_provider(session)
        except CredentialCipherError as exc:
            # A deployment-level key problem (WINGRC_CREDENTIAL_ENCRYPTION_KEYS
            # missing/rotated away from what encrypted the stored credential)
            # -- distinct from "not configured" (a clean 422 below) and from
            # a bad key at the provider itself (also a 422, raised inside
            # ingest_document below). This one is an operator/deployment
            # fault, not something a reviewer fixes by re-entering a key.
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except RuntimeError as exc:
            # The stored config itself is unusable (ai/__init__.py's state
            # 3 -- decrypts fine, but parse_settings rejects it, e.g. an
            # unsupported provider value). Not the same message as "no
            # credential at all" (NullProvider's RuntimeError, surfaced via
            # DocumentIngestError below) or a call-time provider failure
            # (also below) -- str(exc) already names the specific problem.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            entry = ingest_document(
                *tmp_paths,
                product_key=product_key.strip(),
                ai_provider=ai_provider,
                category=category,
                asset_type=asset_type,
                framework=framework,
            )
        except DocumentIngestError as exc:
            # Covers the ai_provider="none" case too (importers/document.py
            # normalizes NullProvider's RuntimeError into this) -- clean,
            # specific 422, never a raw 500.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        for p in tmp_paths:
            try:
                os.unlink(p)
            except OSError:
                pass

    entry.product.ai_generated_at = datetime.now(UTC).isoformat()
    entry.product.ai_generated_model = ai_provider.identity
    data_dict = to_yaml_dict(entry)

    fw, ctrl_lookup = _load_framework_and_controls(session)
    if fw is None:
        raise HTTPException(
            status_code=409,
            detail=f"Framework '{_FRAMEWORK_KEY}' not found -- run 'wingrc seed-catalog' first.",
        )
    preview = build_preview(session, data_dict, ctrl_lookup)

    return DocumentIngestOut(
        yaml=_yaml.safe_dump(data_dict, sort_keys=False),
        preview=_preview_out(preview),
    )


# ---------------------------------------------------------------------------
# Publish / unpublish
# ---------------------------------------------------------------------------


@router.post("/{product_id}/publish", response_model=PublishOut)
def publish_product(
    product_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_role("msp_admin", "consultant_admin")),
) -> PublishOut:
    product = _get_product(session, product_id)
    was_published = product.is_published
    product.is_published = True
    log_event(
        session,
        org_id=None,
        action="product.publish",
        entity_type="product",
        entity_id=product.id,
        before_value={"is_published": was_published},
        after_value={"is_published": True},
        context={"via": "api"},
        actor=str(current_user.id),
        actor_type=actor_type_for(current_user),
    )
    session.commit()
    return PublishOut(id=product.id, is_published=True)


@router.post("/{product_id}/unpublish", response_model=PublishOut)
def unpublish_product(
    product_id: uuid.UUID,
    session: Session = Depends(get_session),
    current_user: CurrentUser = Depends(require_role("msp_admin", "consultant_admin")),
) -> PublishOut:
    product = _get_product(session, product_id)
    was_published = product.is_published
    product.is_published = False
    log_event(
        session,
        org_id=None,
        action="product.unpublish",
        entity_type="product",
        entity_id=product.id,
        before_value={"is_published": was_published},
        after_value={"is_published": False},
        context={"via": "api"},
        actor=str(current_user.id),
        actor_type=actor_type_for(current_user),
    )
    session.commit()
    return PublishOut(id=product.id, is_published=False)


# ---------------------------------------------------------------------------
# Documentation attachments
# ---------------------------------------------------------------------------


@router.get("/{product_id}/documents", response_model=list[ProductDocumentOut])
def list_documents(
    product_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[ProductDocumentOut]:
    _get_product(session, product_id)
    docs = session.scalars(
        select(ProductDocument)
        .where(ProductDocument.product_id == product_id)
        .order_by(ProductDocument.uploaded_at)
    ).all()
    return [_document_out(d) for d in docs]


@router.post("/{product_id}/documents", response_model=ProductDocumentOut, status_code=201)
async def upload_document(
    product_id: uuid.UUID,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    kind: str = Form("other"),
    source_docs_ref: str | None = Form(None),
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
    current_user: CurrentUser = Depends(require_role("msp_admin", "consultant_admin")),
) -> ProductDocumentOut:
    _get_product(session, product_id)
    if kind not in _VALID_DOCUMENT_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of: {sorted(_VALID_DOCUMENT_KINDS)}",
        )

    data = await file.read()
    if len(data) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 50 MB limit")

    raw_name = _safe_filename(file.filename or "upload")
    ext = os.path.splitext(raw_name)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"File extension {ext!r} not permitted. Allowed: {sorted(_ALLOWED_EXTENSIONS)}",
        )

    mime = file.content_type or mimetypes.guess_type(raw_name)[0] or "application/octet-stream"
    if mime not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Content-Type {mime!r} not permitted. Allowed: {sorted(_ALLOWED_MIME_TYPES)}",
        )
    if not _verify_magic_bytes(data, mime):
        raise HTTPException(
            status_code=415, detail=f"File bytes do not match declared Content-Type {mime!r}"
        )

    display_title = title or raw_name
    doc_id = uuid.uuid4()
    # Deployment-wide (no org_id), unlike Evidence's per-org key -- Product
    # is deployment-wide data too. Store under UUID to prevent path
    # traversal; original name kept in title (matches Evidence's own
    # convention exactly).
    storage_key = f"products/{product_id}/{doc_id}/{doc_id}{ext}"
    storage.upload_file(storage_key, data, mime)

    doc = ProductDocument(
        id=doc_id,
        product_id=product_id,
        title=display_title,
        kind=kind,
        source_docs_ref=source_docs_ref,
        storage_key=storage_key,
        mime_type=mime,
        file_size_bytes=len(data),
        sha256_hash=hashlib.sha256(data).hexdigest(),
    )
    session.add(doc)
    log_event(
        session,
        org_id=None,
        action="product_document.upload",
        entity_type="product_document",
        entity_id=doc.id,
        after_value={"product_id": str(product_id), "title": display_title, "kind": kind},
        context={"via": "api"},
        actor=str(current_user.id),
        actor_type=actor_type_for(current_user),
    )
    session.commit()
    return _document_out(doc)


@router.get("/{product_id}/documents/{document_id}/download")
def download_document(
    product_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
) -> RedirectResponse:
    # Deliberately NOT moved to the streamed-download pattern
    # routers/evidence.py uses for Evidence rows (docs/roadmap.md's
    # evidence-download-hardening entry) -- ProductDocument is
    # deployment-wide vendor baseline documentation (msp_admin/
    # consultant_admin only), not customer CUI evidence, and a genuinely
    # different model/router. Flagged there as a follow-up for the same
    # bearer-URL property, not silently left inconsistent.
    doc = session.get(ProductDocument, document_id)
    if doc is None or doc.product_id != product_id:
        raise HTTPException(status_code=404, detail="Document not found")
    url = storage.presigned_url(
        doc.storage_key,
        download_filename=download_filename(doc.title, os.path.splitext(doc.storage_key)[1]),
    )
    if not url:
        raise HTTPException(status_code=404, detail="Storage not configured")
    return RedirectResponse(url=url, status_code=302)


@router.delete("/{product_id}/documents/{document_id}", status_code=204)
def delete_document(
    product_id: uuid.UUID,
    document_id: uuid.UUID,
    session: Session = Depends(get_session),
    storage: StorageClient = Depends(get_storage_client),
    current_user: CurrentUser = Depends(require_role("msp_admin", "consultant_admin")),
) -> None:
    doc = session.get(ProductDocument, document_id)
    if doc is None or doc.product_id != product_id:
        raise HTTPException(status_code=404, detail="Document not found")
    storage.delete_file(doc.storage_key)
    log_event(
        session,
        org_id=None,
        action="product_document.delete",
        entity_type="product_document",
        entity_id=doc.id,
        before_value={"product_id": str(product_id), "title": doc.title, "kind": doc.kind},
        context={"via": "api"},
        actor=str(current_user.id),
        actor_type=actor_type_for(current_user),
    )
    session.delete(doc)
    session.commit()
