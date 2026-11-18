"""The storage host passes reads only (docs/roadmap.md, the compose entry's
"The storage host carries reads only").

Every write to MinIO goes over the compose network; the public endpoint
only signs get_object URLs. So every location on the storage host must
refuse anything but GET (and HEAD, which GET admits), or a leaked
credential can overwrite or delete evidence through nginx. Seen working on
a bench nginx in front of MinIO (the slice's record).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[2] / "deploy" / "nginx" / "nginx.conf"


def _blocks(text: str, head: str) -> list[str]:
    """Each brace-balanced block whose opening matches `head`."""
    out = []
    for m in re.finditer(head + r"[^{;]*\{", text):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        out.append(text[m.start():i])
    return out


@pytest.fixture
def storage_block() -> str:
    if not CONF.exists():
        pytest.skip("deploy/ is outside this tree (the backend image); CI runs this")
    text = "\n".join(
        line.split("#", 1)[0] for line in CONF.read_text(encoding="utf-8").splitlines()
    )
    found = [
        b for b in _blocks(text, r"\bserver\s*")
        if re.search(r"server_name\s+storage\.[^;]*;", b) and re.search(r"listen\s+443", b)
    ]
    assert len(found) == 1, f"fixture must find one TLS storage server block, got {len(found)}"
    return found[0]


def test_every_location_allows_reads_only(storage_block):
    locations = _blocks(storage_block, r"\blocation\s")
    assert locations, "fixture must find the storage host's locations or this test proves nothing"
    for loc in locations:
        if "proxy_pass" not in loc:
            continue
        limits = _blocks(loc, r"\blimit_except\s")
        assert len(limits) == 1, f"a proxied location without limit_except: {loc[:60]}"
        assert re.match(r"limit_except\s+GET\s*\{", limits[0]), limits[0]
        assert re.search(r"\{\s*deny\s+all;\s*\}$", limits[0]), limits[0]


def test_nothing_proxies_outside_a_location(storage_block):
    outside = storage_block
    for loc in _blocks(storage_block, r"\blocation\s"):
        outside = outside.replace(loc, "")
    assert "proxy_pass" not in outside
