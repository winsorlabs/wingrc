"""Unit tests for web_fetch.py's SSRF defenses (§4/§8). No real network
access anywhere in this file -- every test either uses a literal IP
address (getaddrinfo resolves that instantly and locally, no DNS) or
monkeypatches web_fetch._raw_get / socket.getaddrinfo directly. §8 is
explicit that a partial implementation here is the same as none, so each
defense (scheme, private-address literal, resolved-hostname, redirect
hop, robots.txt) gets its own direct test rather than one combined check.
"""
from __future__ import annotations

import inspect
import ipaddress
import socket

import pytest

from app import web_fetch

# ---------------------------------------------------------------------------
# Address classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "addr",
    [
        "127.0.0.1",
        "127.0.0.53",
        "10.0.0.1",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata
        "169.254.0.1",
        "0.0.0.0",
        "224.0.0.1",  # multicast
        "::1",
        "fc00::1",  # unique local
        "fe80::1",  # link-local
    ],
)
def test_is_blocked_ip_rejects_private_and_special_use(addr):
    assert web_fetch._is_blocked_ip(ipaddress.ip_address(addr)) is True


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"])
def test_is_blocked_ip_allows_real_public_addresses(addr):
    assert web_fetch._is_blocked_ip(ipaddress.ip_address(addr)) is False


def test_is_blocked_ip_catches_ipv4_mapped_ipv6_bypass():
    """::ffff:10.0.0.1 is an IPv6 address whose own is_private does not
    automatically apply IPv4 rules to the embedded v4 address -- a known
    SSRF-filter bypass if the mapped form isn't unwrapped and re-checked."""
    mapped = ipaddress.ip_address("::ffff:10.0.0.1")
    assert web_fetch._is_blocked_ip(mapped) is True


# ---------------------------------------------------------------------------
# Scheme enforcement
# ---------------------------------------------------------------------------


def test_raw_get_refuses_http_scheme():
    with pytest.raises(web_fetch.WebFetchError, match="https"):
        web_fetch._raw_get("http://example.com/", timeout=5, max_bytes=1000)


def test_raw_get_refuses_file_scheme():
    with pytest.raises(web_fetch.WebFetchError, match="https"):
        web_fetch._raw_get("file:///etc/passwd", timeout=5, max_bytes=1000)


def test_raw_get_refuses_gopher_scheme():
    with pytest.raises(web_fetch.WebFetchError, match="https"):
        web_fetch._raw_get("gopher://example.com/", timeout=5, max_bytes=1000)


# ---------------------------------------------------------------------------
# Literal private/special-use addresses -- rejected before any connection
# attempt, since getaddrinfo on a literal IP is instant/local (no real
# network I/O happens in any of these).
# ---------------------------------------------------------------------------


def test_raw_get_refuses_loopback_literal():
    with pytest.raises(web_fetch.WebFetchError, match="private|special"):
        web_fetch._raw_get("https://127.0.0.1/", timeout=5, max_bytes=1000)


def test_raw_get_refuses_rfc1918_literal():
    with pytest.raises(web_fetch.WebFetchError, match="private|special"):
        web_fetch._raw_get("https://10.1.2.3/", timeout=5, max_bytes=1000)


def test_raw_get_refuses_cloud_metadata_address():
    with pytest.raises(web_fetch.WebFetchError, match="private|special"):
        web_fetch._raw_get("https://169.254.169.254/latest/meta-data/", timeout=5, max_bytes=1000)


def test_raw_get_refuses_ipv6_loopback_literal():
    with pytest.raises(web_fetch.WebFetchError, match="private|special"):
        web_fetch._raw_get("https://[::1]/", timeout=5, max_bytes=1000)


# ---------------------------------------------------------------------------
# Hostname resolution is checked, not just the string -- a hostname that
# LOOKS public but resolves to a private address must still be refused.
# ---------------------------------------------------------------------------


