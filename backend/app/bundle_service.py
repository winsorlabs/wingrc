"""Bundle export service — point-in-time assessor package assembly.

Produces a structured ZIP that an MSP hands to a C3PAO.  All data is copied
to frozen Python dataclasses before rendering so the bundle is a coherent
point-in-time snapshot even if the live assessment is edited after export.
Evidence file bytes are embedded directly so the bundle stays valid after
presigned URLs expire.

CMMC artifact hashing (DoD-CIO-00008):
  - SHA-256 is computed at upload time and stored on the evidence row.
  - Pre-existing evidence (uploaded before migration 0014) is hashed lazily:
    bytes are already fetched for embedding; the hash is computed from those
    in-memory bytes and written back to the DB in the same transaction.
  - artifact_log.txt lists every embedded file with Algorithm | Hash | Path.
  - A second-order SHA-256 of artifact_log.txt is shown on the cover page
    using the exact eMASS field labels (Hashed Data List / Hash Value).
"""
from __future__ import annotations

import base64
import hashlib
import html
import io
import re
import uuid
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .engine import recompute_sprs
from .models import (
    Assessment,
    AssessmentObjective,
    Contact,
    ContactDocumentationRole,
    Control,
    ControlState,
    Evidence,
    EvidenceStateLink,
    EvidenceTask,
    EvidenceTaskStateLink,
    Finding,
    ImplementationStatement,
    Organization,
    RaciAssignment,
    SystemDescription,
)
from .storage import StorageClient

# ---------------------------------------------------------------------------
# CSS — shared across all HTML documents in the bundle
# ---------------------------------------------------------------------------

_CSS = (
    "body{font-family:system-ui,-apple-system,sans-serif;max-width:960px;"
    "margin:2rem auto;padding:0 1.5rem;color:#1a1a1a;line-height:1.5}"
    "h1{font-size:1.6rem;border-bottom:2px solid #111;padding-bottom:.5rem;margin-top:2rem}"
    "h2{font-size:1.3rem;border-bottom:1px solid #ccc;padding-bottom:.25rem;"
    "margin-top:2.5rem;color:#1e3a5f}"
    "h3{font-size:1.05rem;margin-top:1.5rem;color:#2a2a2a}"
    "table{border-collapse:collapse;width:100%;margin:1rem 0;font-size:.9rem}"
    "th,td{border:1px solid #d1d5db;padding:.45rem .75rem;text-align:left}"
    "th{background:#f3f4f6;font-weight:600}"
    "tr:nth-child(even){background:#f9fafb}"
    ".stamp{background:#eff6ff;border:1px solid #3b82f6;padding:.75rem 1rem;"
    "border-radius:4px;margin:1rem 0;font-size:.9rem}"
    ".s{display:inline-block;padding:.1rem .45rem;border-radius:3px;"
    "font-size:.8rem;font-weight:600;text-transform:uppercase;letter-spacing:.03em}"
    ".s-met{background:#dcfce7;color:#166534}"
    ".s-not_met{background:#fee2e2;color:#991b1b}"
    ".s-pending_evidence{background:#fef9c3;color:#92400e}"
    ".s-partial{background:#dbeafe;color:#1e40af}"
    ".s-needs_review{background:#ede9fe;color:#5b21b6}"
    ".s-not_applicable{background:#f3f4f6;color:#6b7280}"
    ".s-inherited{background:#f0fdf4;color:#166534}"
    ".obj{margin:.6rem 0;padding:.5rem .75rem;border-left:3px solid #e5e7eb;background:#fafafa}"
    ".obj-k{font-weight:700;color:#374151;display:inline-block;min-width:2rem}"
    ".no-stmt{color:#9ca3af;font-style:italic}"
    ".s-tag{font-size:.75rem;color:#6b7280;background:#f3f4f6;"
    "padding:.1rem .4rem;border-radius:2px;margin-left:.5rem}"
    ".logo{max-height:80px;max-width:200px;margin-bottom:1rem;display:block}"
    ".toc{list-style:none;padding:0}"
    ".toc li{padding:.35rem 0;border-bottom:1px solid #f0f0f0}"
    ".toc a{color:#1d4ed8;text-decoration:none}"
    ".score-big{font-size:2.5rem;font-weight:700;color:#1e3a5f}"
    ".ev-path{font-family:monospace;font-size:.8rem;color:#4b5563;"
    "background:#f3f4f6;padding:.1rem .4rem;border-radius:2px}"
    "@media print{body{max-width:100%}a{color:inherit}}"
)

# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------

_STATUS_PRIORITY: dict[str, int] = {
    "not_met": 0,
    "needs_review": 1,
    "pending_evidence": 2,
    "partial": 3,
    "inherited": 4,
    "not_applicable": 5,
    "met": 6,
}

_NON_PASSING = frozenset({"not_met", "needs_review", "pending_evidence", "partial"})

_ARTIFACT_HASH_ALGO = "SHA-256"


def _rollup_status(statuses: list[str]) -> str:
    if not statuses:
        return "not_met"
    return min(statuses, key=lambda s: _STATUS_PRIORITY.get(s, 0))


# ---------------------------------------------------------------------------
# Snapshot dataclasses — all primitives, zero ORM references
# ---------------------------------------------------------------------------


@dataclass
class OrgSnap:
    name: str
    cage_code: str | None
    uei: str | None
    year_established: int | None
    industry: str | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state_or_province: str | None
    postal_code: str | None
    country: str | None
    phone_primary: str | None
    website: str | None
    logo_bytes: bytes | None
    logo_mime: str | None


@dataclass
class SysDescSnap:
    system_name: str
    system_type: str
    operational_status: str
    system_description: str | None
    cui_categories: list
    cui_storage_locations: list
    authorization_boundary_description: str | None
    external_connections: list
    cui_flow_description: str | None


@dataclass
class AssessmentSnap:
    id: uuid.UUID
    name: str
    assessment_type: str
    status: str
    started_at: datetime | None


