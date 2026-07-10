"""Contact and documentation-role endpoints.

GET    /orgs/{org_id}/contacts                        List contacts (includes roles)
POST   /orgs/{org_id}/contacts                        Create contact
GET    /orgs/{org_id}/contacts/{contact_id}           Get one contact
PATCH  /orgs/{org_id}/contacts/{contact_id}           Partial-update contact
DELETE /orgs/{org_id}/contacts/{contact_id}           Delete contact (cascades roles)
POST   /orgs/{org_id}/contacts/{contact_id}/roles     Add documentation role
DELETE /orgs/{org_id}/contacts/{contact_id}/roles/{role}  Remove documentation role

Documentation roles are DOCUMENTATION attributes (who appears in SSP/CRM) — not
platform-access roles. One contact can hold multiple roles (e.g. President +
authorizing_official). The auth-linkage seam runs auth→contact (user.contact_id FK),
never contact→auth.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import log_event
from ..db import get_session
from ..models import Contact, ContactDocumentationRole, Organization

router = APIRouter(prefix="/orgs", tags=["contacts"])

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


class ContactIn(BaseModel):
    name: str
    email: str
    affiliation: str
    phone: str | None = None
    role_title: str | None = None
    contract_ref: str | None = None
    notes: str | None = None

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