def test_resolve_and_pin_refuses_hostname_resolving_to_private_address(monkeypatch):
    def fake_getaddrinfo(host, port, **kwargs):
        assert host == "internal.example.test"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.5.5.5", port))]

    monkeypatch.setattr(web_fetch.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(web_fetch.WebFetchError, match="private|special"):
        web_fetch._resolve_and_pin("internal.example.test", 443)


def test_resolve_and_pin_refuses_if_any_resolved_address_is_private(monkeypatch):
    """A hostname resolving to BOTH a public and a private address (e.g.
    round-robin, or a poisoned/rebinding DNS response) must fail closed --
    the whole hostname is refused, not just the private one skipped."""

    def fake_getaddrinfo(host, port, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", port)),
        ]

    monkeypatch.setattr(web_fetch.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(web_fetch.WebFetchError, match="private|special"):
        web_fetch._resolve_and_pin("mixed.example.test", 443)


def test_resolve_and_pin_reports_dns_failure_clearly(monkeypatch):
    def fake_getaddrinfo(host, port, **kwargs):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(web_fetch.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(web_fetch.WebFetchError, match="resolve"):
        web_fetch._resolve_and_pin("nonexistent.example.test", 443)


# ---------------------------------------------------------------------------
# Redirects: every hop is re-validated from scratch, including one that
# starts public and redirects to a private address.
# ---------------------------------------------------------------------------


def test_fetch_url_safely_refuses_redirect_to_private_address(monkeypatch):
    monkeypatch.setattr(web_fetch, "_robots_allows", lambda url: True)
    real_raw_get = web_fetch._raw_get

    def fake_raw_get(url, *, timeout, max_bytes):
        if url == "https://public.example.com/page":
            return 302, {"location": "https://169.254.169.254/secret"}, b""
        # The second hop is NOT faked -- this exercises the real
        # resolve+block check against the redirect target.
        return real_raw_get(url, timeout=timeout, max_bytes=max_bytes)

    monkeypatch.setattr(web_fetch, "_raw_get", fake_raw_get)
    result = web_fetch.fetch_url_safely("https://public.example.com/page")
    assert result.ok is False
    assert result.error is not None
    assert "169.254.169.254" in result.error or "private" in result.error.lower() or "special" in result.error.lower()


def test_fetch_url_safely_follows_redirect_to_a_public_address(monkeypatch):
    monkeypatch.setattr(web_fetch, "_robots_allows", lambda url: True)
    calls = []

    def fake_raw_get(url, *, timeout, max_bytes):
        calls.append(url)
        if url == "https://public.example.com/old":
            return 301, {"location": "https://public.example.com/new"}, b""
        return 200, {"content-type": "text/html"}, b"<html><body>hi</body></html>"

    monkeypatch.setattr(web_fetch, "_raw_get", fake_raw_get)
    result = web_fetch.fetch_url_safely("https://public.example.com/old")
    assert result.ok is True
    assert result.final_url == "https://public.example.com/new"
    assert calls == ["https://public.example.com/old", "https://public.example.com/new"]


def test_fetch_url_safely_caps_redirect_chain_length(monkeypatch):
    monkeypatch.setattr(web_fetch, "_robots_allows", lambda url: True)

    def fake_raw_get(url, *, timeout, max_bytes):
        # Always redirects to a new URL -- an infinite chain if not capped.
        n = int(url.rsplit("/", 1)[-1])
        return 302, {"location": f"https://public.example.com/{n + 1}"}, b""

    monkeypatch.setattr(web_fetch, "_raw_get", fake_raw_get)
    result = web_fetch.fetch_url_safely("https://public.example.com/0")
    assert result.ok is False
    assert "redirect" in (result.error or "").lower()


def test_fetch_url_safely_refuses_redirect_with_no_location_header(monkeypatch):
    monkeypatch.setattr(web_fetch, "_robots_allows", lambda url: True)
    monkeypatch.setattr(
        web_fetch, "_raw_get", lambda url, *, timeout, max_bytes: (302, {}, b"")
    )
    result = web_fetch.fetch_url_safely("https://public.example.com/page")
    assert result.ok is False
    assert "location" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# robots.txt
# ---------------------------------------------------------------------------


def test_robots_allows_disallowed_path(monkeypatch):
    def fake_raw_get(url, *, timeout, max_bytes):
        assert url.endswith("/robots.txt")
        return 200, {}, b"User-agent: *\nDisallow: /admin/\n"

    monkeypatch.setattr(web_fetch, "_raw_get", fake_raw_get)
    assert web_fetch._robots_allows("https://vendor.example.com/admin/secret") is False
    assert web_fetch._robots_allows("https://vendor.example.com/public/page") is True


def test_robots_permissive_when_unreachable(monkeypatch):
    def fake_raw_get(url, *, timeout, max_bytes):
        raise web_fetch.WebFetchError("connection refused")

    monkeypatch.setattr(web_fetch, "_raw_get", fake_raw_get)
    assert web_fetch._robots_allows("https://vendor.example.com/anything") is True


def test_robots_permissive_when_404(monkeypatch):
    monkeypatch.setattr(web_fetch, "_raw_get", lambda url, *, timeout, max_bytes: (404, {}, b""))
    assert web_fetch._robots_allows("https://vendor.example.com/anything") is True


def test_fetch_url_safely_honors_robots_disallow(monkeypatch):
    monkeypatch.setattr(web_fetch, "_robots_allows", lambda url: False)
    result = web_fetch.fetch_url_safely("https://vendor.example.com/admin/secret")
    assert result.ok is False
    assert "robots" in (result.error or "").lower()


# ---------------------------------------------------------------------------
# Response size cap
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    def read(self, n: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


def test_read_capped_refuses_oversized_response():
    resp = _FakeResponse([b"a" * 100, b"b" * 100])
    with pytest.raises(web_fetch.WebFetchError, match="cap"):
        web_fetch._read_capped(resp, max_bytes=150)


def test_read_capped_allows_response_under_cap():
    resp = _FakeResponse([b"a" * 100])
    assert web_fetch._read_capped(resp, max_bytes=150) == b"a" * 100


# ---------------------------------------------------------------------------
# No credentials ever attached -- the request is built from nothing but
# the URL and a fixed User-Agent; there is no code path that reads a
# WinGRC session/cookie/Authorization header into an outbound request.
# ---------------------------------------------------------------------------


def test_user_agent_identifies_wingrc_and_no_auth_header_is_ever_built():
    assert "WinGRC" in web_fetch.USER_AGENT
    source = inspect.getsource(web_fetch)
    assert "Authorization" not in source
    assert "Cookie" not in source


# ---------------------------------------------------------------------------
# HTML -> text extraction
# ---------------------------------------------------------------------------


def test_extract_web_text_strips_script_and_style_and_gets_title():
    html = b"""
    <html><head><title>Admin Security Guide</title>
    <style>body { color: red; }</style>
    </head><body>
    <script>alert('hi')</script>
    <h1>Role-Based Access Control</h1>
    <p>Configure RBAC in the admin console.</p>
    </body></html>
    """
    text, title = web_fetch.extract_web_text(html, "text/html; charset=utf-8")
    assert title == "Admin Security Guide"
    assert "Role-Based Access Control" in text
    assert "Configure RBAC" in text
    assert "alert" not in text
    assert "color: red" not in text


def test_extract_web_text_handles_bad_charset_gracefully():
    html = "café".encode("latin-1")
    text, _title = web_fetch.extract_web_text(html, "text/html; charset=totally-bogus")
    assert isinstance(text, str)  # never raises
