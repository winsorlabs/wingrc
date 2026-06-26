"""Catalog of CMMC "lists" as views over the scope graph.

Each `ListView` is a saved projection: a filter over the scope graph plus an
ordered set of columns. The four views below reproduce the tabs of the
Authorized-Entities workbook, all keyed to AC.L2-3.1.1 (external services also
3.1.20). At assessment-bundle time these render to assessor-ready files; day to
day the same underlying entities are the live source of truth.

Adding a new required list later means adding a ListView here, not maintaining
another spreadsheet by hand.
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain import EntityType


@dataclass(frozen=True)
class ListView:
    id: str
    sheet_title: str
    title: str
    control_ids: tuple[str, ...]
    entity_type: EntityType
    # Ordered (attribute_key, display_header). attribute_key matches the raw
    # workbook header captured at import time, so round-trips are faithful.
    columns: tuple[tuple[str, str], ...]
    description: str = ""


AUTHORIZED_USERS = ListView(
    id="3.1.1a-authorized-users",
    sheet_title="3.1.1a Authorized Users",
    title="Authorized Users - Active Directory",
    control_ids=("AC.L2-3.1.1",),
    entity_type=EntityType.PERSON,
    columns=(
        ("First Name", "First Name"),
        ("Last Name", "Last Name"),
        ("Data Intake Admin", "Data Intake Admin"),
        ("Remote Access", "Remote Access"),
        ("Requested By/Responsible Party", "Requested By/Responsible Party"),
        ("Start Date", "Start Date"),
        ("End Date", "End Date"),
    ),
    description="All authorized AD users in the CUI boundary.",
)

AUTHORIZED_PROCESSES = ListView(
    id="3.1.1b-auth-processes",
    sheet_title="3.1.1b Auth Processes",
    title="Processes Acting on Behalf of Authorized Users",
    control_ids=("AC.L2-3.1.1",),
    entity_type=EntityType.PROCESS,
    columns=(
        ("Process Name", "Process Name"),
        ("Running On", "Running On"),
        ("Associated Account", "Associated Account"),
        ("Description / Purpose", "Description / Purpose"),
    ),
    description="Service accounts and scheduled tasks acting on behalf of users.",
)

AUTHORIZED_DEVICES = ListView(
    id="3.1.1c-authorized-devices",
    sheet_title="3.1.1c Authorized Devices",
    title="Authorized Devices",
    control_ids=("AC.L2-3.1.1",),
    entity_type=EntityType.DEVICE,
    columns=(
        ("Name", "Name"),
        ("Owner / Primary User", "Owner / Primary User"),
        ("Make", "Make"),
        ("Model", "Model"),
        ("Serial # or Asset Tag", "Serial # or Asset Tag"),
        ("Mac Address", "Mac Address"),
        ("OS", "OS"),
        ("BIOS FW Ver", "BIOS FW Ver"),
        ("Location", "Location"),
        ("Asset Type", "Asset Type"),
        ("In Service Date", "In Service Date"),
        ("Decommissioned Date", "Decommissioned Date"),
        # These agent columns are the join to the curated stack library.
        ("FenixPyre Installed", "FenixPyre Installed"),
        ("DUO Installed", "DUO Installed"),
        ("Senteon Installed", "Senteon Installed"),
        ("RoboShadow Installed", "RoboShadow Installed"),
        ("Heimdal Installed", "Heimdal Installed"),
    ),
    description="Every authorized device in the CUI boundary with installed agents.",
)

EXTERNAL_SERVICES = ListView(
    id="external-services",
    sheet_title="External Services",
    title="External / Cloud Services",
    control_ids=("AC.L2-3.1.1", "AC.L2-3.1.20"),
    entity_type=EntityType.EXTERNAL_SERVICE,
    columns=(
        ("Name", "Name"),
        ("Provider", "Provider"),
        ("Asset Type", "Asset Type"),
    ),
    description="External/cloud services that interact with the boundary (ESP/CSP).",
)

ALL_VIEWS: tuple[ListView, ...] = (
    AUTHORIZED_USERS,
    AUTHORIZED_PROCESSES,
    AUTHORIZED_DEVICES,
    EXTERNAL_SERVICES,
)

VIEWS_BY_ID: dict[str, ListView] = {v.id: v for v in ALL_VIEWS}