@dataclass
class ContactSnap:
    name: str
    email: str
    phone: str | None
    affiliation: str
    role_title: str | None
    contract_ref: str | None
    roles: list[str]


@dataclass
class EvidenceSnap:
    evidence_id: uuid.UUID
    kind: str
    title: str
    artifact_type: str
    zip_path: str | None
    location: str | None
    file_size_bytes: int | None
    collected_at: datetime
    sha256_hash: str | None = None


@dataclass
class RaciSnap:
    contact_name: str
    contact_affiliation: str
    raci_letter: str


@dataclass
class ObjectiveSnap:
    objective_key: str
    objective_text: str
    status: str
    responsibility: str
    stmt_body: str | None
    stmt_status: str | None
    evidence: list[EvidenceSnap] = field(default_factory=list)
    raci: list[RaciSnap] = field(default_factory=list)


@dataclass
class ControlSnap:
    control_id: str
    family: str
    title: str
    requirement_text: str
    sprs_weight: int
    rollup_status: str
    objectives: list[ObjectiveSnap] = field(default_factory=list)


@dataclass
class OpenTaskSnap:
    task_id: uuid.UUID
    title: str
    artifact_type: str
    control_ids: list[str] = field(default_factory=list)


@dataclass
class FindingSnap:
    title: str
    severity: str
    description: str | None
    status: str


@dataclass
class BundleSnapshot:
    generated_at: datetime
    sprs_score: int
    org: OrgSnap
    sys_desc: SysDescSnap | None
    assessment: AssessmentSnap
    contacts: list[ContactSnap]
    controls: list[ControlSnap]
    evidence_files: dict[uuid.UUID, tuple[str, bytes]]
    evidence_hashes: dict[uuid.UUID, str]
    unavailable_ev_ids: set[uuid.UUID]
    open_tasks: list[OpenTaskSnap]
    findings: list[FindingSnap]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _detect_image_mime(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


_MIME_TO_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/plain": ".txt",
    "text/csv": ".csv",
}


def _ext_from_mime(mime: str | None) -> str:
    return _MIME_TO_EXT.get(mime or "", "")


def _safe_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40]


def _esc(s: str | None) -> str:
    return html.escape(s or "")


def _na(v: object) -> str:
    if v is None or v == "":
        return "N/A"
    return str(v)


def _status_badge(status: str) -> str:
    label = status.replace("_", " ")
    return f'<span class="s s-{_esc(status)}">{_esc(label)}</span>'


def _html_page(title: str, body: str) -> str:
    return (
        f"<!DOCTYPE html><html lang='en'>"
        f"<head><meta charset='utf-8'><title>{_esc(title)}</title>"
        f"<style>{_CSS}</style></head>"
        f"<body>{body}</body></html>"
    )


def _ev_zip_rel(ev_id: uuid.UUID, title: str, mime_type: str | None) -> str:
    """Canonical ZIP-relative path for an embedded evidence file.

    Single source of truth used by both snapshot assembly (where the path is
    stored in EvidenceSnap.zip_path) and render (where the path is written to
    the artifact log and the ZIP entry).  Keeping this in one function prevents
    slug-generation drift between the two code paths.
    """
    ext = _ext_from_mime(mime_type)
    slug = _safe_slug(title or str(ev_id)[:8])
    return f"evidence/files/{str(ev_id)[:8]}_{slug}{ext}"


def _hash_cell(ev: EvidenceSnap) -> str:
    if ev.kind == "reference":
        return "not applicable — reference only"
    if ev.sha256_hash:
        return f"<span class='ev-path'>{_esc(ev.sha256_hash)}</span>"
    return "<em style='color:#9ca3af'>unavailable</em>"


# ---------------------------------------------------------------------------
# Snapshot assembly
# ---------------------------------------------------------------------------


