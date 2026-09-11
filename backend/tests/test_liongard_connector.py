"""Unit tests for connectors/liongard.py's D.2 additions: environment
listing, device/identity pagination, InventoryState filtering, and error
surfacing. No real network calls -- urllib.request.urlopen is monkeypatched
to return canned responses shaped like real Liongard payloads (captured
from Liongard's own Postman collection, see this module's docstring).

test_connection (D.1) already has its own coverage in test_integrations.py
and isn't retested here.
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
        body = json.loads(request.data)
        calls.append(body["Pagination"]["Page"])
        return _FakeResponse(pages[body["Pagination"]["Page"] - 1])

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    rows = liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)
    assert calls == [1, 2]
    assert [r["Hostname"] for r in rows] == ["host-1", "host-2"]


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
    monkeypatch.setattr(
        liongard.urllib.request, "urlopen", lambda request, timeout: _FakeResponse(payload)
    )
    rows = liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)
    assert [r["Hostname"] for r in rows] == ["kept"]


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
        seen_urls.append(request.full_url)
        return _FakeResponse(payload)

    monkeypatch.setattr(liongard.urllib.request, "urlopen", fake_urlopen)
    rows = liongard.pull_identities(_CONFIG, _CREDENTIAL, 8815)
    assert rows == [{"InventoryState": "Inventory", "Email": "a@example.com"}]
    assert seen_urls == ["https://myinstance.app.liongard.com/api/v2/inventory/identities/query"]


def test_pagination_stops_at_max_pages_rather_than_looping_forever(monkeypatch):
    payload = {
        "Success": True,
        "Data": {"DeviceProfiles": [], "Pagination": {"HasMoreRows": True}},
    }
    monkeypatch.setattr(
        liongard.urllib.request, "urlopen", lambda request, timeout: _FakeResponse(payload)
    )
    with pytest.raises(liongard.LiongardAPIError, match="Stopped after"):
        liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)


def test_missing_data_key_raises_clear_error(monkeypatch):
    payload = {"Success": True, "Data": {"Pagination": {"HasMoreRows": False}}}
    monkeypatch.setattr(
        liongard.urllib.request, "urlopen", lambda request, timeout: _FakeResponse(payload)
    )
    with pytest.raises(liongard.LiongardAPIError, match="DeviceProfiles"):
        liongard.pull_device_profiles(_CONFIG, _CREDENTIAL, 8815)


def test_success_false_raises_clear_error(monkeypatch):
    payload = {"Success": False, "Data": {}}
    monkeypatch.setattr(
        liongard.urllib.request, "urlopen", lambda request, timeout: _FakeResponse(payload)
    )
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
