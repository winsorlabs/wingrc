"""SSRF-safe HTTPS fetcher for admin-approved documentation URLs
(importers/research.py). Treat this module as a security control, not a
convenience wrapper — the backend sits inside Jarrod's management network
with reach to Liongard, Postgres, and MinIO, so giving it the ability to
fetch operator-supplied URLs is a real SSRF surface.

Defenses, all enforced here (never left to a caller to remember):
  1. HTTPS only. No http://, file://, gopher://, or any other scheme.
  2. Hostname is resolved and EVERY resolved address is checked against
     private/special-use ranges (RFC 1918, loopback, link-local including
     169.254.169.254, multicast, reserved, unspecified) before connecting
     -- and the connection is then PINNED to the specific address that was
     checked (see _PinnedHTTPSConnection). Resolving once to validate and
     then letting a normal HTTPS client re-resolve at connect time is a
     textbook DNS-rebinding TOCTOU: the checked address and the connected
     address could otherwise be two different lookups.
  3. Every redirect hop is treated as a brand new URL and re-validated from
     scratch (scheme, resolution, address check) before being followed, up
     to a hop cap.
  4. Connect/read timeouts and a hard response-size cap, enforced while
     streaming (not just checked against a Content-Length header a hostile
     server can lie about or omit).
  5. No credentials of any kind are ever attached to an outbound request --
     there is nothing in this module that reads a WinGRC session, cookie,
     or Authorization header to forward in the first place; the request is
     built from nothing but the URL and a fixed identifying User-Agent.
  6. robots.txt is checked before every fetch (not just the first one in a
     run) and a disallow is honored -- this fetches a vendor's own
     documentation site under WinGRC's name, and scraping rudely reflects
     on the MSP running it.

Never follows a link found IN fetched page content -- only HTTP redirects
(a server directing navigation of the SAME requested URL), which is a
different thing from crawling. The set of URLs this module will ever touch
in one run is exactly the admin-approved list plus each one's own
robots.txt, nothing discovered from a page body.
"""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
import urllib.robotparser
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

USER_AGENT = "WinGRC-ResearchFetcher/1.0 (+compliance documentation research; see robots.txt)"

_MAX_REDIRECTS = 5
_CONNECT_TIMEOUT_SECONDS = 8
_SOCKET_TIMEOUT_SECONDS = 20
_MAX_TOTAL_WALL_SECONDS = 30
_MAX_RESPONSE_BYTES = 5_000_000
_READ_CHUNK_BYTES = 65_536
_ROBOTS_TIMEOUT_SECONDS = 8
_ROBOTS_MAX_BYTES = 200_000


class WebFetchError(Exception):
    """Any expected fetch failure -- refused scheme, blocked address,
    robots.txt disallow, timeout, oversized response, HTTP error, too many
    redirects. Callers report `str(exc)` directly; every message here is
    already written for a reviewer, not a developer."""


@dataclass
class FetchResult:
    url: str
    final_url: str
    ok: bool
    status_code: int | None
    content: bytes | None
    content_type: str | None
    error: str | None


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if `ip` is private, loopback, link-local (covers the
    169.254.169.254 cloud-metadata address), multicast, reserved, or
    unspecified. Checked both directly and, for an IPv6 address, on its
    unwrapped IPv4-mapped form (::ffff:10.0.0.1) -- ipaddress.IPv6Address's
    own is_private does not automatically apply IPv4 rules to an embedded
    v4 address, which is a known SSRF-filter bypass if left unhandled.
    """

    def _blocked(a: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return (
            a.is_private
            or a.is_loopback
            or a.is_link_local
            or a.is_multicast
            or a.is_reserved
            or a.is_unspecified
        )

    if _blocked(ip):
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and _blocked(ipaddress.IPv4Address(mapped)):
        return True
    return False


def _resolve_and_pin(hostname: str, port: int) -> str:
    """Resolve `hostname`, refuse if ANY resolved address is blocked (fail
    closed rather than picking only the first one someone might expect),
    and return one validated address to connect to."""
    try:
        infos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise WebFetchError(f"Could not resolve {hostname!r}: {exc}") from exc
    if not infos:
        raise WebFetchError(f"{hostname!r} resolved to no addresses.")

    addresses = {info[4][0] for info in infos}
    for addr_str in addresses:
        raw = addr_str.split("%", 1)[0]  # strip an IPv6 zone id (fe80::1%eth0)
        ip = ipaddress.ip_address(raw)
        if _is_blocked_ip(ip):
            raise WebFetchError(
                f"{hostname!r} resolves to {addr_str}, a private or special-use "
                "address -- refusing to fetch it."
            )
    return next(iter(addresses))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """An HTTPSConnection that connects to a pre-validated IP address
    instead of re-resolving `host` itself, while still sending the correct
    SNI/Host for `host` so certificate validation is unaffected. This is
    what makes the resolve-then-check in _resolve_and_pin() actually
    binding rather than advisory -- without pinning, the OS could resolve
    `host` again at connect time and land on a different (attacker-
    controlled, freshly-published) address than the one just checked."""

    def __init__(self, host: str, pinned_ip: str, port: int, timeout: float):
        super().__init__(host, port=port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        context = self._context or ssl.create_default_context()
        self.sock = context.wrap_socket(sock, server_hostname=self.host)


def _read_capped(resp: http.client.HTTPResponse, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = resp.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise WebFetchError(
                f"Response exceeded the {max_bytes:,}-byte size cap -- refusing to "
                "read further."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _raw_get(url: str, *, timeout: float, max_bytes: int) -> tuple[int, dict[str, str], bytes]:
    """One HTTP request, no redirect-following, no robots check -- the
    single building block both the robots.txt lookup and the real
    validated-and-pinned fetch below are built from. Scheme/address
    validation happens here so nothing can call this without it."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise WebFetchError(
            f"Only https:// URLs are allowed (got {parsed.scheme!r} for {url})."
        )
    if not parsed.hostname:
        raise WebFetchError(f"URL has no hostname: {url}")

    port = parsed.port or 443
    pinned_ip = _resolve_and_pin(parsed.hostname, port)

    conn = _PinnedHTTPSConnection(parsed.hostname, pinned_ip, port=port, timeout=timeout)
    try:
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        conn.request(
            "GET",
            path,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Encoding": "identity",
            },
        )
        resp = conn.getresponse()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        body = _read_capped(resp, max_bytes)
        return resp.status, headers, body
    except (TimeoutError, OSError) as exc:
        raise WebFetchError(f"Connection to {parsed.hostname} failed: {exc}") from exc
    finally:
        conn.close()