def snapshot_bundle(
    session: Session,
    storage: StorageClient,
    org_id: uuid.UUID,
    assessment_id: uuid.UUID,
) -> BundleSnapshot:
    """Load all data into frozen snapshot dataclasses for bundle rendering.

    Calls recompute_sprs first so the score in the bundle is always current.
    Evidence file bytes are fetched from object storage and embedded; failures
    are recorded in unavailable_ev_ids (included in manifest, not the zip).

    SHA-256 hashes (DoD-CIO-00008):
      evidence_hashes is populated ONLY for evidence items whose bytes were
      successfully fetched this export.  If a fetch fails, the item goes into
      unavailable_ev_ids and gets NO entry in evidence_hashes, regardless of
      whether a cached sha256_hash exists on the DB row.  This prevents a stale
      DB hash from appearing next to a file that is absent from the bundle.

      Pre-existing evidence (sha256_hash IS NULL in DB): hash is computed from
      the in-memory bytes (already fetched for embedding) and written back to
      the evidence row in the same transaction via a lightweight UPDATE.
    """
    sprs_score = recompute_sprs(session, assessment_id)
    session.flush()

    # --- organization ---
    org = session.get(Organization, org_id)
    logo_bytes: bytes | None = None
    logo_mime: str | None = None
    if org and org.logo_storage_key:
        try:
            logo_bytes = storage.get_bytes(org.logo_storage_key) or None
            if logo_bytes:
                logo_mime = _detect_image_mime(logo_bytes)
        except Exception:  # noqa: BLE001
            pass

    org_snap = OrgSnap(
        name=org.name if org else "",
        cage_code=org.cage_code if org else None,
        uei=org.uei if org else None,
        year_established=org.year_established if org else None,
        industry=org.industry if org else None,
        address_line1=org.address_line1 if org else None,
        address_line2=org.address_line2 if org else None,
        city=org.city if org else None,
        state_or_province=org.state_or_province if org else None,
        postal_code=org.postal_code if org else None,
        country=org.country if org else None,
        phone_primary=org.phone_primary if org else None,
        website=org.website if org else None,
        logo_bytes=logo_bytes,
        logo_mime=logo_mime,
    )

    # --- system description ---
    sd = session.scalars(
        select(SystemDescription).where(SystemDescription.org_id == org_id)
    ).first()
    sys_desc_snap: SysDescSnap | None = None
    if sd:
        sys_desc_snap = SysDescSnap(
            system_name=sd.system_name,
            system_type=sd.system_type,
            operational_status=sd.operational_status,
            system_description=sd.system_description,
            cui_categories=list(sd.cui_categories or []),
            cui_storage_locations=list(sd.cui_storage_locations or []),
            authorization_boundary_description=sd.authorization_boundary_description,
            external_connections=list(sd.external_connections or []),
            cui_flow_description=sd.cui_flow_description,
        )

    # --- assessment ---
    assessment = session.get(Assessment, assessment_id)
    assessment_snap = AssessmentSnap(
        id=assessment.id if assessment else assessment_id,
        name=assessment.name if assessment else "",
        assessment_type=assessment.assessment_type if assessment else "",
        status=assessment.status if assessment else "",
        started_at=assessment.started_at if assessment else None,
    )

    # --- contacts ---
    contact_rows = session.scalars(
        select(Contact).where(Contact.org_id == org_id).order_by(Contact.name)
    ).all()
    contact_ids = [c.id for c in contact_rows]
    role_rows = (
        session.scalars(
            select(ContactDocumentationRole).where(
                ContactDocumentationRole.contact_id.in_(contact_ids)
            )
        ).all()
        if contact_ids
        else []
    )
    roles_by_contact: dict[uuid.UUID, list[str]] = {}
    for rr in role_rows:
        roles_by_contact.setdefault(rr.contact_id, []).append(rr.role)

    contacts = [
        ContactSnap(
            name=c.name,
            email=c.email,
            phone=c.phone,
            affiliation=c.affiliation,
            role_title=c.role_title,
            contract_ref=c.contract_ref,
            roles=roles_by_contact.get(c.id, []),
        )
        for c in contact_rows
    ]

    # --- control tree: states + objectives + controls + impl statements ---
    ctrl_rows = session.execute(
        select(
            Control.control_id,
            Control.family,
            Control.title,
            Control.requirement_text,
            Control.sprs_weight,
            Control.sequence_order,
            AssessmentObjective.id.label("obj_id"),
            AssessmentObjective.objective_key,
            AssessmentObjective.text.label("obj_text"),
            ControlState.id.label("cs_id"),
            ControlState.status,
            ControlState.responsibility,
            ImplementationStatement.body.label("stmt_body"),
            ImplementationStatement.status.label("stmt_status"),
        )
        .select_from(ControlState)
        .join(AssessmentObjective, AssessmentObjective.id == ControlState.objective_id)
        .join(Control, Control.id == AssessmentObjective.control_id)
        .outerjoin(
            ImplementationStatement,
            (ImplementationStatement.objective_id == ControlState.objective_id)
            & (ImplementationStatement.assessment_id == assessment_id),
        )
        .where(ControlState.assessment_id == assessment_id)
        .order_by(
            Control.sequence_order,
            Control.family,
            Control.control_id,
            AssessmentObjective.objective_key,
        )
    ).all()

    # --- evidence per control state ---
    ev_rows = session.execute(
        select(
            ControlState.id.label("cs_id"),
            Evidence.id.label("ev_id"),
            Evidence.kind,
            Evidence.title.label("ev_title"),
            Evidence.artifact_type,
            Evidence.storage_key,
            Evidence.mime_type,
            Evidence.file_size_bytes,
            Evidence.reference_location,
            Evidence.collected_at,
            Evidence.sha256_hash,
        )
        .select_from(ControlState)
        .join(EvidenceStateLink, EvidenceStateLink.control_state_id == ControlState.id)
        .join(Evidence, Evidence.id == EvidenceStateLink.evidence_id)
        .where(
            ControlState.assessment_id == assessment_id,
            EvidenceStateLink.is_archived.is_(False),
        )
        .order_by(Evidence.collected_at)
    ).all()

    # Fetch file bytes (deduplicated by evidence_id).
    #
    # Invariant: evidence_files and evidence_hashes have identical key sets.
    # An ev_id enters both dicts only on a successful get_bytes() call.
    # Fetch failure → unavailable_ev_ids only; the DB sha256_hash value is
    # intentionally ignored so a stale cached hash never appears next to a
    # file that is absent from this bundle.
    seen_ev: set[uuid.UUID] = set()
    evidence_files: dict[uuid.UUID, tuple[str, bytes]] = {}
    evidence_hashes: dict[uuid.UUID, str] = {}
    unavailable_ev_ids: set[uuid.UUID] = set()
    for er in ev_rows:
        if er.kind != "file" or not er.storage_key:
            continue
        ev_id: uuid.UUID = er.ev_id
        if ev_id in seen_ev:
            continue
        seen_ev.add(ev_id)
        zip_path = _ev_zip_rel(ev_id, er.ev_title or "", er.mime_type)
        try:
            file_bytes = storage.get_bytes(er.storage_key)
            if file_bytes:
                h = er.sha256_hash
                if h is None:
                    h = hashlib.sha256(file_bytes).hexdigest()
                    session.execute(
                        update(Evidence)
                        .where(Evidence.id == ev_id)
                        .values(sha256_hash=h)
                    )
                evidence_files[ev_id] = (zip_path, file_bytes)
                evidence_hashes[ev_id] = h
            else:
                unavailable_ev_ids.add(ev_id)
        except Exception:  # noqa: BLE001
            unavailable_ev_ids.add(ev_id)

    ev_by_cs: dict[uuid.UUID, list[EvidenceSnap]] = {}
    for er in ev_rows:
        cs_id: uuid.UUID = er.cs_id
        ev_id = er.ev_id
        if er.kind == "file":
            hit = evidence_files.get(ev_id)
            zip_path_for_ev = hit[0] if hit else None
            ev_hash = evidence_hashes.get(ev_id)
        else:
            zip_path_for_ev = None
            ev_hash = None
        ev_snap = EvidenceSnap(
            evidence_id=ev_id,
            kind=er.kind,
            title=er.ev_title or "",
            artifact_type=er.artifact_type,
            zip_path=zip_path_for_ev,
            location=er.reference_location,
            file_size_bytes=er.file_size_bytes,
            collected_at=er.collected_at,
            sha256_hash=ev_hash,
        )
        ev_by_cs.setdefault(cs_id, []).append(ev_snap)

    # --- RACI per control state ---
    all_cs_ids = [r.cs_id for r in ctrl_rows]
    raci_by_cs: dict[uuid.UUID, list[RaciSnap]] = {}
    if all_cs_ids:
        raci_rows = session.execute(
            select(
                RaciAssignment.control_state_id,
                RaciAssignment.raci_letter,
                Contact.name.label("contact_name"),
                Contact.affiliation.label("contact_affiliation"),
            )
            .join(Contact, Contact.id == RaciAssignment.contact_id)
            .where(RaciAssignment.control_state_id.in_(all_cs_ids))
        ).all()
        for rr in raci_rows:
            raci_by_cs.setdefault(rr.control_state_id, []).append(
                RaciSnap(
                    contact_name=rr.contact_name,
                    contact_affiliation=rr.contact_affiliation,
                    raci_letter=rr.raci_letter,
                )
            )

    # Build ControlSnap tree (preserves query order via insertion-ordered dict)
    ctrl_map: dict[str, ControlSnap] = {}
    for row in ctrl_rows:
        cid = row.control_id
        if cid not in ctrl_map:
            ctrl_map[cid] = ControlSnap(
                control_id=cid,
                family=row.family,
                title=row.title,
                requirement_text=row.requirement_text,
                sprs_weight=row.sprs_weight,
                rollup_status="met",
                objectives=[],
            )
        cs_id = row.cs_id
        ctrl_map[cid].objectives.append(
            ObjectiveSnap(
                objective_key=row.objective_key,
                objective_text=row.obj_text or "",
                status=row.status,
                responsibility=row.responsibility,
                stmt_body=row.stmt_body,
                stmt_status=row.stmt_status,
                evidence=ev_by_cs.get(cs_id, []),
                raci=raci_by_cs.get(cs_id, []),
            )
        )

    for ctrl in ctrl_map.values():
        ctrl.rollup_status = _rollup_status([o.status for o in ctrl.objectives])

    controls = list(ctrl_map.values())

    # --- open evidence tasks ---
    task_rows = session.execute(
        select(
            EvidenceTask.id.label("task_id"),
            EvidenceTask.title,
            EvidenceTask.artifact_type,
            Control.control_id,
        )
        .select_from(EvidenceTask)
        .outerjoin(EvidenceTaskStateLink, EvidenceTaskStateLink.task_id == EvidenceTask.id)
        .outerjoin(ControlState, ControlState.id == EvidenceTaskStateLink.control_state_id)
        .outerjoin(AssessmentObjective, AssessmentObjective.id == ControlState.objective_id)
        .outerjoin(Control, Control.id == AssessmentObjective.control_id)
        .where(
            EvidenceTask.assessment_id == assessment_id,
            EvidenceTask.status == "open",
            EvidenceTask.is_archived.is_(False),
        )
        .order_by(EvidenceTask.title, Control.control_id)
    ).all()

    task_map: dict[uuid.UUID, OpenTaskSnap] = {}
    for tr in task_rows:
        tid: uuid.UUID = tr.task_id
        if tid not in task_map:
            task_map[tid] = OpenTaskSnap(
                task_id=tid, title=tr.title, artifact_type=tr.artifact_type
            )
        if tr.control_id and tr.control_id not in task_map[tid].control_ids:
            task_map[tid].control_ids.append(tr.control_id)

    open_tasks = list(task_map.values())

    # --- open findings ---
    finding_rows = session.scalars(
        select(Finding)
        .where(
            Finding.assessment_id == assessment_id,
            Finding.status.in_(["open", "in_remediation"]),
        )
        .order_by(Finding.severity, Finding.title)
    ).all()
    findings = [
        FindingSnap(
            title=fr.title,
            severity=fr.severity,
            description=fr.description,
            status=fr.status,
        )
        for fr in finding_rows
    ]

    # Stamp generated_at after all data is collected
    generated_at = datetime.now(UTC)

    return BundleSnapshot(
        generated_at=generated_at,
        sprs_score=sprs_score,
        org=org_snap,
        sys_desc=sys_desc_snap,
        assessment=assessment_snap,
        contacts=contacts,
        controls=controls,
        evidence_files=evidence_files,
        evidence_hashes=evidence_hashes,
        unavailable_ev_ids=unavailable_ev_ids,
        open_tasks=open_tasks,
        findings=findings,
    )


