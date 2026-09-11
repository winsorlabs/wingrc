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
    body: {"Environment": <int>, "Filters": [...], "Pagination": {"Page", "PageSize"}}
    response: {"Success": bool, "Data": {"DeviceProfiles" | "Identities": [...],
                                          "Pagination": {"HasMoreRows", ...}}}

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
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import ConnectorSpec, ConnectorTestResult

_TIMEOUT_SECONDS = 20
_DEFAULT_PAGE_SIZE = 100
# Safety cap on pagination loops (20,000 rows) -- protects against an
# unexpected HasMoreRows=true-forever response rather than any real
# environment size we expect to see.
_MAX_PAGES = 200


def _test_connection(config: dict, credential: dict) -> ConnectorTestResult:
    instance_url = str(config.get("instance_url") or "").strip().rstrip("/")
    access_key_id = str(credential.get("access_key_id") or "")
    access_key_secret = str(credential.get("access_key_secret") or "")

    if not instance_url:
        return ConnectorTestResult(ok=False, message="Instance URL is not set.")
    if not access_key_id or not access_key_secret:
        return ConnectorTestResult(ok=False, message="Access Key ID/Secret are not set.")

    token = base64.b64encode(f"{access_key_id}:{access_key_secret}".encode()).decode()
    url = f"{instance_url}/api/v1/environments/count/"
    req = urllib.request.Request(url, headers={"X-ROAR-API-KEY": token})

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
    instance_url = str(config.get("instance_url") or "").strip().rstrip("/")
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
    request = urllib.request.Request(url, data=data, headers=req_headers)

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


def _paginate(url: str, headers: dict[str, str], environment_id: int, data_key: str) -> list[dict]:
    """Drive one of the v2 inventory query endpoints to exhaustion.

    Loops on Data.Pagination.HasMoreRows rather than trusting a fixed page
    count -- an inventory pull that silently stopped partway through and
    presented itself as complete would mark real, still-present assets
    MISSING in the reconcile diff, exactly the wrong failure mode (see this
    connector's module docstring / ROADMAP.md D.2's "Errors and limits").
    """
    all_rows: list[dict] = []
    page = 1
    while True:
        body = {
            "Environment": environment_id,
            "Filters": [],
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


def pull_device_profiles(config: dict, credential: dict, environment_id: int) -> list[dict]:
    """Pull every InventoryState="Inventory" device from one Liongard
    Environment. Returns raw Liongard records -- importers/liongard.py maps
    them onto CanonicalEntity/the canonical attribute vocabulary.
    """
    instance_url = _instance_url(config)
    headers = _auth_header(credential)
    url = f"{instance_url}/api/v2/inventory/device-profiles/query"
    rows = _paginate(url, headers, environment_id, "DeviceProfiles")
    return [r for r in rows if r.get("InventoryState") == "Inventory"]


def pull_identities(config: dict, credential: dict, environment_id: int) -> list[dict]:
    """Pull every InventoryState="Inventory" identity from one Liongard
    Environment. Returns raw Liongard records -- importers/liongard.py maps
    them onto CanonicalEntity(entity_type=PERSON).
    """
    instance_url = _instance_url(config)
    headers = _auth_header(credential)
    url = f"{instance_url}/api/v2/inventory/identities/query"
    rows = _paginate(url, headers, environment_id, "Identities")
    return [r for r in rows if r.get("InventoryState") == "Inventory"]


CONNECTOR = ConnectorSpec(
    key="liongard",
    name="Liongard",
    config_fields=("instance_url",),
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