def _robots_allows(url: str) -> bool:
    """Permissive on any failure to fetch/parse robots.txt (absent,
    unreachable, malformed) -- that is standard robots.txt handling, not a
    security relaxation: robots.txt only ever narrows what a well-behaved
    crawler does, it isn't an access control this module depends on for
    safety (the SSRF checks above are what's load-bearing)."""
    parsed = urlparse(url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    try:
        status, _headers, body = _raw_get(
            robots_url, timeout=_ROBOTS_TIMEOUT_SECONDS, max_bytes=_ROBOTS_MAX_BYTES
        )
    except WebFetchError:
        return True
    if status != 200:
        return True
    rp = urllib.robotparser.RobotFileParser()
    try:
        rp.parse(body.decode("utf-8", errors="replace").splitlines())
    except Exception:
        return True
    return rp.can_fetch(USER_AGENT, url)


def fetch_url_safely(url: str) -> FetchResult:
    """Fetch one admin-approved URL under every defense this module's own
    docstring lists. Never raises -- every failure mode is reported back
    as FetchResult(ok=False, error=...) so a batch of several approved
    URLs can report per-URL results instead of aborting the whole run on
    the first bad one."""
    current_url = url
    deadline = time.monotonic() + _MAX_TOTAL_WALL_SECONDS

    for _hop in range(_MAX_REDIRECTS + 1):
        if time.monotonic() > deadline:
            return FetchResult(
                url=url, final_url=current_url, ok=False, status_code=None,
                content=None, content_type=None,
                error=f"Exceeded the {_MAX_TOTAL_WALL_SECONDS}s total fetch budget.",
            )
        try:
            if not _robots_allows(current_url):
                return FetchResult(
                    url=url, final_url=current_url, ok=False, status_code=None,
                    content=None, content_type=None,
                    error=f"Blocked by {urlparse(current_url).netloc}'s robots.txt.",
                )
            status, headers, body = _raw_get(
                current_url, timeout=_SOCKET_TIMEOUT_SECONDS, max_bytes=_MAX_RESPONSE_BYTES
            )
        except WebFetchError as exc:
            return FetchResult(
                url=url, final_url=current_url, ok=False, status_code=None,
                content=None, content_type=None, error=str(exc),
            )

        if status in (301, 302, 303, 307, 308):
            location = headers.get("location")
            if not location:
                return FetchResult(
                    url=url, final_url=current_url, ok=False, status_code=status,
                    content=None, content_type=None,
                    error=f"HTTP {status} redirect with no Location header.",
                )
            # Re-validated from scratch at the top of the next iteration --
            # this is what makes a public-URL-redirecting-to-a-private-one
            # attack refused rather than silently followed.
            current_url = urljoin(current_url, location)
            continue

        if status != 200:
            return FetchResult(
                url=url, final_url=current_url, ok=False, status_code=status,
                content=None, content_type=None, error=f"HTTP {status}.",
            )

        return FetchResult(
            url=url, final_url=current_url, ok=True, status_code=200,
            content=body, content_type=headers.get("content-type"), error=None,
        )

    return FetchResult(
        url=url, final_url=current_url, ok=False, status_code=None,
        content=None, content_type=None,
        error=f"Too many redirects (more than {_MAX_REDIRECTS}).",
    )


# ---------------------------------------------------------------------------
# HTML -> plain text (stdlib only -- html.parser.HTMLParser is a
# non-validating tag scanner, not an XML parser, so there is no XXE-style
# external-entity risk in feeding it untrusted markup)
# ---------------------------------------------------------------------------

_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._skip_tag: str | None = None
        self._in_title = False
        self.text_parts: list[str] = []
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS and self._skip_depth == 0:
            self._skip_tag = tag
            self._skip_depth = 1
        elif self._skip_tag == tag:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag == tag and self._skip_depth > 0:
            self._skip_depth -= 1
            if self._skip_depth == 0:
                self._skip_tag = None
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        stripped = data.strip()
        if stripped:
            self.text_parts.append(stripped)


def _charset_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return "utf-8"
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip().strip('"') or "utf-8"
    return "utf-8"


def extract_web_text(html_bytes: bytes, content_type: str | None) -> tuple[str, str | None]:
    """Returns (visible_text, page_title). Best-effort decoding -- a wrong
    or missing charset degrades to replacement characters, never raises."""
    charset = _charset_from_content_type(content_type)
    try:
        html_str = html_bytes.decode(charset, errors="replace")
    except LookupError:
        html_str = html_bytes.decode("utf-8", errors="replace")
    parser = _TextExtractor()
    parser.feed(html_str)
    text = "\n".join(parser.text_parts)
    title = " ".join(" ".join(parser.title_parts).split()).strip() or None
    return text, title
