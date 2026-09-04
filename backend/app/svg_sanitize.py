"""SVG sanitization for untrusted diagram uploads (docs/pdf_ssp_template_spec.md's
Network Diagram / Data Flow Diagram addendum).

Security model: allowlist, not blocklist. SVG is XML, and XML blocklists have
a long history of bypasses (event-handler attributes, `<foreignObject>`
smuggling arbitrary HTML, CSS `url()` inside `<style>`, XXE via DOCTYPE/entity
expansion). Rather than trying to strip every known-dangerous construct, this
rebuilds a brand-new tree containing *only* elements and attributes on the
allowlist below — anything not explicitly allowed is dropped, full stop.

Parsing goes through `defusedxml`, never the stdlib `xml.etree` directly on
untrusted bytes — `forbid_dtd=True` rejects any `<!DOCTYPE>` outright (the
spec's own instruction: reject, don't attempt to sanitize), and
`forbid_entities`/`forbid_external` (defusedxml's defaults, passed explicitly
here for clarity) block entity-expansion and external-entity attacks.

No `style` attribute or `<style>` element is ever allowed through, on
purpose: CSS accepts `url(...)` as a value for `fill`, `background`, etc.,
which is exactly the same external-reference smuggling risk as an `<image
xlink:href>` — allowing arbitrary presentation attributes (fill, stroke, ...)
directly as XML attributes gets equivalent styling power without that
loophole, since none of them can carry a CSS function call.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import defusedxml.ElementTree as DET
from defusedxml.common import DefusedXmlException

SVG_NS = "http://www.w3.org/2000/svg"

# Exact element list from the spec's own Addendum wording — deliberately
# conservative. Notably absent: <script> (obviously), <image>/<a> (the only
# ways to reference an external resource by URL), <foreignObject> (lets
# arbitrary embedded HTML/JS ride along), <style> (see module docstring).
# Also absent: <marker>/<use> — real diagrams may want arrowheads via
# <marker>, but that's outside what was asked here; if the survey-tool
# integration (the spec's own "Open risk" section) needs it, add it as its
# own deliberate decision, not a silent expansion of this allowlist.
_ALLOWED_ELEMENTS = frozenset({
    "svg", "path", "g", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "defs", "title", "desc",
})

# Geometry, structure, and presentation attributes only — no `style`, no
# `href`/`xlink:href` (would let <use>/<a>-style references smuggle a URL
# back in even without <image>), no event handlers (allowlist means they're
# simply never considered, not filtered by name).
_ALLOWED_ATTRS = frozenset({
    "id", "class",
    "width", "height", "viewBox", "preserveAspectRatio", "version",
    "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "points", "d", "transform",
    "fill", "stroke", "stroke-width", "stroke-dasharray",
    "stroke-linecap", "stroke-linejoin", "stroke-opacity",
    "opacity", "fill-opacity",
    "font-size", "font-family", "font-weight", "font-style",
    "text-anchor", "dominant-baseline",
})

_MAX_INPUT_BYTES = 5 * 1024 * 1024  # 5 MB — diagrams are small vector files


class SvgSanitizeError(ValueError):
    """Raised when an SVG upload is rejected outright (not sanitized)."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _clean(elem: ET.Element) -> ET.Element | None:
    """Rebuild one element from the allowlist, recursing into children.

    Returns None if the element itself isn't allowed — the caller drops it
    (and everything under it) rather than hoisting its children up, since an
    unknown element's children were only ever meaningful in that context.
    """
    name = _local_name(elem.tag)
    if name not in _ALLOWED_ELEMENTS:
        return None

    new = ET.Element(f"{{{SVG_NS}}}{name}")
    for key, value in elem.attrib.items():
        if _local_name(key) in _ALLOWED_ATTRS:
            new.set(_local_name(key), value)

    if elem.text:
        new.text = elem.text

    for child in elem:
        cleaned_child = _clean(child)
        if cleaned_child is not None:
            new.append(cleaned_child)

    return new


def sanitize_svg(data: bytes) -> bytes:
    """Parse, allowlist-sanitize, and re-serialize an untrusted SVG upload.

    Raises SvgSanitizeError (never lets a parser exception escape raw) if the
    input isn't well-formed XML, carries a DOCTYPE/entity declaration, or its
    root element isn't <svg> — these are rejected outright, not sanitized
    down to something safe, per the spec's explicit instruction.

    The returned bytes are what must actually be hashed and stored — never
    hash or store the raw upload for SVG, or the "sanitized" claim would be
    fiction (the stored/served artifact must be what was actually cleaned).
    """
    if len(data) > _MAX_INPUT_BYTES:
        raise SvgSanitizeError(f"SVG exceeds {_MAX_INPUT_BYTES // (1024 * 1024)} MB limit")

    try:
        root = DET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise SvgSanitizeError(f"SVG rejected: {exc}") from exc
    except ET.ParseError as exc:
        raise SvgSanitizeError(f"SVG is not well-formed XML: {exc}") from exc

    if _local_name(root.tag) != "svg":
        raise SvgSanitizeError("Root element is not <svg>")

    sanitized_root = _clean(root)
    if sanitized_root is None:
        # Unreachable given the check above (root's own tag already verified
        # to be "svg", which is always in _ALLOWED_ELEMENTS) — guarded
        # anyway since _clean's contract is Optional.
        raise SvgSanitizeError("Root <svg> element was rejected during sanitization")

    ET.register_namespace("", SVG_NS)
    return ET.tostring(sanitized_root, encoding="utf-8")