# ---------------------------------------------------------------------------
# Bundle rendering
# ---------------------------------------------------------------------------


def render_bundle(snapshot: BundleSnapshot) -> tuple[bytes, str, str, str]:
    """Render the snapshot to a ZIP archive.

    Returns (zip_bytes, filename, artifact_log_filename, artifact_log_hash).

    Artifact log (DoD-CIO-00008):
      All generated HTML documents (except cover.html) and all embedded evidence
      files are listed with Algorithm | Hash | Path.  cover.html is excluded
      because it carries the second-order hash — including it would be circular.
      The artifact log itself is then hashed (second-order hash) and both values
      are written to cover.html under the exact eMASS field names.
    """
    slug = _safe_slug(snapshot.org.name)
    date_str = snapshot.generated_at.strftime("%Y%m%d")
    root = f"{slug}_{date_str}"

    # --- render all HTML pages except cover ---
    index_html = _render_index(snapshot)
    sys_desc_html = _render_sys_desc(snapshot)
    impl_html = _render_implementation(snapshot)
    personnel_html = _render_personnel(snapshot)
    manifest_html = _render_manifest(snapshot)
    scoring_html = _render_scoring(snapshot)
    outstanding_html = _render_outstanding(snapshot)

    # --- build artifact log ---
    # HTML documents are hashed from their rendered bytes.
    # Evidence files use the stored sha256_hash from evidence_hashes (computed
    # at upload time or lazily during this snapshot_bundle call — never re-read
    # from storage here).  evidence_hashes and evidence_files share the same
    # key set, so the direct index lookup below is always valid.
    log_lines = ["Algorithm | Hash | Path"]
    for rel_path, content in [
        (f"{root}/index.html", index_html),
        (f"{root}/ssp/01_system_description.html", sys_desc_html),
        (f"{root}/ssp/02_implementation.html", impl_html),
        (f"{root}/ssp/03_personnel.html", personnel_html),
        (f"{root}/evidence/manifest.html", manifest_html),
        (f"{root}/summary/scoring.html", scoring_html),
        (f"{root}/summary/outstanding.html", outstanding_html),
    ]:
        h = hashlib.sha256(content.encode()).hexdigest()
        log_lines.append(f"{_ARTIFACT_HASH_ALGO} | {h} | {rel_path}")

    for ev_id, (zip_rel, _) in snapshot.evidence_files.items():
        log_lines.append(
            f"{_ARTIFACT_HASH_ALGO} | {snapshot.evidence_hashes[ev_id]} | {root}/{zip_rel}"
        )

    artifact_log_filename = "artifact_log.txt"
    artifact_log_bytes = ("\n".join(log_lines) + "\n").encode()
    artifact_log_hash = hashlib.sha256(artifact_log_bytes).hexdigest()

    # --- render cover with eMASS fields (after second-order hash is known) ---
    cover_html = _render_cover(snapshot, artifact_log_filename, artifact_log_hash)

    # --- write ZIP ---
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{root}/index.html", index_html)
        zf.writestr(f"{root}/cover.html", cover_html)
        zf.writestr(f"{root}/ssp/01_system_description.html", sys_desc_html)
        zf.writestr(f"{root}/ssp/02_implementation.html", impl_html)
        zf.writestr(f"{root}/ssp/03_personnel.html", personnel_html)
        zf.writestr(f"{root}/evidence/manifest.html", manifest_html)
        zf.writestr(f"{root}/summary/scoring.html", scoring_html)
        zf.writestr(f"{root}/summary/outstanding.html", outstanding_html)
        zf.writestr(f"{root}/{artifact_log_filename}", artifact_log_bytes)
        for _ev_id, (zip_rel, file_bytes) in snapshot.evidence_files.items():
            zf.writestr(f"{root}/{zip_rel}", file_bytes)

    filename = f"wingrc_bundle_{slug}_{date_str}.zip"
    return buf.getvalue(), filename, artifact_log_filename, artifact_log_hash


