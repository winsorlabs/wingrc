"""Unit tests for connectors/liongard.py's D.2 additions: environment
listing, device/identity pagination, InventoryState filtering, and error
surfacing. No real network calls -- urllib.request.urlopen is monkeypatched
to return canned responses shaped like real Liongard payloads (captured
from Liongard's own Postman collection, see this module's docstring).

test_connection (D.1)'s general behavior (RBAC, credential storage, audit
events) has its own coverage in test_integrations.py, fully mocked at the
ConnectorSpec level -- it never exercises this module's own URL-handling
code. _normalize_instance_url() and the ValueError guards around
urllib.request.Request() ARE retested here (2026-09-14, the scheme-less-
instance_url 500 found live on wl-util-1) since that's specifically about
this module's own logic, not the router around it.

**Mock-fidelity fix (2026-09-15):** every v2-query fake_urlopen in this
file now routes the request through _validate_query_body() before
returning its canned success payload -- it rejects a missing/invalid
Sorting the same way Liongard's real API does (see connectors/liongard.py's
own docstring for the missing-Sorting 400 this closes). Before this, the
fakes here accepted any request body, which is exactly how the connector's
own missing-Sorting bug shipped without a single local test catching it --
a mock that's more permissive than the real service validates the
connector against itself, not against the contract. This is the second
time a gap has surfaced only under real conditions (the first: the
scheme-less-URL 500 above) -- see docs/roadmap.md's D.2 entry for this
recorded as a pattern, not just an incident.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from app.connectors import liongard

_CONFIG = {"instance_url": "https://myinstance.app.liongard.com"}
_CREDENTIAL = {"access_key_id": "AKIDEXAMPLE", "access_key_secret": "s3cr3t"}


class _FakeResponse:
    def __init__(self, body: dict | list):
        self._body = json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# Mock-fidelity fix (2026-09-15): every fake_urlopen above and below that
# stands in for a v2 inventory query endpoint used to accept ANY request
# body -- it validated the connector against itself, not against Liongard's
# real contract. That's exactly what let the missing-Sorting 400 (this
# module's own docstring) ship: no test here ever inspected the posted
# body for a v2 query call, so a hand-authored body missing a field
# Liongard actually requires sailed through every existing test.
#
# _query_urlopen() is the fix: a fake_urlopen that validates Sorting the
# same way Liongard's own validator does (required, non-empty, SortBy in
# the endpoint-specific enum discovered in connectors/liongard.py, real
# 400 body shape on failure) before ever handing off to the caller's own
# success-payload logic. Every v2-query test below (existing and new) now
# goes through this rather than a bespoke lambda that only ever checked
# the URL or nothing at all.
_ENDPOINT_SORT_BY = {
    "/api/v2/inventory/device-profiles/query": liongard.DEVICE_PROFILES_SORT_BY,
    "/api/v2/inventory/identities/query": liongard.IDENTITIES_SORT_BY,
}


class _FakeHTTPError(urllib.error.HTTPError):
    """A real urllib.error.HTTPError whose .read() returns a body shaped
    exactly like Liongard's own 400 response -- so the connector's actual
    error-handling path (_call()'s except HTTPError branch) is what's
    under test, not a shortcut."""

    def __init__(self, url: str, code: int, body: dict):
        payload = json.dumps(body).encode()
        super().__init__(url, code, body.get("Message", "error"), {}, None)
        self._payload = payload

    def read(self):
        return self._payload


def _validate_query_body(request) -> None:
    """Raises _FakeHTTPError(400, ...) exactly the way Liongard's real
    validator does -- the request body inspection this whole test file was
    missing before. Covers every required field confirmed live against
    Jarrod's tenant (2026-09-15), not just Sorting (the one that actually
    shipped broken): Environment ("Expected number but instead got:
    undefined" when absent) and Filters ("Expected Array<...>" when
    absent) are ALSO required and were ALSO unvalidated here before this
    fix -- the connector already sends both unconditionally so neither is
    a live bug, but an unvalidated mock wouldn't have caught it if it
    were, the same blind spot Sorting fell into. Pagination is NOT
    required (confirmed: omitting it live returns 200), so it's
    deliberately not checked here.
    """
    matches = (sbv for path, sbv in _ENDPOINT_SORT_BY.items() if path in request.full_url)
    valid_sort_by = next(matches, None)
    if valid_sort_by is None:
        return  # not a v2 query endpoint (e.g. GET /api/v1/environments) -- no body to check

    body = json.loads(request.data) if request.data else {}

    if not isinstance(body.get("Environment"), int):
        raise _FakeHTTPError(
            request.full_url,
            400,
            {
                "Success": False,
                "Message": "Bad request",
                "ValidationErrors": [
                    {
                        "path": "Environment",
                        "message": (
                            "Expected number but instead got: "
                            f"{json.dumps(body.get('Environment'))}."
                        ),
                    }
                ],
            },
        )
    if not isinstance(body.get("Filters"), list):
        raise _FakeHTTPError(
            request.full_url,
            400,
            {
                "Success": False,
                "Message": "Bad request",
                "ValidationErrors": [
                    {
                        "path": "Filters",
                        "message": (
                            "Expected Array<...> but instead got: "
                            f"{json.dumps(body.get('Filters'))}."
                        ),
                    }
                ],
            },
        )

    sorting = body.get("Sorting")
    if not isinstance(sorting, list) or not sorting:
        raise _FakeHTTPError(
            request.full_url,
            400,
            {
                "Success": False,
                "Message": "Bad request",
                "ValidationErrors": [
                    {
                        "path": "Sorting",
                        "message": (
                            f"Expected Array<{{ SortBy: ...valid values..., "
                            f'Direction: "ASC" | "DESC" }}> but instead got: '
                            f"{json.dumps(sorting)}."
                        ),
                    }
                ],
            },
        )
    for entry in sorting:
        sort_by = entry.get("SortBy") if isinstance(entry, dict) else None
        if sort_by not in valid_sort_by:
            raise _FakeHTTPError(
                request.full_url,
                400,
                {
                    "Success": False,
                    "Message": "Bad request",
                    "ValidationErrors": [
                        {
                            "path": "Sorting",
                            "message": f"{sort_by!r} is not a valid SortBy for this endpoint.",
                        }
                    ],
                },
            )
        if entry.get("Direction") not in ("ASC", "DESC"):
            raise _FakeHTTPError(
                request.full_url,
                400,
                {
                    "Success": False,
                    "Message": "Bad request",
                    "ValidationErrors": [
                        {"path": "Sorting", "message": "Direction must be ASC or DESC."}
                    ],
                },
            )


def _query_urlopen(page_for_request):
    """Build a fake_urlopen for a v2 query endpoint: validates Sorting
    like the real API (raising the real 400 shape on failure), then calls
    page_for_request(request) -> dict|list for the response body on
    success. page_for_request can also just be a fixed dict/list."""

    def fake_urlopen(request, timeout):
        _validate_query_body(request)
        response = page_for_request(request) if callable(page_for_request) else page_for_request
        return _FakeResponse(response)

    return fake_urlopen


def _device_row(inventory_state: str, hostname: str) -> dict:
    return {
        "ID": f"id-{hostname}",
        "EnvironmentID": 8815,
        "InventoryState": inventory_state,
        "Hostname": hostname,
        "SerialNumber": f"SN-{hostname}",
        "MACAddress": ["14:9d:99:8b:72:36"],
        "Manufacturer": "Apple",
        "Model": "Macmini8,1",
        "OperatingSystem": "macOS",
        "Type": "desktop",
    }


def test_list_environments_parses_bare_list(monkeypatch):
    payload = [{"EnvironmentID": 8815, "Name": "Acme Corp"}, {"ID": 42, "Name": "Beta LLC"}]

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://myinstance.app.liongard.com/api/v1/environments"
        return _FakeResponse(payload)

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    environments = liongard.list_environments(_CONFIG, _CREDENTIAL)
    assert environments == [
        liongard.LiongardEnvironment(id=8815, name="Acme Corp"),
        liongard.LiongardEnvironment(id=42, name="Beta LLC"),
    ]


def test_list_environments_parses_wrapped_list(monkeypatch):
    payload = {"Data": [{"EnvironmentID": 1, "Name": "Only One"}]}
    monkeypatch.setattr(
        liongard.urllib.request, "urlopen", lambda request, timeout: _FakeResponse(payload)
    )
    environments = liongard.list_environments(_CONFIG, _CREDENTIAL)
    assert environments == [liongard.LiongardEnvironment(id=1, name="Only One")]


def test_list_environments_unrecognized_shape_raises_clear_error(monkeypatch):
    monkeypatch.setattr(
        liongard.urllib.request, "urlopen", lambda request, timeout: _FakeResponse({"nonsense": 1})
    )
    with pytest.raises(liongard.LiongardAPIError, match="unexpected shape"):
        liongard.list_environments(_CONFIG, _CREDENTIAL)


def test_list_environments_rows_without_id_field_raise_clear_error(monkeypatch):
    monkeypatch.setattr(
        liongard.urllib.request,
        "urlopen",
        lambda request, timeout: _FakeResponse([{"SomethingElse": "x"}]),
    )
    with pytest.raises(liongard.LiongardAPIError, match="recognizable id field"):
        liongard.list_environments(_CONFIG, _CREDENTIAL)


# ---------------------------------------------------------------------------
# Sorting -- required by the real API (2026-09-15), and load-bearing for
# pagination stability, not just to satisfy a validator (see _paginate's
# own docstring for why an unstable page boundary is the exact silent-
# MISSING-in-the-diff failure this module exists to prevent).
# ---------------------------------------------------------------------------


def test_pull_device_profiles_sends_id_sort_ascending(monkeypatch):
    payload = {
        "Success": True,
        "Data": {"DeviceProfiles": [], "Pagination": {"HasMoreRows": False}},
    }
    seen_sorting = []

    def fake_urlopen(request, timeout):
        _validate_query_body(request)
        seen_sorting.append(json.loads(request.data)["Sorting"])
        return _FakeResponse(payload)

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)
    assert seen_sorting == [[{"SortBy": "ID", "Direction": "ASC"}]]


def test_pull_identities_sends_id_sort_ascending(monkeypatch):
    payload = {
        "Success": True,
        "Data": {"Identities": [], "Pagination": {"HasMoreRows": False}},
    }
    seen_sorting = []

    def fake_urlopen(request, timeout):
        _validate_query_body(request)
        seen_sorting.append(json.loads(request.data)["Sorting"])
        return _FakeResponse(payload)

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    liongard.pull_identities(_CONFIG, _CREDENTIAL, 8815)
    assert seen_sorting == [[{"SortBy": "ID", "Direction": "ASC"}]]


def test_missing_sorting_surfaces_as_liongard_api_error_not_a_crash(monkeypatch):
    """The real regression this whole section exists for: before this fix,
    _paginate() sent no Sorting key at all. Confirms Liongard's real 400
    for that shape (reproduced by the validating mock) surfaces through
    _call()'s existing HTTPError branch as a clean LiongardAPIError, not
    an uncaught exception -- built by hand here (bypassing
    pull_device_profiles' own now-correct call) specifically to prove the
    *handling* holds independently of the *fix*, since Liongard rejecting
    a request for some other future reason has to hit this same path."""
    monkeypatch.setattr(liongard.urllib.request, "urlopen", _query_urlopen({}))
    body_missing_sorting = {"Environment": 8815, "Filters": [], "Pagination": {"Page": 1}}

    with pytest.raises(liongard.LiongardAPIError, match="Bad request"):
        liongard._call(
            "https://myinstance.app.liongard.com/api/v2/inventory/device-profiles/query",
            liongard._auth_header(_CREDENTIAL),
            body_missing_sorting,
        )


def test_pull_device_profiles_paginates_until_has_more_rows_false(monkeypatch):
    pages = [
        {
            "Success": True,
            "Data": {
                "DeviceProfiles": [_device_row("Inventory", "host-1")],
                "Pagination": {"HasMoreRows": True, "CurrentPage": 1},
            },
        },
        {
            "Success": True,
            "Data": {
                "DeviceProfiles": [_device_row("Inventory", "host-2")],
                "Pagination": {"HasMoreRows": False, "CurrentPage": 2},
            },
        },
    ]
    calls = []

    def fake_urlopen(request, timeout):
        _validate_query_body(request)
        body = json.loads(request.data)
        calls.append(body["Pagination"]["Page"])
        return _FakeResponse(pages[body["Pagination"]["Page"] - 1])

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    pull = liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)
    assert calls == [1, 2]
    assert [r["Hostname"] for r in pull.records] == ["host-1", "host-2"]
    assert pull.total_count == 2
    assert pull.inventory_count == 2


def test_pull_device_profiles_filters_out_non_inventory_state(monkeypatch):
    payload = {
        "Success": True,
        "Data": {
            "DeviceProfiles": [
                _device_row("Inventory", "kept"),
                _device_row("Discovery", "dropped-discovery"),
                _device_row("Archive", "dropped-archive"),
            ],
            "Pagination": {"HasMoreRows": False},
        },
    }
    monkeypatch.setattr(liongard.urllib.request, "urlopen", _query_urlopen(payload))
    pull = liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)
    assert [r["Hostname"] for r in pull.records] == ["kept"]
    # total_count is pre-filter -- all 3 rows Liongard returned, not just
    # the 1 that survived InventoryState=="Inventory".
    assert pull.total_count == 3
    assert pull.inventory_count == 1


def test_pull_device_profiles_all_discovery_reports_total_found_but_zero_inventory(monkeypatch):
    """The exact shape found live on Jarrod's real tenant (2026-09-15):
    197 real records, all Discovery, 0 Inventory -- a successful pull with
    nothing to compare. total_count must still reflect what was actually
    returned so routers/scope.py can distinguish this from "Liongard
    returned nothing at all."
    """
    payload = {
        "Success": True,
        "Data": {
            "DeviceProfiles": [_device_row("Discovery", f"host-{i}") for i in range(5)],
            "Pagination": {"HasMoreRows": False},
        },
    }
    monkeypatch.setattr(liongard.urllib.request, "urlopen", _query_urlopen(payload))
    pull = liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)
    assert pull.records == []
    assert pull.total_count == 5
    assert pull.inventory_count == 0


def test_pull_identities_uses_identities_endpoint_and_data_key(monkeypatch):
    payload = {
        "Success": True,
        "Data": {
            "Identities": [{"InventoryState": "Inventory", "Email": "a@example.com"}],
            "Pagination": {"HasMoreRows": False},
        },
    }
    seen_urls = []

    def fake_urlopen(request, timeout):
        _validate_query_body(request)
        seen_urls.append(request.full_url)
        return _FakeResponse(payload)

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    pull = liongard.pull_identities(_CONFIG, _CREDENTIAL, 8815)
    assert pull.records == [{"InventoryState": "Inventory", "Email": "a@example.com"}]
    assert pull.total_count == 1
    assert seen_urls == ["https://myinstance.app.liongard.com/api/v2/inventory/identities/query"]


def test_pagination_stops_at_max_pages_rather_than_looping_forever(monkeypatch):
    payload = {
        "Success": True,
        "Data": {"DeviceProfiles": [], "Pagination": {"HasMoreRows": True}},
    }
    monkeypatch.setattr(liongard.urllib.request, "urlopen", _query_urlopen(payload))
    with pytest.raises(liongard.LiongardAPIError, match="Stopped after"):
        liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)


def test_missing_data_key_raises_clear_error(monkeypatch):
    payload = {"Success": True, "Data": {"Pagination": {"HasMoreRows": False}}}
    monkeypatch.setattr(liongard.urllib.request, "urlopen", _query_urlopen(payload))
    with pytest.raises(liongard.LiongardAPIError, match="DeviceProfiles"):
        liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)


def test_success_false_raises_clear_error(monkeypatch):
    payload = {"Success": False, "Data": {}}
    monkeypatch.setattr(liongard.urllib.request, "urlopen", _query_urlopen(payload))
    with pytest.raises(liongard.LiongardAPIError, match="unsuccessful"):
        liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)


@pytest.mark.parametrize(
    ("http_code", "expected_match"),
    [(401, "rejected the key"), (403, "rejected the key"), (429, "rate-limited"), (404, "404")],
)
def test_http_error_codes_surface_specific_messages(monkeypatch, http_code, expected_match):
    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url, http_code, "error", {}, None
        )

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(liongard.LiongardAPIError, match=expected_match):
        liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)


def test_url_error_surfaces_reachability_message(monkeypatch):
    def fake_urlopen(request, timeout):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(liongard.LiongardAPIError, match="Could not reach Liongard"):
        liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)


def test_missing_instance_url_raises_before_any_request(monkeypatch):
    called = False

    def fake_urlopen(request, timeout):
        nonlocal called
        called = True
        return _FakeResponse({})

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(liongard.LiongardAPIError, match="Instance URL"):
        liongard.pull_device_profiles({}, _CREDENTIAL, 8815)
    assert called is False


def test_missing_credential_raises_before_any_request(monkeypatch):
    called = False

    def fake_urlopen(request, timeout):
        nonlocal called
        called = True
        return _FakeResponse({})

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(liongard.LiongardAPIError, match="Access Key"):
        liongard.pull_device_profiles(_CONFIG, {}, 8815)
    assert called is False


# ---------------------------------------------------------------------------
# Instance URL normalization (2026-09-14) -- a scheme-less instance_url
# ("us4.app.liongard.com", not "https://us4.app.liongard.com") 500'd live
# on wl-util-1: urllib.request.Request() raises a bare ValueError that
# wasn't caught anywhere, in every one of the four code paths that build a
# Liongard request (test-connection, environment listing x2 call sites,
# the sync dry-run's device/identity pull) -- a config typo the field's
# own help_text invites without enforcing must surface as a message naming
# what to fix, not a 500 (CLAUDE.md's own standard for this class of bug).
# ---------------------------------------------------------------------------


def test_normalize_instance_url_adds_https_when_scheme_missing():
    assert (
        liongard._normalize_instance_url("us4.app.liongard.com")
        == "https://us4.app.liongard.com"
    )


def test_normalize_instance_url_leaves_existing_scheme_alone():
    assert (
        liongard._normalize_instance_url("http://myinstance.app.liongard.com")
        == "http://myinstance.app.liongard.com"
    )


def test_normalize_instance_url_strips_whitespace_and_trailing_slash():
    assert (
        liongard._normalize_instance_url("  us4.app.liongard.com/  ")
        == "https://us4.app.liongard.com"
    )


def test_normalize_instance_url_empty_stays_empty():
    assert liongard._normalize_instance_url("") == ""
    assert liongard._normalize_instance_url("   ") == ""


def test_list_environments_normalizes_scheme_less_instance_url(monkeypatch):
    """The exact live 500: a bare-subdomain instance_url must still reach
    a real request (over https://), not raise ValueError."""

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://us4.app.liongard.com/api/v1/environments"
        return _FakeResponse([{"EnvironmentID": 1, "Name": "Acme"}])

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    config = {"instance_url": "us4.app.liongard.com"}
    environments = liongard.list_environments(config, _CREDENTIAL)
    assert environments == [liongard.LiongardEnvironment(id=1, name="Acme")]


def test_pull_device_profiles_normalizes_scheme_less_instance_url(monkeypatch):
    def fake_urlopen(request, timeout):
        _validate_query_body(request)
        assert request.full_url == "https://us4.app.liongard.com/api/v2/inventory/device-profiles/query"
        return _FakeResponse(
            {"Success": True, "Data": {"DeviceProfiles": [], "Pagination": {"HasMoreRows": False}}}
        )

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    config = {"instance_url": "us4.app.liongard.com"}
    assert liongard.pull_device_profiles(config, _CREDENTIAL, 8815).records == []


def test_test_connection_normalizes_scheme_less_instance_url(monkeypatch):
    """Direct coverage of _test_connection's own URL handling -- the
    general test_connection flow (RBAC, credential storage) is covered at
    the router level in test_integrations.py, fully mocked; this is the
    one behavior that lives in this module specifically."""

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://us4.app.liongard.com/api/v1/environments/count/"
        return _FakeResponse(3)

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    config = {"instance_url": "us4.app.liongard.com"}
    result = liongard._test_connection(config, _CREDENTIAL)
    assert result.ok is True
    assert "3 environment(s)" in result.message
