"""Liongard connector.

D.1 (credential entry + test-connection) plus D.2 (environment listing and
the device/identity inventory pull).

Auth: Liongard's own docs (docs.liongard.com/reference/authentication) —
an Access Key ID + Access Key Secret pair generated for a Liongard user
account, base64(id:secret) sent as the X-ROAR-API-KEY header. This key is
scoped to the whole MSP instance (see models.py's IntegrationConnection
docstring) — Environments (per-client tenants) live underneath it, they
don't each get their own key. A dedicated Liongard "Reader" role account is
sufficient — WinGRC never needs write access to Liongard.

Test-connection call: GET /api/v1/environments/count/, the endpoint
Liongard's own docs use to validate a key pair. Uses stdlib urllib (same
convention as auth.py's HIBP k-anonymity check) rather than adding a new
HTTP client dependency.

**API version note (D.2, found while building the inventory pull — not
assumed from the roadmap):** the environments/count test-connection call
above is the only thing that lives under /api/v1/. The actual inventory
endpoints Liongard exposes — confirmed against Liongard's own Postman
collection (docs.liongard.com/reference/postman-collection), since the
published reference pages don't show response schemas — are v2, and are
POST "query" endpoints with a request body, not simple paginated GET
lists:

    POST /api/v2/inventory/device-profiles/query
    POST /api/v2/inventory/identities/query
    body: {"Environment": <int>, "Filters": [...],
           "Sorting": [{"SortBy": <str>, "Direction": "ASC" | "DESC"}],
           "Pagination": {"Page", "PageSize"}}
    response: {"Success": bool, "Data": {"DeviceProfiles" | "Identities": [...],
                                          "Pagination": {"HasMoreRows", ...}}}

**`Sorting` is required, found live against Jarrod's real tenant
(2026-09-15) — not in any published doc.** Its absence 400s with
`{"Success":false,"Message":"Bad request","ValidationErrors":[{"path":
"Sorting","message":"Expected Array<{SortBy: ..., Direction: "ASC"|"DESC"}>
..."}]}` — the error body itself enumerates every valid `SortBy` value,
which is how `DEVICE_PROFILES_SORT_BY`/`IDENTITIES_SORT_BY` below (the
two enums, one per endpoint) were discovered: a deliberately-invalid
`SortBy` sent to each endpoint returns its own full list in the 400 body.
Do this again if Liongard ever adds fields rather than guessing at what
changed — don't hand-edit the two constants from a hunch.

**This connector sorts both endpoints by `ID`.** Not `Hostname` (the
device-side field that would read most naturally): a hostname can be
blank or duplicated across records (a re-imaged machine, a template
clone), which would make page boundaries unstable in exactly the case
this exists to prevent — see `_paginate()`'s own docstring. `ID` is in
both enums, is Liongard's own per-record identifier (returned as a UUID
string in every row observed against the real tenant, e.g.
"036de7e2-299e-4087-8a3c-4cda54cd7b6a"), and is the only field in either
enum guaranteed both present and unique per record — nothing else in
either list carries that guarantee from the API itself. If Liongard's own
`ID` values are ever found to collide (not observed, not expected, but
not contractually promised either by anything published), pagination
stability would need a different approach — this is the best guarantee
available from what the API actually documents about itself.

Environment ids are small integers (e.g. 8815), not UUIDs — see
models.py's OrgLiongardEnvironment.

Every device/identity record carries an `InventoryState` of "Discovery"
(seen, not yet confirmed inside Liongard itself), "Inventory" (confirmed),
or "Archive" (decommissioned/historical). This connector pulls and keeps
only InventoryState="Inventory" rows — pulling "Discovery" rows would feed
Liongard-unconfirmed candidates into a compliance scope list, the same
candidates-vs-confirmed hazard CLAUDE.md's hard rules guard against
elsewhere. Filtered client-side after fetching rather than via the API's
own `Filters` array: the exact filter operator syntax Liongard expects
isn't documented anywhere reachable, and guessing at it risks silently
returning zero rows instead of an honest error — client-side filtering
needs no guess and is cheap at realistic MSP client inventory sizes.

**Inventory-only is a deliberate, permanent product decision (Jarrod,
2026-09-15), not an implementation detail to revisit casually.** Liongard's
Discovery→Inventory promotion is a human confirmation step performed
inside Liongard itself -- an engineer looking at a discovered asset and
saying "yes, this is real and ours." WinGRC treats that confirmation as
authoritative rather than re-doing it: a device or identity only becomes a
scope_entity candidate after a person has already vouched for it once, in
Liongard. **Alternative considered and rejected:** surfacing Discovery-state
records as their own candidates for WinGRC's review flow (i.e., a second,
WinGRC-side confirmation step layered on top of Liongard's). Rejected
because it duplicates a confirmation step that already exists and already
has an owner (whoever runs Liongard for this client), and because it would
blur "confirmed in the source tool" with "confirmed in WinGRC" into a
single ambiguous status -- two review queues for the same underlying fact
is worse than one. If this is ever revisited, it needs its own design
(where do Discovery candidates live, who reviews them, how do they relate
to the existing dry-run/apply flow), not a quiet toggle bolted onto this
filter.

**On a real tenant, this can mean a sync returns a successful, empty
result** (WinsorLabs' own mapped Environment, checked live 2026-09-15: 197
records total, 0 in Inventory state). An empty pull and an empty diff look
identical unless the pre-filter count is surfaced too -- `pull_device_
profiles`/`pull_identities` return an `InventoryPull` (records plus the
pre-filter total) rather than a bare list for exactly this reason;
routers/scope.py's dry-run turns that into the actionable "N found, 0 in
Inventory -- promote them in Liongard" message rather than a silent empty
result that reads as either "broken" or "nothing to do."
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import ConfigField, ConnectorSpec, ConnectorTestResult

_TIMEOUT_SECONDS = 20
_DEFAULT_PAGE_SIZE = 100
# Safety cap on pagination loops (20,000 rows) -- protects against an
# unexpected HasMoreRows=true-forever response rather than any real
# environment size we expect to see.
_MAX_PAGES = 200

# The two Sorting.SortBy enums, discovered from Liongard's own 400
# response body (see this module's docstring for how and why) -- kept as
# real constants, not just docstring prose, so tests/test_liongard_
# connector.py's fake server can import and enforce the exact same list
# instead of a second, hand-copied one that could drift from this one.
# Re-discover (send a deliberately-invalid SortBy, read the enum back out
# of the 400 body) rather than guessing if Liongard ever changes these.
DEVICE_PROFILES_SORT_BY = frozenset({
    "InventoryState", "Hostname", "OperatingSystem", "OSVersion", "WinElevenReady",
    "InternalIP", "MACAddress", "SerialNumber", "DomainRole", "Manufacturer", "Model",
    "ExternalIP", "HardwareID", "Antivirus", "EDR", "Firmware", "LastLogin",
    "LastLoginUser", "Alias", "Class", "Category", "DeviceCategory", "Type", "Role",
    "Location", "PrimarySubnetCidr", "DefaultGateway", "LocationManaged", "Physical",
    "HostServer", "ClusterName", "DataCenter", "VirtualizationSoftware",
    "HypervisorVersion", "ManagedDevice", "Status", "AssetTagNumber", "Purpose",
    "PurchaseDate", "DaysSincePurchaseDate", "WarrantyExpiration", "LicenseExpiration",
    "EOLDate", "LastReviewDate", "ID", "LastSeen", "CreatedOn", "AvailableStorage",
    "DeletedOn", "LastUpdated",
})
IDENTITIES_SORT_BY = frozenset({
    "InventoryState", "IdentityStatus", "Email", "Username", "FirstName", "LastName",
    "Type", "Membership", "Privileged", "AccountActivity", "LastLogin", "MfaStatus",
    "Enabled", "Phone", "Status", "Location", "Department", "EmailLicenses",
    "LiongardBillable", "LastSeen", "ID", "SupportStatus", "AuthorizationStatus",
    "AuthorizationStartDate", "AuthorizationStopDate", "DeletedOn",
})


def _normalize_instance_url(raw: str) -> str:
    """Strip whitespace/trailing slash, and default to https:// when no
    scheme is given -- Liongard is SaaS-only (always HTTPS), and an admin
    pasting just the subdomain (e.g. "us4.app.liongard.com", not
    "https://us4.app.liongard.com") is a completely ordinary mistake the
    config field's own help_text invites without enforcing. Found live on
    wl-util-1 (2026-09-14): urllib.request.Request() raises a bare
    ValueError for a scheme-less URL, and that ValueError isn't caught
    anywhere downstream (not HTTPError, not URLError) -- every caller
    (_test_connection here, and _call() below, shared by
    list_environments/pull_device_profiles/pull_identities) turned an
    ordinary config typo into an uncaught 500 instead of a message naming
    what to fix. Normalizing here fixes the common case silently -- the
    result always contains "://" (urllib's own trigger for "unknown url
    type") once this has run on a non-empty string, so the ValueError
    guards around urllib.request.Request() in _test_connection() and
    _call() below are a defensive fallback for any input this function
    doesn't anticipate, not a path expected to fire in normal use.
    """
    url = raw.strip().rstrip("/")
    if url and "://" not in url:
        url = f"https://{url}"
    return url


def _test_connection(
    config: dict, credential: dict, test_input: str | None = None
) -> ConnectorTestResult:
    # test_input: unused -- this connector's test takes no per-invocation
    # input (there's nothing to "send" to test), see connectors/__init__.py's
    # TestConnectionFn docstring for why the parameter exists at all.
    instance_url = _normalize_instance_url(str(config.get("instance_url") or ""))
    access_key_id = str(credential.get("access_key_id") or "")
    access_key_secret = str(credential.get("access_key_secret") or "")

    if not instance_url:
        return ConnectorTestResult(ok=False, message="Instance URL is not set.")
    if not access_key_id or not access_key_secret:
        return ConnectorTestResult(ok=False, message="Access Key ID/Secret are not set.")

    token = base64.b64encode(f"{access_key_id}:{access_key_secret}".encode()).decode()
    url = f"{instance_url}/api/v1/environments/count/"

    try:
        req = urllib.request.Request(url, headers={"X-ROAR-API-KEY": token})
    except ValueError:
        return ConnectorTestResult(
            ok=False,
            message=(
                f"{instance_url!r} doesn't look like a valid URL — check the Instance URL "
                "field (e.g. https://myinstance.app.liongard.com)."
            ),
        )

    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
            body = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ConnectorTestResult(
                ok=False,
                message=(
                    f"Liongard rejected the key (HTTP {e.code}) — check that it's active "
                    "and has at least Reader access to this instance."
                ),
            )
        return ConnectorTestResult(ok=False, message=f"Liongard API returned HTTP {e.code}.")
    except urllib.error.URLError as e:
        return ConnectorTestResult(ok=False, message=f"Could not reach {instance_url}: {e.reason}")
    except TimeoutError:
        return ConnectorTestResult(ok=False, message=f"Timed out connecting to {instance_url}.")

    count = body.strip()
    try:
        parsed = json.loads(body)
        if isinstance(parsed, int):
            count = str(parsed)
        elif isinstance(parsed, dict) and "count" in parsed:
            count = str(parsed["count"])
    except (ValueError, TypeError):
        pass  # fall back to the raw body text below

    return ConnectorTestResult(ok=True, message=f"Connected — {count} environment(s) visible.")


class LiongardAPIError(Exception):
    """Raised for any Liongard API failure during D.2's environment listing
    or inventory pull. Message is always a specific, human-readable
    explanation of what happened -- never a generic "request failed" --
    matching _test_connection's existing error surfacing above, per D.1's
    "the actual error, not a generic failure" requirement.
    """


@dataclass(frozen=True)
class LiongardEnvironment:
    id: int
    name: str


def _instance_url(config: dict) -> str:
    instance_url = _normalize_instance_url(str(config.get("instance_url") or ""))
    if not instance_url:
        raise LiongardAPIError("Instance URL is not set.")
    return instance_url


def _auth_header(credential: dict) -> dict[str, str]:
    access_key_id = str(credential.get("access_key_id") or "")
    access_key_secret = str(credential.get("access_key_secret") or "")
    if not access_key_id or not access_key_secret:
        raise LiongardAPIError("Access Key ID/Secret are not set.")
    token = base64.b64encode(f"{access_key_id}:{access_key_secret}".encode()).decode()
    return {"X-ROAR-API-KEY": token}


def _call(url: str, headers: dict[str, str], body: dict | None) -> dict | list:
    """One HTTP round-trip (GET if body is None, POST otherwise), raising
    LiongardAPIError with a specific message for every failure mode --
    same discipline as _test_connection, so a wrong Environment id, an
    expired key, or a rate-limit response are each distinguishable to
    whoever reads the dry-run's error, not lumped into one generic string.
    """
    data = json.dumps(body).encode() if body is not None else None
    req_headers = dict(headers)
    if data is not None:
        req_headers["Content-Type"] = "application/json"

    try:
        request = urllib.request.Request(url, data=data, headers=req_headers)
    except ValueError as e:
        raise LiongardAPIError(
            f"{url!r} doesn't look like a valid URL — check the Instance URL field "
            "(e.g. https://myinstance.app.liongard.com)."
        ) from e

    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:  # noqa: S310
            raw = resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode(errors="replace")
        except Exception:  # noqa: BLE001 -- best-effort only, never block on this
            pass
        if e.code in (401, 403):
            raise LiongardAPIError(
                f"Liongard rejected the key (HTTP {e.code}) — check that it's active "
                "and has at least Reader access to this instance."
            ) from e
        if e.code == 429:
            raise LiongardAPIError(
                "Liongard rate-limited this request (HTTP 429) — wait a moment and try again."
            ) from e
        if e.code == 404:
            raise LiongardAPIError(
                f"Liongard returned HTTP 404 for this request — check the Environment id "
                f"is correct. ({url})"
            ) from e
        raise LiongardAPIError(
            f"Liongard API returned HTTP {e.code}: {error_body[:200] or e.reason}"
        ) from e
    except urllib.error.URLError as e:
        raise LiongardAPIError(f"Could not reach Liongard: {e.reason}") from e
    except TimeoutError as e:
        raise LiongardAPIError("Timed out connecting to Liongard.") from e

    try:
        return json.loads(raw)
    except (ValueError, TypeError) as e:
        raise LiongardAPIError("Liongard returned a response that wasn't valid JSON.") from e


_ENV_ID_KEYS = ("EnvironmentID", "ID", "Id", "id")
_ENV_NAME_KEYS = ("Name", "EnvironmentName", "Alias", "DisplayName", "name")


def list_environments(config: dict, credential: dict) -> list[LiongardEnvironment]:
    """List every Environment visible to this MSP's Liongard key -- for the
    org-mapping picker (routers/scope.py's GET .../liongard/environments).

    Defensive response parsing: Liongard's own reference docs for GET
    /api/v1/environments don't publish a response schema (no example
    reachable, unlike the v2 query endpoints -- see this module's
    docstring), so this accepts either a bare JSON list or a dict wrapping
    one under a plausible key, and tries several plausible id/name field
    names per row rather than assuming one. If nothing recognizable is
    found, raises a specific error rather than silently returning an empty
    or wrong list -- the same "surface real errors" discipline as the rest
    of this module.
    """
    instance_url = _instance_url(config)
    headers = _auth_header(credential)
    payload = _call(f"{instance_url}/api/v1/environments", headers, None)

    rows: object = payload
    if isinstance(payload, dict):
        for key in ("Data", "data", "Environments", "environments"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                rows = candidate
                break

    if not isinstance(rows, list):
        raise LiongardAPIError(
            "Liongard's environment list came back in an unexpected shape — "
            "could not find a list of environments in the response."
        )

    environments: list[LiongardEnvironment] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        env_id = next((row[k] for k in _ENV_ID_KEYS if k in row and row[k] is not None), None)
        env_name = next((row[k] for k in _ENV_NAME_KEYS if k in row and row[k]), None)
        if env_id is None:
            continue
        environments.append(LiongardEnvironment(id=int(env_id), name=str(env_name or env_id)))

    if not environments and rows:
        raise LiongardAPIError(
            "Liongard returned environment rows but none had a recognizable id field — "
            "the response shape may not match what this connector expects."
        )
    return environments


def _paginate(
    url: str, headers: dict[str, str], environment_id: int, data_key: str, sort_by: str
) -> list[dict]:
    """Drive one of the v2 inventory query endpoints to exhaustion.

    Loops on Data.Pagination.HasMoreRows rather than trusting a fixed page
    count -- an inventory pull that silently stopped partway through and
    presented itself as complete would mark real, still-present assets
    MISSING in the reconcile diff, exactly the wrong failure mode (see this
    connector's module docstring / ROADMAP.md D.2's "Errors and limits").

    A deterministic Sorting is required for that same reason, not just to
    satisfy Liongard's validator (see this module's docstring for the
    discovery and why `ID` was chosen): without one, page boundaries
    aren't stable while the underlying set is being read across multiple
    requests -- a row can shift between pages and get skipped or
    duplicated, silently, which is exactly the MISSING-in-the-diff failure
    this function exists to prevent. Found live (2026-09-15): the body
    this function sent before had no Sorting key at all and Liongard
    400'd it outright, so this was never actually exercised against a
    real environment before that -- fixed here, not merely worked around.
    """
    all_rows: list[dict] = []
    page = 1
    while True:
        body = {
            "Environment": environment_id,
            "Filters": [],
            "Sorting": [{"SortBy": sort_by, "Direction": "ASC"}],
            "Pagination": {"Page": page, "PageSize": _DEFAULT_PAGE_SIZE},
        }
        payload = _call(url, headers, body)
        if not isinstance(payload, dict) or payload.get("Success") is False:
            raise LiongardAPIError(f"Liongard reported this query as unsuccessful: {payload!r}")

        data = payload.get("Data")
        if not isinstance(data, dict) or data_key not in data:
            raise LiongardAPIError(
                f"Liongard's response is missing the expected {data_key!r} field."
            )
        rows = data[data_key]
        if not isinstance(rows, list):
            raise LiongardAPIError(f"Liongard's {data_key!r} field wasn't a list.")
        all_rows.extend(rows)

        pagination = data.get("Pagination") or {}
        if not pagination.get("HasMoreRows"):
            break
        page += 1
        if page > _MAX_PAGES:
            raise LiongardAPIError(
                f"Stopped after {_MAX_PAGES} pages ({len(all_rows)} rows) without reaching "
                "the end of this environment's inventory — treating this as an error rather "
                "than silently presenting a partial pull as complete."
            )
    return all_rows


@dataclass(frozen=True)
class InventoryPull:
    """Result of one device-profile or identity pull.

    `total_count` is every row Liongard returned for the environment
    (any InventoryState), before the Inventory-only filter this module's
    docstring explains; `records` (and `inventory_count`, its length) is
    what survived it. Kept as two numbers, not just the filtered list, so
    a caller can tell "Liongard returned nothing at all" apart from
    "Liongard returned plenty, none of it confirmed yet" -- see
    routers/scope.py's dry-run for where that distinction actually
    surfaces to a user.
    """

    records: list[dict]
    total_count: int

    @property
    def inventory_count(self) -> int:
        return len(self.records)


def pull_device_profiles(config: dict, credential: dict, environment_id: int) -> InventoryPull:
    """Pull every device from one Liongard Environment, keeping only
    InventoryState="Inventory" rows in `.records` -- importers/liongard.py
    maps those onto CanonicalEntity/the canonical attribute vocabulary.
    """
    instance_url = _instance_url(config)
    headers = _auth_header(credential)
    url = f"{instance_url}/api/v2/inventory/device-profiles/query"
    rows = _paginate(url, headers, environment_id, "DeviceProfiles", sort_by="ID")
    inventory_rows = [r for r in rows if r.get("InventoryState") == "Inventory"]
    return InventoryPull(records=inventory_rows, total_count=len(rows))


def pull_identities(config: dict, credential: dict, environment_id: int) -> InventoryPull:
    """Pull every identity from one Liongard Environment, keeping only
    InventoryState="Inventory" rows in `.records` -- importers/liongard.py
    maps those onto CanonicalEntity(entity_type=PERSON).
    """
    instance_url = _instance_url(config)
    headers = _auth_header(credential)
    url = f"{instance_url}/api/v2/inventory/identities/query"
    rows = _paginate(url, headers, environment_id, "Identities", sort_by="ID")
    inventory_rows = [r for r in rows if r.get("InventoryState") == "Inventory"]
    return InventoryPull(records=inventory_rows, total_count=len(rows))


CONNECTOR = ConnectorSpec(
    key="liongard",
    name="Liongard",
    # Migrated to the ConfigField descriptor shape (2026-09-14) alongside
    # SMTP -- see connectors/__init__.py's module docstring for why. A
    # single required text field renders identically to the old
    # name-only tuple; this is a shape migration, not a redesign.
    config_fields=(
        ConfigField(
            name="instance_url",
            label="Instance URL",
            help_text="The subdomain your team uses to sign in, e.g. "
            "https://myinstance.app.liongard.com.",
        ),
    ),
    credential_fields=("access_key_id", "access_key_secret"),
    hint_field="access_key_secret",
    test_connection=_test_connection,
    help_text=(
        "Generate an Access Key ID/Secret for a Liongard user with the Reader role "
        "(Account Settings → Access Tokens) — WinGRC only needs read access. "
        "Instance URL is the subdomain your team uses to sign in, e.g. "
        "https://myinstance.app.liongard.com."
    ),
)