# ---------------------------------------------------------------------------
# HTML renderers
# ---------------------------------------------------------------------------


def _stamp(snapshot: BundleSnapshot) -> str:
    ts = snapshot.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    return (
        f'<div class="stamp">Point-in-time snapshot &mdash; generated {_esc(ts)}'
        f" &mdash; do not modify this bundle before submitting to a C3PAO.</div>"
    )


def _render_cover(
    snapshot: BundleSnapshot,
    hashed_data_list: str | None = None,
    hash_value: str | None = None,
) -> str:
    o = snapshot.org
    a = snapshot.assessment

    logo_html = ""
    if o.logo_bytes and o.logo_mime:
        b64 = base64.b64encode(o.logo_bytes).decode()
        logo_html = f'<img class="logo" src="data:{_esc(o.logo_mime)};base64,{b64}" alt="Logo">'

    addr_parts = [
        o.address_line1,
        o.address_line2,
        ", ".join(filter(None, [o.city, o.state_or_province, o.postal_code])),
        o.country,
    ]
    address = "; ".join(p for p in addr_parts if p)

    score_color = "#166534" if snapshot.sprs_score >= 0 else "#991b1b"
    score_style = f"color:{score_color}"

    rows = "".join([
        f"<tr><th>Assessment</th><td>{_esc(a.name)}</td></tr>",
        f"<tr><th>Type</th><td>{_esc(a.assessment_type)}</td></tr>",
        f"<tr><th>Status</th><td>{_status_badge(a.status)}</td></tr>",
        f"<tr><th>CAGE Code</th><td>{_esc(_na(o.cage_code))}</td></tr>",
        f"<tr><th>UEI</th><td>{_esc(_na(o.uei))}</td></tr>",
        f"<tr><th>Industry</th><td>{_esc(_na(o.industry))}</td></tr>",
        f"<tr><th>Address</th><td>{_esc(_na(address) if address else None)}</td></tr>",
        f"<tr><th>Phone</th><td>{_esc(_na(o.phone_primary))}</td></tr>",
        f"<tr><th>Website</th><td>{_esc(_na(o.website))}</td></tr>",
    ])

    emass_html = ""
    if hashed_data_list and hash_value:
        emass_html = (
            "<h2>Artifact Hash &mdash; eMASS Submission Fields</h2>"
            "<table>"
            f"<tr><th>Hashed Data List</th>"
            f"<td><span class='ev-path'>{_esc(hashed_data_list)}</span></td></tr>"
            f"<tr><th>Hash Value</th>"
            f"<td><span class='ev-path'>{_esc(hash_value)}</span></td></tr>"
            "</table>"
            "<p style='font-size:.85rem;color:#4b5563'>"
            "Copy these values into the corresponding eMASS fields when submitting "
            "this assessment package to your C3PAO or DCMA DIBCAC.</p>"
        )

    body = (
        f"{_stamp(snapshot)}"
        f"{logo_html}"
        f"<h1>{_esc(o.name)}</h1>"
        f"<p style='{score_style}'><span class='score-big'>{snapshot.sprs_score}</span>"
        f"&nbsp;<span style='color:#6b7280'>/ 110 SPRS</span></p>"
        f"<table>{rows}</table>"
        f"{emass_html}"
        f"<p style='color:#6b7280;font-size:.85rem'>"
        f"Generated: {_esc(snapshot.generated_at.strftime('%Y-%m-%d %H:%M UTC'))}</p>"
    )
    return _html_page(f"Cover — {o.name}", body)


