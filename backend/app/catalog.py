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

# Pseudo-sources for ListView.sources: values that live on the scope_entity
# row itself rather than in `attributes`.
SCOPE_CATEGORY = "@scope_category"
NATURAL_KEY = "@natural_key"


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
    # Where a column's value may come from, in priority order, for columns
    # whose value is not only ever under the raw workbook header. Three
    # writers fill scope_entity.attributes with different key sets -- the
    # workbook keeps its raw headers, Liongard writes canonical keys
    # (make_oem, display_name, ...) beside its own raw record, and an
    # operator writes the overlay keys -- and a list must show the value
    # whichever writer supplied it. Columns absent here read their own key.
    sources: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Shown in place of an empty table, in the UI and in the exported
    # sheet, when nothing populates this view on its own.
    empty_explanation: str = ""

    def column_sources(self, attr_key: str) -> tuple[str, ...]:
        for key, srcs in self.sources:
            if key == attr_key:
                return srcs
        return (attr_key,)


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
    sources=(
        ("First Name", ("First Name", "FirstName")),
        ("Last Name", ("Last Name", "LastName")),
        ("Requested By/Responsible Party", ("requested_by", "Requested By/Responsible Party")),
    ),
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
    # EntityType.PROCESS has no connector: only a workbook import or manual
    # entry ever creates one, so an empty list here is the normal state,
    # not a failed sync.
    empty_explanation=(
        "No processes recorded. Service accounts and agents acting on behalf of "
        "users (e.g. an RMM agent's service account) are not discovered by any "
        "sync -- add them manually."
    ),
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
        ("Device Subtype", "Device Subtype"),
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
    sources=(
        ("Name", ("Name", "display_name", "Hostname", NATURAL_KEY)),
        ("Make", ("Make", "make_oem")),
        ("Model", ("Model", "model")),
        ("Device Subtype", ("Device Subtype", "device_subtype")),
        ("Serial # or Asset Tag", ("Serial # or Asset Tag", "asset_tag", "SerialNumber")),
        ("Mac Address", ("Mac Address", "mac_addresses")),
        ("OS", ("OS", "version")),
        # Operator overlay first: it is the edit surface, so a hand
        # correction outranks the value an earlier workbook import stored.
        ("Location", ("location", "Location")),
        # The constrained column, not the free-text cell it was parsed from.
        ("Asset Type", (SCOPE_CATEGORY, "Asset Type")),
        ("In Service Date", ("in_service_date", "In Service Date")),
        ("Decommissioned Date", ("decommissioned_date", "Decommissioned Date")),
    ),
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
    sources=(("Asset Type", (SCOPE_CATEGORY, "Asset Type")),),
)

ALL_VIEWS: tuple[ListView, ...] = (
    AUTHORIZED_USERS,
    AUTHORIZED_PROCESSES,
    AUTHORIZED_DEVICES,
    EXTERNAL_SERVICES,
)

VIEWS_BY_ID: dict[str, ListView] = {v.id: v for v in ALL_VIEWS}
