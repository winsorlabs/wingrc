"""Contact and documentation-role endpoints.

GET    /orgs/{org_id}/contacts                        List contacts (includes roles)
POST   /orgs/{org_id}/contacts                        Create contact
GET    /orgs/{org_id}/contacts/{contact_id}           Get one contact
PATCH  /orgs/{org_id}/contacts/{contact_id}           Partial-update contact
DELETE /orgs/{org_id}/contacts/{contact_id}           Delete contact (cascades roles)
POST   /orgs/{org_id}/contacts/{contact_id}/roles     Add documentation role
DELETE /orgs/{org_id}/contacts/{contact_id}/roles/{role}  Remove documentation role

GET    /orgs/{org_id}/contacts/import/liongard        List this org's mapped Liongard
                                                       identities as candidate contacts
POST   /orgs/{org_id}/contacts/import/liongard        Create/refresh contacts from an
                                                       admin-selected subset

Documentation roles are DOCUMENTATION attributes (who appears in SSP/CRM) — not
platform-access roles. One contact can hold multiple roles (e.g. President +
authorizing_official). The auth-linkage seam runs auth→contact (user.contact_id FK),
never contact→auth.

The Liongard import is deliberately selection-based, not a bulk sync: an org may
have hundreds of Liongard identities and only a handful are compliance contacts.
It never writes to scope_entity (the scope graph) and never touches
identities_to_canonical -- that is a different model with a different mapping
function (importers/liongard.py:identity_to_contact_fields vs.
identity_to_canonical), kept deliberately separate per Contact's own docstring
in models.py. Restricted to msp_admin/consultant_admin (unlike the rest of this
router, which any org member with write access can use) -- bulk-adjacent contact
creation from an external system is treated as an admin action, not a routine
CRM edit.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import CurrentUser, require_org_access, require_write
from ..connectors import liongard as liongard_connector
from ..db import get_session
from ..domain import Source
from ..importers.liongard import build_source_ref, identity_to_contact_fields
from ..models import Contact, ContactDocumentationRole, Organization
from .scope import get_liongard_credential, get_liongard_mapping

router = APIRouter(
    prefix="/orgs",
    tags=["contacts"],
    dependencies=[Depends(require_org_access()), Depends(require_write())],
)

_VALID_AFFILIATIONS = frozenset({"msp", "customer", "mssp", "government", "other"})
_VALID_DOC_ROLES = frozenset(
    {
        "it_admin",
        "security_officer",
        "system_owner",
        "authorizing_official",
        "president",
        "cui_user",
        "assessor",
        "mssp",
        "consultant",
        "other",
    }
)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ContactDocRoleOut(BaseModel):
    role: str
    notes: str | None = None


def _normalize_email(v: str) -> str:
    # Fixes a pre-existing bug: neither this router's dupe/clash checks nor
    # the DB's UniqueConstraint("org_id", "email") were case-insensitive, so
    # "Alice@x.com" and "alice@x.com" could become two separate contacts.
    # Normalizing here, at the one place all contact-creating/patching input
    # passes through, closes that for every caller -- including the
    # Liongard import below, which would otherwise multiply the bug across
    # however many identities an admin selects. See migration
    # 0049_contact_provenance for the matching backfill of existing rows.
    return v.strip().lower()


class ContactIn(BaseModel):
    name: str
    email: str
    affiliation: str
    phone: str | None = None
    role_title: str | None = None
    contract_ref: str | None = None
    notes: str | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        return _normalize_email(v)

    @field_validator("affiliation")
    @classmethod
    def validate_affiliation(cls, v: str) -> str:
        if v not in _VALID_AFFILIATIONS:
            raise ValueError(
                f"affiliation must be one of: {sorted(_VALID_AFFILIATIONS)}"
            )
        return v


class ContactPatch(BaseModel):
    name: str | None = None
    email: str | None = None
    affiliation: str | None = None
    phone: str | None = None
    role_title: str | None = None
    contract_ref: str | None = None
    notes: str | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str | None) -> str | None:
        return _normalize_email(v) if v is not None else v

    @field_validator("affiliation")
    @classmethod
    def validate_affiliation(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_AFFILIATIONS:
            raise ValueError(
                f"affiliation must be one of: {sorted(_VALID_AFFILIATIONS)}"
            )
        return v


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    email: str
    affiliation: str
    phone: str | None = None
    role_title: str | None = None
    contract_ref: str | None = None
    notes: str | None = None
    source: str
    source_ref: str | None = None
    documentation_roles: list[ContactDocRoleOut] = []
    created_at: datetime


class RoleIn(BaseModel):
    role: str
    notes: str | None = None

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in _VALID_DOC_ROLES:
            raise ValueError(f"role must be one of: {sorted(_VALID_DOC_ROLES)}")
        return v


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contact_id: uuid.UUID
    role: str
    notes: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_org(session: Session, org_id: uuid.UUID) -> Organization:
    org = session.get(Organization, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def _get_contact(
    session: Session, org_id: uuid.UUID, contact_id: uuid.UUID
) -> Contact:
    contact = session.scalars(
        select(Contact).where(
            Contact.id == contact_id, Contact.org_id == org_id
        )
    ).first()
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return contact


def _attach_roles(
    contacts: list[Contact], session: Session
) -> list[ContactOut]:
    """Build ContactOut list with documentation_roles populated in one query."""
    if not contacts:
        return []

    contact_ids = [c.id for c in contacts]
    role_rows = session.scalars(
        select(ContactDocumentationRole).where(
            ContactDocumentationRole.contact_id.in_(contact_ids)
        )
    ).all()

    roles_by_contact: dict[uuid.UUID, list[ContactDocRoleOut]] = defaultdict(list)
    for r in role_rows:
        roles_by_contact[r.contact_id].append(
            ContactDocRoleOut(role=r.role, notes=r.notes)
        )

    out = []
    for c in contacts:
        co = ContactOut.model_validate(c)
        co.documentation_roles = roles_by_contact.get(c.id, [])
        out.append(co)
    return out


# ---------------------------------------------------------------------------
# Contact CRUD
# ---------------------------------------------------------------------------


@router.get("/{org_id}/contacts", response_model=list[ContactOut])
def list_contacts(
    org_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[ContactOut]:
    _get_org(session, org_id)
    contacts = session.scalars(
        select(Contact).where(Contact.org_id == org_id).order_by(Contact.name)
    ).all()
    return _attach_roles(list(contacts), session)


@router.post("/{org_id}/contacts", response_model=ContactOut, status_code=201)
def create_contact(
    org_id: uuid.UUID,
    body: ContactIn,
    session: Session = Depends(get_session),
) -> ContactOut:
    _get_org(session, org_id)

    existing = session.scalars(
        select(Contact).where(
            Contact.org_id == org_id, Contact.email == body.email
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Contact with email {body.email!r} already exists in this org",
        )

    contact = Contact(org_id=org_id, **body.model_dump())
    session.add(contact)
    session.flush()
    log_event(
        session,
        org_id=org_id,
        action="contact.create",
        entity_type="contact",
        entity_id=contact.id,
        after_value={
            "name": contact.name,
            "email": contact.email,
            "affiliation": contact.affiliation,
        },
        context={"via": "api"},
    )
    session.commit()
    session.refresh(contact)
    return _attach_roles([contact], session)[0]


@router.get("/{org_id}/contacts/{contact_id}", response_model=ContactOut)
def get_contact(
    org_id: uuid.UUID,
    contact_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> ContactOut:
    contact = _get_contact(session, org_id, contact_id)
    return _attach_roles([contact], session)[0]


@router.patch("/{org_id}/contacts/{contact_id}", response_model=ContactOut)
def patch_contact(
    org_id: uuid.UUID,
    contact_id: uuid.UUID,
    body: ContactPatch,
    session: Session = Depends(get_session),
) -> ContactOut:
    contact = _get_contact(session, org_id, contact_id)

    update_data = body.model_dump(include=body.model_fields_set)
    if not update_data:
        return _attach_roles([contact], session)[0]

    before: dict[str, Any] = {k: getattr(contact, k) for k in update_data}

    # Reject email change to one that already exists in this org
    if "email" in update_data and update_data["email"] != contact.email:
        clash = session.scalars(
            select(Contact).where(
                Contact.org_id == org_id,
                Contact.email == update_data["email"],
                Contact.id != contact_id,
            )
        ).first()
        if clash is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Email {update_data['email']!r} is already used by another contact",
            )

    for field, value in update_data.items():
        setattr(contact, field, value)

    session.flush()
    log_event(
        session,
        org_id=org_id,
        action="contact.update",
        entity_type="contact",
        entity_id=contact.id,
        before_value=before,
        after_value=update_data,
        context={"via": "api"},
    )
    session.commit()
    session.refresh(contact)
    return _attach_roles([contact], session)[0]


@router.delete("/{org_id}/contacts/{contact_id}", status_code=204)
def delete_contact(
    org_id: uuid.UUID,
    contact_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> None:
    contact = _get_contact(session, org_id, contact_id)
    log_event(
        session,
        org_id=org_id,
        action="contact.delete",
        entity_type="contact",
        entity_id=contact.id,
        before_value={"name": contact.name, "email": contact.email},
        context={"via": "api"},
    )
    session.delete(contact)
    session.commit()


# ---------------------------------------------------------------------------
# Documentation role endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{org_id}/contacts/{contact_id}/roles",
    response_model=RoleOut,
    status_code=201,
)
def add_role(
    org_id: uuid.UUID,
    contact_id: uuid.UUID,
    body: RoleIn,
    session: Session = Depends(get_session),
) -> RoleOut:
    contact = _get_contact(session, org_id, contact_id)

    existing_role = session.scalars(
        select(ContactDocumentationRole).where(
            ContactDocumentationRole.contact_id == contact.id,
            ContactDocumentationRole.role == body.role,
        )
    ).first()
    if existing_role is not None:
        raise HTTPException(
            status_code=409, detail=f"Contact already has role {body.role!r}"
        )

    role_row = ContactDocumentationRole(
        contact_id=contact.id, role=body.role, notes=body.notes
    )
    session.add(role_row)
    session.flush()

    log_event(
        session,
        org_id=org_id,
        action="contact_role.add",
        entity_type="contact_documentation_role",
        entity_id=role_row.id,
        after_value={"contact_id": str(contact.id), "role": body.role},
        context={"via": "api"},
    )
    session.commit()
    session.refresh(role_row)
    return RoleOut.model_validate(role_row)


@router.delete(
    "/{org_id}/contacts/{contact_id}/roles/{role}",
    status_code=204,
)
def remove_role(
    org_id: uuid.UUID,
    contact_id: uuid.UUID,
    role: str,
    session: Session = Depends(get_session),
) -> None:
    if role not in _VALID_DOC_ROLES:
        raise HTTPException(
            status_code=422,
            detail=f"role must be one of: {sorted(_VALID_DOC_ROLES)}",
        )
    _get_contact(session, org_id, contact_id)

    role_row = session.scalars(
        select(ContactDocumentationRole).where(
            ContactDocumentationRole.contact_id == contact_id,
            ContactDocumentationRole.role == role,
        )
    ).first()
    if role_row is None:
        raise HTTPException(
            status_code=404, detail=f"Contact does not have role {role!r}"
        )

    log_event(
        session,
        org_id=org_id,
        action="contact_role.remove",
        entity_type="contact_documentation_role",
        entity_id=role_row.id,
        before_value={"contact_id": str(contact_id), "role": role},
        context={"via": "api"},
    )
    session.delete(role_row)
    session.commit()


# ---------------------------------------------------------------------------
# Liongard identities -> contacts import (selection-based, admin-only)
# ---------------------------------------------------------------------------

# Fields identity_to_contact_fields() can plausibly refresh from a later
# Liongard pull. Deliberately excludes affiliation (never Liongard-sourced --
# always an admin decision, made fresh at creation time, not something a
# "refresh" from Liongard data could ever supply) and role_title (no
# Liongard field exists at all, per identity_to_contact_fields' own
# docstring -- nothing to refresh it from).
_REFRESHABLE_FIELDS = frozenset({"name", "phone"})


class LiongardIdentityCandidate(BaseModel):
    liongard_id: str | None = None
    email: str | None = None
    name: str
    phone: str | None = None
    enabled: bool
    has_email: bool
    existing_contact: ContactOut | None = None


class LiongardIdentityListOut(BaseModel):
    source_ref: str
    candidates: list[LiongardIdentityCandidate]
    warnings: list[str] = []


class LiongardContactSelection(BaseModel):
    """One admin-picked row from a prior GET .../import/liongard response.

    contact_id absent -> create a new contact; affiliation is then required
    (validated below -- never guessed, never defaulted, per the same
    reasoning as the Contact model's own docstring).

    contact_id present -> this identity already matches an existing contact
    by email; refresh_fields lists which of that contact's fields the admin
    explicitly chose to overwrite with this selection's data. An empty
    refresh_fields means "leave it, just acknowledge the match" -- re-import
    never silently overwrites a hand-edited value.
    """

    email: str
    name: str
    phone: str | None = None
    role_title: str | None = None
    affiliation: str | None = None
    contact_id: uuid.UUID | None = None
    refresh_fields: list[str] = []

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        v = _normalize_email(v)
        if not v:
            raise ValueError(
                "email must not be blank -- identities with no email cannot be imported"
            )
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("refresh_fields")
    @classmethod
    def validate_refresh_fields(cls, v: list[str]) -> list[str]:
        bad = set(v) - _REFRESHABLE_FIELDS
        if bad:
            raise ValueError(f"refresh_fields may only contain: {sorted(_REFRESHABLE_FIELDS)}")
        return v

    @model_validator(mode="after")
    def _validate_affiliation_required_for_create(self) -> LiongardContactSelection:
        if self.contact_id is None:
            if self.affiliation is None:
                raise ValueError(
                    "affiliation is required to create a new contact -- Liongard cannot "
                    "tell MSP staff from the customer's employees, so it must be set "
                    "explicitly, not guessed"
                )
            if self.affiliation not in _VALID_AFFILIATIONS:
                raise ValueError(f"affiliation must be one of: {sorted(_VALID_AFFILIATIONS)}")
        return self


class LiongardImportIn(BaseModel):
    # Echoed back verbatim from the GET response's own source_ref, same
    # dry-run-data-is-authoritative contract as routers/scope.py's
    # /imports/workbook/apply reading incoming.source_ref from a prior
    # dry-run rather than recomputing it -- this endpoint never re-pulls
    # Liongard itself, it only writes what was already shown and confirmed.
    source_ref: str
    selections: list[LiongardContactSelection]


class LiongardImportResultItem(BaseModel):
    email: str
    outcome: str  # created | refreshed | unchanged | skipped
    contact_id: uuid.UUID | None = None
    detail: str | None = None


class LiongardImportOut(BaseModel):
    results: list[LiongardImportResultItem]


@router.get(
    "/{org_id}/contacts/import/liongard",
    response_model=LiongardIdentityListOut,
)
def list_liongard_identity_candidates(
    org_id: uuid.UUID,
    session: Session = Depends(get_session),
    _current_user: CurrentUser = Depends(require_org_access("msp_admin", "consultant_admin")),
) -> LiongardIdentityListOut:
    """Pull this org's mapped Liongard Environment's identities and present
    them as candidate contacts, cross-referenced against existing contacts
    by normalized email. Read-only -- no Contact rows are written here; the
    admin picks a subset and confirms via POST below. Never writes to
    scope_entity and never calls identities_to_canonical -- see this
    router's module docstring.
    """
    _get_org(session, org_id)
    mapping = get_liongard_mapping(session, org_id)
    if mapping is None:
        raise HTTPException(
            status_code=400,
            detail="No Liongard Environment is mapped to this org yet -- set one first.",
        )
    config, credential = get_liongard_credential(session)

    pulled_at = datetime.now(UTC).isoformat()
    source_ref = build_source_ref(
        mapping.liongard_environment_id, mapping.liongard_environment_name, pulled_at
    )
    try:
        # .records -- pull_identities() returns InventoryPull (records plus
        # the pre-filter total_count, see connectors/liongard.py), not a
        # bare list. This endpoint only needs the already Inventory-state-
        # filtered records; total_count has no use here since there's no
        # dry-run-style pull_status to surface for a contact-candidate list.
        identity_records = liongard_connector.pull_identities(
            config, credential, mapping.liongard_environment_id
        ).records
    except liongard_connector.LiongardAPIError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # Keyed on a re-normalized form, not the stored value as-is -- a
    # pre-existing contact whose email predates the migration
    # 0049_contact_provenance backfill (or that backfill's own conservative
    # collision skip) could still be stored mixed-case; matching against
    # its raw column value would silently miss it.
    existing_by_email = {
        c.email.strip().lower(): c
        for c in session.scalars(select(Contact).where(Contact.org_id == org_id)).all()
    }
    existing_out_by_email = {
        email: out
        for email, out in zip(
            existing_by_email.keys(),
            _attach_roles(list(existing_by_email.values()), session),
            strict=True,
        )
    }

    candidates: list[LiongardIdentityCandidate] = []
    warnings: list[str] = []
    for record in identity_records:
        fields = identity_to_contact_fields(record)
        email = fields["email"].strip().lower() if fields["email"] else None
        name = fields["name"]
        if not email and not name:
            warnings.append(
                "A Liongard identity record had no email, name, or username -- "
                f"skipped (EnvironmentID={record.get('EnvironmentID')!r})."
            )
            continue
        candidates.append(
            LiongardIdentityCandidate(
                liongard_id=str(record["ID"]) if record.get("ID") else None,
                email=email,
                name=name or email or "Unknown",
                phone=fields["phone"],
                enabled=record.get("Enabled") is not False,
                has_email=email is not None,
                existing_contact=existing_out_by_email.get(email) if email else None,
            )
        )
    candidates.sort(key=lambda c: c.name.lower())
    return LiongardIdentityListOut(source_ref=source_ref, candidates=candidates, warnings=warnings)


@router.post(
    "/{org_id}/contacts/import/liongard",
    response_model=LiongardImportOut,
)
def import_liongard_contacts(
    org_id: uuid.UUID,
    body: LiongardImportIn,
    session: Session = Depends(get_session),
    _current_user: CurrentUser = Depends(require_org_access("msp_admin", "consultant_admin")),
) -> LiongardImportOut:
    """Create/refresh contacts from an admin-confirmed selection -- never a
    bulk create-per-identity. Each selection is either a brand-new contact
    (contact_id absent, affiliation required) or an explicit refresh of
    specific fields on an already-matched contact (contact_id present,
    refresh_fields lists what to overwrite). One selection's failure (e.g. a
    stale contact_id) is reported per-item, not raised. One commit for the
    whole batch at the end (not per-item) -- same all-or-nothing contract as
    routers/scope.py's /imports/workbook/apply, which this mirrors.
    """
    _get_org(session, org_id)
    results: list[LiongardImportResultItem] = []

    for sel in body.selections:
        if sel.contact_id is not None:
            contact = session.scalars(
                select(Contact).where(Contact.id == sel.contact_id, Contact.org_id == org_id)
            ).first()
            if contact is None:
                results.append(
                    LiongardImportResultItem(
                        email=sel.email, outcome="skipped", detail="Contact not found"
                    )
                )
                continue

            candidate_values = {"name": sel.name, "phone": sel.phone}
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            for field in sel.refresh_fields:
                new_value = candidate_values[field]
                if getattr(contact, field) != new_value:
                    before[field] = getattr(contact, field)
                    setattr(contact, field, new_value)
                    after[field] = new_value

            if not after:
                results.append(
                    LiongardImportResultItem(
                        email=sel.email, outcome="unchanged", contact_id=contact.id
                    )
                )
                continue

            session.flush()
            log_event(
                session,
                org_id=org_id,
                action="contact.update",
                entity_type="contact",
                entity_id=contact.id,
                before_value=before,
                after_value=after,
                context={"via": "liongard_import", "source_ref": body.source_ref},
            )
            results.append(
                LiongardImportResultItem(
                    email=sel.email, outcome="refreshed", contact_id=contact.id
                )
            )
            continue

        # New contact. Re-checked here (not just trusted from the prior GET)
        # since the admin may be confirming a selection made from a listing
        # that's since gone stale -- same defensive re-check create_contact
        # above already does for the manual-entry path. func.lower() (not a
        # plain == against sel.email, already-normalized as it is) so this
        # still catches a legacy mixed-case row the migration's conservative
        # collision-skip left untouched.
        existing = session.scalars(
            select(Contact).where(
                Contact.org_id == org_id, func.lower(Contact.email) == sel.email
            )
        ).first()
        if existing is not None:
            results.append(
                LiongardImportResultItem(
                    email=sel.email,
                    outcome="skipped",
                    contact_id=existing.id,
                    detail="A contact with this email already exists -- re-select it as a refresh.",
                )
            )
            continue

        contact = Contact(
            org_id=org_id,
            name=sel.name,
            email=sel.email,
            affiliation=sel.affiliation,
            phone=sel.phone,
            role_title=sel.role_title,
            source=Source.LIONGARD.value,
            source_ref=body.source_ref,
        )
        session.add(contact)
        session.flush()
        log_event(
            session,
            org_id=org_id,
            action="contact.create",
            entity_type="contact",
            entity_id=contact.id,
            after_value={
                "name": contact.name,
                "email": contact.email,
                "affiliation": contact.affiliation,
            },
            context={"via": "liongard_import", "source_ref": body.source_ref},
        )
        results.append(
            LiongardImportResultItem(email=sel.email, outcome="created", contact_id=contact.id)
        )

    session.commit()
    return LiongardImportOut(results=results)