def _render_sys_desc(snapshot: BundleSnapshot) -> str:
    sd = snapshot.sys_desc
    if sd is None:
        body = (
            f"{_stamp(snapshot)}"
            "<h1>System Description</h1>"
            '<p class="no-stmt">No system description has been entered for this organization.</p>'
        )
        return _html_page("System Description", body)

    def _list_items(items: list) -> str:
        if not items:
            return "<p>None recorded.</p>"
        return "<ul>" + "".join(f"<li>{_esc(str(i))}</li>" for i in items) + "</ul>"

    def _ext_conn_table(conns: list) -> str:
        if not conns:
            return "<p>No external connections recorded.</p>"
        rows = ""
        for conn in conns:
            if isinstance(conn, dict):
                rows += (
                    f"<tr><td>{_esc(str(conn.get('name', '')))}</td>"
                    f"<td>{_esc(str(conn.get('type', '')))}</td>"
                    f"<td>{_esc(str(conn.get('description', '')))}</td></tr>"
                )
            else:
                rows += f"<tr><td colspan='3'>{_esc(str(conn))}</td></tr>"
        return (
            "<table><tr><th>Name</th><th>Type</th><th>Description</th></tr>"
            f"{rows}</table>"
        )

    body = (
        f"{_stamp(snapshot)}"
        "<h1>System Description</h1>"
        f"<h2>1.1 System Name &amp; Type</h2>"
        f"<table>"
        f"<tr><th>System Name</th><td>{_esc(sd.system_name)}</td></tr>"
        f"<tr><th>System Type</th><td>{_esc(sd.system_type.replace('_', ' ').title())}</td></tr>"
        f"<tr><th>Operational Status</th><td>"
        f"{_esc(sd.operational_status.replace('_', ' ').title())}</td></tr>"
        f"</table>"
        f"<h2>1.2 System Description</h2>"
        f"<p>{_esc(sd.system_description or 'Not provided.')}</p>"
        f"<h2>1.3 CUI Categories</h2>"
        f"{_list_items(sd.cui_categories)}"
        f"<h2>1.4 CUI Storage Locations</h2>"
        f"{_list_items(sd.cui_storage_locations)}"
        f"<h2>1.5 Authorization Boundary</h2>"
        f"<p>{_esc(sd.authorization_boundary_description or 'Not provided.')}</p>"
        f"<h2>1.6 External Connections</h2>"
        f"{_ext_conn_table(sd.external_connections)}"
        f"<h2>1.7 CUI Flow</h2>"
        f"<p>{_esc(sd.cui_flow_description or 'Not provided.')}</p>"
    )
    return _html_page("System Description", body)


def _render_implementation(snapshot: BundleSnapshot) -> str:
    if not snapshot.controls:
        body = (
            f"{_stamp(snapshot)}"
            "<h1>Implementation Statements</h1>"
            '<p class="no-stmt">No control states found for this assessment.</p>'
        )
        return _html_page("Implementation Statements", body)

    sections = ""
    current_family = ""
    for ctrl in snapshot.controls:
        if ctrl.family != current_family:
            current_family = ctrl.family
            sections += f"<h2>{_esc(current_family)} — Access Control</h2>" if False else (
                f"<h2>{_esc(current_family)}</h2>"
            )

        sections += (
            f"<h3>{_esc(ctrl.control_id)} — {_esc(ctrl.title)}"
            f" &nbsp;{_status_badge(ctrl.rollup_status)}"
            f" &nbsp;<small style='color:#6b7280'>weight&nbsp;{ctrl.sprs_weight}</small></h3>"
            f"<p style='color:#374151;font-style:italic'>{_esc(ctrl.requirement_text)}</p>"
        )

        for obj in ctrl.objectives:
            stmt_html = (
                f"<p>{_esc(obj.stmt_body)}"
                f"<span class='s-tag'>{_esc(obj.stmt_status or '')}</span></p>"
                if obj.stmt_body
                else "<p class='no-stmt'>No implementation statement drafted.</p>"
            )

            raci_html = ""
            if obj.raci:
                raci_items = ", ".join(
                    f"{_esc(r.raci_letter)}: {_esc(r.contact_name)} ({_esc(r.contact_affiliation)})"
                    for r in obj.raci
                )
                raci_html = (
                    f"<p style='font-size:.85rem;color:#4b5563'>"
                    f"<strong>RACI:</strong> {raci_items}</p>"
                )

            ev_html = ""
            if obj.evidence:
                ev_items = "".join(
                    f"<li>{_esc(e.title)} "
                    f"<span class='s-tag'>{_esc(e.artifact_type)}</span>"
                    + (
                        f" <span class='ev-path'>{_esc(e.zip_path or '')}</span>"
                        if e.kind == "file" and e.zip_path
                        else (
                            f" <span class='s-tag'>ref</span> {_esc(e.location or '')}"
                            if e.kind == "reference"
                            else (
                                " <em style='color:#9ca3af'>"
                                "(file not available in this bundle)</em>"
                            )
                        )
                    )
                    + "</li>"
                    for e in obj.evidence
                )
                ev_html = (
                    f"<details style='font-size:.85rem;margin-top:.25rem'>"
                    f"<summary style='cursor:pointer;color:#1d4ed8'>"
                    f"Evidence ({len(obj.evidence)})</summary>"
                    f"<ul>{ev_items}</ul></details>"
                )

            sections += (
                f'<div class="obj">'
                f'<span class="obj-k">[{_esc(obj.objective_key)}]</span>'
                f" {_esc(obj.objective_text)}"
                f" {_status_badge(obj.status)}"
                f"<br>{stmt_html}{raci_html}{ev_html}"
                f"</div>"
            )

    body = f"{_stamp(snapshot)}<h1>Implementation Statements</h1>{sections}"
    return _html_page("Implementation Statements", body)


