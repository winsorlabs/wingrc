"""HSTS is on, one day, app host only (docs/roadmap.md, "HSTS: one day first").

Lengthening it is a deliberate act with a condition; this test is where that
act shows up. nginx drops a server block's add_header in any location that
adds its own, so a location-level add_header would silently remove HSTS.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "nginx.conf"


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
def conf() -> str:
    if not CONF.exists():
        pytest.skip("deploy/ is outside this tree (the backend image); CI runs this")
    lines = CONF.read_text(encoding="utf-8").splitlines()
    return "\n".join(line.split("#", 1)[0] for line in lines)


def test_one_day_on_the_app_host_and_nowhere_else(conf):
    found = {}
    for block in _server_blocks(conf):
        names = re.search(r"server_name\s+([^;]+);", block).group(1).split()
        listens_tls = re.search(r"listen\s+443\s+ssl", block) is not None
        headers = re.findall(r'add_header\s+Strict-Transport-Security\s+"([^"]+)"\s+always;', block)
        found[(tuple(names), listens_tls)] = headers
    app = [k for k in found if k[1] and k[0] == ("dev.wingrc.us",)]
    assert app, f"fixture must find the app's TLS server block, got {list(found)}"
    assert found[app[0]] == ["max-age=86400"], found[app[0]]
    others = {k: v for k, v in found.items() if k != app[0] and v}
    assert not others, f"HSTS outside the app host: {others}"


def test_no_include_subdomains(conf):
    assert "includeSubDomains" not in conf


def test_no_location_drops_it(conf):
    for block in _server_blocks(conf):
        for loc in re.findall(r"location[^{]*\{[^}]*\}", block):
            assert "add_header" not in loc, f"a location's add_header drops HSTS: {loc[:80]}"
