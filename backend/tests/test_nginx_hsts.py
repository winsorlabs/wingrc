"""HSTS is one day on both TLS hosts (docs/roadmap.md, "HSTS: one day first").

Lengthening it is a deliberate act with a condition; this test is where that
act shows up. Two ways a header escapes this file, both held here:
- nginx drops a server block's add_header in any location that adds its own;
- an upstream sends its own. MinIO sends a year with includeSubDomains, and
  live served it on the storage host until 2026-11-17, because a test that
  read only this file could not see it. The storage block hides it.
The headers as served were checked on a bench nginx (the slice's record).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "nginx.conf"
HSTS = re.compile(r'add_header\s+Strict-Transport-Security\s+"([^"]+)"\s+always;')


def _server_blocks(text: str) -> list[str]:
    blocks, depth, start = [], 0, None
    for m in re.finditer(r"server\s*\{|\{|\}", text):
        tok = m.group(0)
        if tok.startswith("server") and depth == 0:
            start, depth = m.start(), 1
        elif tok == "{" and start is not None:
            depth += 1
        elif tok == "}" and start is not None:
            depth -= 1
            if depth == 0:
                blocks.append(text[start:m.end()])
                start = None
    return blocks


@pytest.fixture
def tls_blocks() -> dict[str, str]:
    if not CONF.exists():
        pytest.skip("deploy/ is outside this tree (the backend image); CI runs this")
    lines = CONF.read_text(encoding="utf-8").splitlines()
    text = "\n".join(line.split("#", 1)[0] for line in lines)
    out = {}
    for block in _server_blocks(text):
        if re.search(r"listen\s+443\s+ssl", block):
            out[re.search(r"server_name\s+([^;]+);", block).group(1).strip()] = block
    return out


def test_both_tls_hosts_and_only_those(tls_blocks):
    assert set(tls_blocks) == {"dev.wingrc.us", "storage.dev.wingrc.us"}, (
        f"fixture must find the two TLS server blocks, got {sorted(tls_blocks)}"
    )


@pytest.mark.parametrize("host", ["dev.wingrc.us", "storage.dev.wingrc.us"])
def test_one_day_no_subdomains(tls_blocks, host):
    assert HSTS.findall(tls_blocks[host]) == ["max-age=86400"]
    assert "includeSubDomains" not in tls_blocks[host]


def test_the_storage_host_hides_minios_own(tls_blocks):
    assert re.search(
        r"proxy_hide_header\s+Strict-Transport-Security;", tls_blocks["storage.dev.wingrc.us"]
    )


@pytest.mark.parametrize("host", ["dev.wingrc.us", "storage.dev.wingrc.us"])
def test_no_location_drops_it(tls_blocks, host):
    for loc in re.findall(r"location[^{]*\{[^}]*\}", tls_blocks[host]):
        assert "add_header" not in loc, f"a location's add_header drops HSTS: {loc[:80]}"
        assert "proxy_hide_header" not in loc, f"a location resets proxy_hide_header: {loc[:60]}"