def _render_personnel(snapshot: BundleSnapshot) -> str:
    if not snapshot.contacts:
        body = (
            f"{_stamp(snapshot)}"
            "<h1>Personnel &amp; Contacts</h1>"
            '<p class="no-stmt">No contacts have been added.</p>'
        )
        return _html_page("Personnel", body)

    rows = "".join(
        f"<tr>"
        f"<td>{_esc(c.name)}</td>"
        f"<td>{_esc(c.affiliation)}</td>"
        f"<td>{_esc(c.role_title or '')}</td>"
        f"<td>{_esc(c.email)}</td>"
        f"<td>{_esc(c.phone or '')}</td>"
        f"<td>{_esc(', '.join(r.replace('_', ' ') for r in c.roles))}</td>"
        f"<td>{_esc(c.contract_ref or '')}</td>"
        f"</tr>"
        for c in snapshot.contacts
    )
    table = (
        "<table>"
        "<tr><th>Name</th><th>Affiliation</th><th>Role Title</th><th>Email</th>"
        "<th>Phone</th><th>Documentation Roles</th><th>Contract Ref</th></tr>"
        f"{rows}</table>"
    )
    body = f"{_stamp(snapshot)}<h1>Personnel &amp; Contacts</h1>{table}"
    return _html_page("Personnel", body)


def _render_manifest(snapshot: BundleSnapshot) -> str:
    if not snapshot.controls:
        body = (
            f"{_stamp(snapshot)}"
            "<h1>Evidence Manifest</h1>"
            '<p class="no-stmt">No control states found.</p>'
        )
        return _html_page("Evidence Manifest", body)

    sections = ""
    for ctrl in snapshot.controls:
        all_ev = [e for obj in ctrl.objectives for e in obj.evidence]
        if not all_ev:
            continue

        rows = ""
        for obj in ctrl.objectives:
            for ev in obj.evidence:
                if ev.kind == "file":
                    loc_html = (
                        f"<span class='ev-path'>{_esc(ev.zip_path)}</span>"
                        if ev.zip_path
                        else "<em style='color:#9ca3af'>not available in bundle</em>"
                    )
                else:
                    loc_html = _esc(ev.location or "")
                rows += (
                    f"<tr>"
                    f"<td>[{_esc(obj.objective_key)}]</td>"
                    f"<td>{_esc(ev.title)}</td>"
                    f"<td>{_esc(ev.kind)}</td>"
                    f"<td>{_esc(ev.artifact_type)}</td>"
                    f"<td>{loc_html}</td>"
                    f"<td>{_esc(ev.collected_at.strftime('%Y-%m-%d'))}</td>"
                    f"<td>{_hash_cell(ev)}</td>"
                    f"</tr>"
                )
        sections += (
            f"<h3>{_esc(ctrl.control_id)} — {_esc(ctrl.title)}</h3>"
            "<table>"
            "<tr><th>Obj</th><th>Title</th><th>Kind</th>"
            "<th>Type</th><th>Location / Path</th><th>Collected</th>"
            "<th>SHA-256 Hash</th></tr>"
            f"{rows}</table>"
        )

    if not sections:
        sections = '<p class="no-stmt">No evidence has been collected yet.</p>'

    body = f"{_stamp(snapshot)}<h1>Evidence Manifest</h1>{sections}"
    return _html_page("Evidence Manifest", body)


def _render_scoring(snapshot: BundleSnapshot) -> str:
    deducted = [c for c in snapshot.controls if c.rollup_status in _NON_PASSING]
    passing = [c for c in snapshot.controls if c.rollup_status not in _NON_PASSING]

    by_weight: dict[int, list[ControlSnap]] = {5: [], 3: [], 1: []}
    for ctrl in deducted:
        bucket = by_weight.get(ctrl.sprs_weight, by_weight.setdefault(ctrl.sprs_weight, []))
        bucket.append(ctrl)

    deduction_rows = ""
    for weight in (5, 3, 1):
        for ctrl in by_weight.get(weight, []):
            deduction_rows += (
                f"<tr>"
                f"<td>{_esc(ctrl.control_id)}</td>"
                f"<td>{_esc(ctrl.title)}</td>"
                f"<td>{_status_badge(ctrl.rollup_status)}</td>"
                f"<td style='text-align:right'>−{ctrl.sprs_weight}</td>"
                f"</tr>"
            )

    deduction_table = (
        "<table>"
        "<tr><th>Control</th><th>Title</th><th>Status</th><th>Deduction</th></tr>"
        f"{deduction_rows}"
        "</table>"
    ) if deduction_rows else '<p class="no-stmt">No deductions — all controls passing.</p>'

    # Family summary
    family_status: dict[str, Counter[str]] = {}
    for ctrl in snapshot.controls:
        family_status.setdefault(ctrl.family, Counter())[ctrl.rollup_status] += 1

    _col_order = (
        "met", "inherited", "not_applicable", "partial",
        "pending_evidence", "needs_review", "not_met",
    )
    family_rows = "".join(
        f"<tr><td>{_esc(fam)}</td>"
        + "".join(
            f"<td style='text-align:center'>{counts.get(s, 0)}</td>"
            for s in _col_order
        )
        + "</tr>"
        for fam, counts in sorted(family_status.items())
    )
    family_table = (
        "<table>"
        "<tr><th>Family</th><th>Met</th><th>Inherited</th><th>N/A</th>"
        "<th>Partial</th><th>Pending</th><th>Needs Review</th><th>Not Met</th></tr>"
        f"{family_rows}</table>"
    )

    total_deduction = 110 - snapshot.sprs_score
    score_color = "#166534" if snapshot.sprs_score >= 0 else "#991b1b"

    body = (
        f"{_stamp(snapshot)}"
        "<h1>SPRS Scoring Summary</h1>"
        f"<p><span class='score-big' style='color:{score_color}'>{snapshot.sprs_score}</span>"
        f"&nbsp;<span style='color:#6b7280'>/ 110</span></p>"
        f"<p>Starting score: 110 &nbsp;&mdash;&nbsp; Total deduction: {total_deduction}</p>"
        "<h2>Deducted Controls</h2>"
        f"{deduction_table}"
        "<h2>Status by Control Family</h2>"
        f"{family_table}"
        "<h2>Passing Controls</h2>"
        + (
            "<table><tr><th>Control</th><th>Title</th><th>Status</th></tr>"
            + "".join(
                f"<tr><td>{_esc(c.control_id)}</td><td>{_esc(c.title)}</td>"
                f"<td>{_status_badge(c.rollup_status)}</td></tr>"
                for c in passing
            )
            + "</table>"
            if passing
            else '<p class="no-stmt">No passing controls.</p>'
        )
    )
    return _html_page("SPRS Scoring", body)


def _render_outstanding(snapshot: BundleSnapshot) -> str:
    not_passing = [c for c in snapshot.controls if c.rollup_status in _NON_PASSING]

    gaps_rows = "".join(
        f"<tr>"
        f"<td>{_esc(c.control_id)}</td>"
        f"<td>{_esc(c.family)}</td>"
        f"<td>{_esc(c.title)}</td>"
        f"<td>{_status_badge(c.rollup_status)}</td>"
        f"<td style='text-align:right'>−{c.sprs_weight}</td>"
        f"</tr>"
        for c in not_passing
    )
    gaps_table = (
        "<table><tr><th>Control</th><th>Family</th><th>Title</th><th>Status</th><th>Pts</th></tr>"
        f"{gaps_rows}</table>"
    ) if gaps_rows else '<p class="no-stmt">No gaps — all controls passing.</p>'

    tasks_rows = "".join(
        f"<tr>"
        f"<td>{_esc(t.title)}</td>"
        f"<td>{_esc(t.artifact_type)}</td>"
        f"<td>{_esc(', '.join(t.control_ids))}</td>"
        f"</tr>"
        for t in snapshot.open_tasks
    )
    tasks_table = (
        "<table><tr><th>Task</th><th>Artifact Type</th><th>Controls</th></tr>"
        f"{tasks_rows}</table>"
    ) if tasks_rows else '<p class="no-stmt">No open evidence tasks.</p>'

    findings_rows = "".join(
        f"<tr>"
        f"<td>{_esc(f.title)}</td>"
        f"<td>{_esc(f.severity)}</td>"
        f"<td>{_status_badge(f.status)}</td>"
        f"<td>{_esc(f.description or '')}</td>"
        f"</tr>"
        for f in snapshot.findings
    )
    findings_table = (
        "<table><tr><th>Finding</th><th>Severity</th><th>Status</th><th>Description</th></tr>"
        f"{findings_rows}</table>"
    ) if findings_rows else '<p class="no-stmt">No open findings.</p>'

    body = (
        f"{_stamp(snapshot)}"
        "<h1>Outstanding Items</h1>"
        "<h2>Control Gaps</h2>"
        f"{gaps_table}"
        "<h2>Open Evidence Tasks</h2>"
        f"{tasks_table}"
        "<h2>Open Findings</h2>"
        f"{findings_table}"
    )
    return _html_page("Outstanding Items", body)


def _render_index(snapshot: BundleSnapshot) -> str:
    o = snapshot.org
    a = snapshot.assessment
    ts = snapshot.generated_at.strftime("%Y-%m-%d %H:%M UTC")

    items = [
        ("Cover &amp; Metadata", "cover.html"),
        ("SSP — System Description", "ssp/01_system_description.html"),
        ("SSP — Implementation Statements", "ssp/02_implementation.html"),
        ("SSP — Personnel &amp; Contacts", "ssp/03_personnel.html"),
        ("Evidence Manifest", "evidence/manifest.html"),
        ("SPRS Scoring Summary", "summary/scoring.html"),
        ("Outstanding Items", "summary/outstanding.html"),
        ("Artifact Hash Log", "artifact_log.txt"),
    ]
    li_items = "".join(
        f'<li><a href="{href}">{label}</a></li>'
        for label, href in items
    )

    body = (
        f"{_stamp(snapshot)}"
        "<h1>WinGRC Assessor Bundle</h1>"
        f"<table>"
        f"<tr><th>Organization</th><td>{_esc(o.name)}</td></tr>"
        f"<tr><th>Assessment</th><td>{_esc(a.name)}</td></tr>"
        f"<tr><th>SPRS Score</th><td><strong>{snapshot.sprs_score} / 110</strong></td></tr>"
        f"<tr><th>Generated</th><td>{_esc(ts)}</td></tr>"
        "</table>"
        "<h2>Contents</h2>"
        f'<ul class="toc">{li_items}</ul>'
        "<h2>Usage Notes</h2>"
        "<ul>"
        "<li>Open <code>index.html</code> in any browser "
        "— all documents are self-contained HTML.</li>"
        "<li>Evidence files are in <code>evidence/files/</code> and linked from the manifest.</li>"
        "<li><code>artifact_log.txt</code> lists SHA-256 hashes for all artifacts "
        "per DoD-CIO-00008. The second-order hash on the cover page is the eMASS "
        "<em>Hash Value</em> field.</li>"
        "<li>This bundle is a point-in-time snapshot. Do not modify it before submission.</li>"
        "</ul>"
    )
    return _html_page(f"Bundle Index — {o.name}", body)
