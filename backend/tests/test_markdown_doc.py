"""Unit tests for app/markdown_doc.py (roadmap N.2).

No DB -- `render_html` is pure, so these run in the local
`-m "not integration"` subset.

The hostile-input cases are the point of this file. N.2's section 3 is
explicit that "it renders" and "it renders safely" are different claims,
and that the same rendering reaches the browser, the ZIP bundle and
WeasyPrint's PDF input. These assert the safe claim directly rather than
trusting the parser's configuration to mean what it says.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.markdown_doc import _ALLOWED_ATTRS, _ALLOWED_TAGS, UnsafeRenderError, render_html

CORPUS_PATH = pathlib.Path(__file__).parent / "fixtures" / "markdown_corpus.json"


# ---------------------------------------------------------------------------
# Hostile input
# ---------------------------------------------------------------------------

# Bodies an operator (or an attacker who got a body past one) could
# paste. The assertion below is structural rather than a substring scan:
# the dangerous text is *expected* to appear in the output, escaped --
# that is the correct rendering of someone writing about a script tag in a
# security policy. What must never happen is any of it becoming live
# markup.
HOSTILE = [
    "<script>alert(1)</script>",
    "<SCRIPT>alert(1)</SCRIPT>",
    "<img src=x onerror=alert(1)>",
    "<svg/onload=alert(1)>",
    "<iframe src=//evil></iframe>",
    "<style>body{background:url(//evil)}</style>",
    "<object data=//evil></object>",
    "<embed src=//evil>",
    "<form action=//evil><input name=p></form>",
    '<a href="javascript:alert(1)">x</a>',
    "<div onclick=alert(1)>x</div>",
    "<!-- <script>alert(1)</script> -->",
    "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
    '<noscript><p title="</noscript><img src=x onerror=alert(1)>">',
    "<base href=//evil>",
    "<meta http-equiv=refresh content=0;url=//evil>",
    "Normal text\n\n<script>alert(1)</script>\n\nmore text",
    "> <script>alert(1)</script>",
    "- <img src=x onerror=alert(1)>",
    "| a |\n|---|\n| <script>alert(1)</script> |",
]


def _live_markup(rendered: str) -> list[tuple[str, list[tuple[str, str | None]]]]:
    """Every tag the browser/WeasyPrint would actually act on."""
    from html.parser import HTMLParser

    found: list[tuple[str, list[tuple[str, str | None]]]] = []

    class _Collect(HTMLParser):
        def handle_starttag(self, tag, attrs):
            found.append((tag, attrs))

        def handle_startendtag(self, tag, attrs):
            found.append((tag, attrs))

    parser = _Collect(convert_charrefs=True)
    parser.feed(rendered)
    parser.close()
    return found


@pytest.mark.parametrize("body", HOSTILE)
def test_hostile_markup_never_becomes_live_markup(body: str) -> None:
    out = render_html(body)

    for tag, attrs in _live_markup(out):
        assert tag in _ALLOWED_TAGS, f"live <{tag}> rendered from {body!r}"
        for name, value in attrs:
            assert name in _ALLOWED_ATTRS.get(tag, frozenset()), (
                f"live attribute {name!r} on <{tag}> rendered from {body!r}"
            )
            assert not name.startswith("on"), f"event handler {name!r} survived"
            if name == "href":
                assert not (value or "").lower().lstrip().startswith(
                    ("javascript:", "vbscript:", "data:")
                )


@pytest.mark.parametrize("body", HOSTILE)
def test_hostile_markup_is_escaped_rather_than_silently_dropped(body: str) -> None:
    """Neutralized, not deleted.

    A reader of the policy should still see what was written -- an author
    documenting `<script>` in an appendix must not have it vanish. This
    also proves the previous test is not passing merely because the
    renderer threw the content away.
    """
    out = render_html(body)
    assert "&lt;" in out, f"escaped markup missing entirely from {out!r}"


# Link schemes are the one attacker-influenced *attribute value* the
# renderer emits at all, so they get their own set.
HOSTILE_LINKS = [
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))",
    "[x](  javascript:alert(1))",
    "[x](vbscript:msgbox(1))",
    "[x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
    "[x](file:///etc/passwd)",
]


@pytest.mark.parametrize("body", HOSTILE_LINKS)
def test_disallowed_link_schemes_do_not_become_hrefs(body: str) -> None:
    out = render_html(body)
    assert "href" not in out.lower(), f"a disallowed scheme became an href: {out!r}"


def test_allowed_link_schemes_render_as_links() -> None:
    out = render_html("[policy](https://example.com/p) and [mail](mailto:a@b.test)")
    assert 'href="https://example.com/p"' in out
    assert 'href="mailto:a@b.test"' in out


def test_external_links_carry_noopener_and_target() -> None:
    out = render_html("[x](https://example.com)")
    assert 'rel="noopener noreferrer nofollow"' in out
    assert 'target="_blank"' in out


def test_relative_links_are_allowed_without_target() -> None:
    out = render_html("[x](./annex-a)")
    assert 'href="./annex-a"' in out
    assert "target=" not in out


# ---------------------------------------------------------------------------
# The format itself -- what a real policy needs
# ---------------------------------------------------------------------------


def test_policy_structure_renders() -> None:
    out = render_html(
        "# Access Control Policy\n\n"
        "## Purpose\n\nThis policy is **mandatory** for _all_ staff.\n\n"
        "- MFA on every account\n- Quarterly review\n\n"
        "1. First\n2. Second\n\n"
        "> Exceptions require written approval.\n\n"
        "See [NIST](https://csrc.nist.gov) and ~~the old rule~~.\n"
    )
    for tag in ("<h1>", "<h2>", "<strong>", "<em>", "<ul>", "<ol>", "<li>", "<blockquote>", "<s>"):
        assert tag in out, f"{tag} missing from rendered policy"


def test_tables_render_with_alignment_as_classes_not_style() -> None:
    out = render_html("| Role | Owner |\n|:-----|------:|\n| ISSO | MSP |")
    assert "<table>" in out and "<th" in out and "<td" in out
    assert "style=" not in out, "alignment must not render as a style attribute"
    assert 'class="ta-left"' in out
    assert 'class="ta-right"' in out


def test_fenced_code_drops_the_language_class() -> None:
    out = render_html("```python\nprint('x')\n```")
    assert "<pre><code>" in out
    assert "language-" not in out


def test_images_degrade_to_a_link_and_never_emit_img() -> None:
    """No upload path exists until N.4, so `image` is disabled. The
    reference stays visible rather than vanishing, and no <img> is ever
    produced.
    """
    out = render_html("![Network diagram](https://example.com/d.png)")
    assert "<img" not in out
    assert 'href="https://example.com/d.png"' in out


def test_empty_and_blank_bodies_render_empty() -> None:
    assert render_html(None) == ""
    assert render_html("") == ""
    assert render_html("   \n\n  ") == ""


def test_ampersands_and_angle_brackets_in_prose_are_escaped() -> None:
    out = render_html("Policy covers R&D and values < 5 or > 10.")
    assert "&amp;" in out
    assert "&lt;" in out


# ---------------------------------------------------------------------------
# The allowlist backstop
# ---------------------------------------------------------------------------


def test_render_of_every_supported_construct_stays_inside_the_allowlist() -> None:
    """Whatever the parser emits for a full-featured body must already be
    covered by `_ALLOWED_TAGS`/`_ALLOWED_ATTRS`. If a dependency upgrade
    starts emitting something new, `render_html` raises and this fails --
    which is the entire point of keeping a verification pass behind a
    construction-time guarantee.
    """
    body = (
        "# H1\n## H2\n### H3\n#### H4\n##### H5\n###### H6\n\n"
        "Para with **b**, _i_, ~~s~~, `c`, [l](https://e.test), and a break  \nsecond line.\n\n"
        "- a\n- b\n\n1. a\n2. b\n\n> q\n\n---\n\n"
        "```\ncode\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    )
    out = render_html(body)
    assert out
    # markdown-it emits void elements XHTML-style (`<hr />`, `<br />`);
    # _AllowlistCheck's handle_startendtag is what covers that shape.
    assert "<h6>" in out and "<hr />" in out and "<br />" in out


def test_render_fails_closed_if_the_parser_ever_emits_something_new(monkeypatch) -> None:
    """The verification pass is not decoration.

    Simulates the case it exists for -- a dependency upgrade or a newly
    enabled rule starting to emit a tag nobody vetted -- by making the
    parser return exactly that. `render_html` must raise rather than
    return it, because the alternative is silently widening what reaches
    a browser and WeasyPrint.
    """
    import app.markdown_doc as md

    monkeypatch.setattr(
        md._MD, "render", lambda _body: '<p>ok</p><iframe src="//evil.test"></iframe>'
    )
    with pytest.raises(UnsafeRenderError) as exc:
        render_html("anything")
    assert "iframe" in str(exc.value)


def test_render_fails_closed_on_a_disallowed_attribute(monkeypatch) -> None:
    import app.markdown_doc as md

    monkeypatch.setattr(md._MD, "render", lambda _body: '<p onclick="bad()">x</p>')
    with pytest.raises(UnsafeRenderError) as exc:
        render_html("anything")
    assert "onclick" in str(exc.value)


def test_render_fails_closed_on_a_smuggled_href_scheme(monkeypatch) -> None:
    import app.markdown_doc as md

    monkeypatch.setattr(md._MD, "render", lambda _body: '<p><a href="javascript:x">y</a></p>')
    with pytest.raises(UnsafeRenderError) as exc:
        render_html("anything")
    assert "javascript" in str(exc.value)


def test_allowlists_are_self_consistent() -> None:
    assert set(_ALLOWED_ATTRS) <= _ALLOWED_TAGS
    assert "img" not in _ALLOWED_TAGS
    assert "script" not in _ALLOWED_TAGS
    for attrs in _ALLOWED_ATTRS.values():
        assert "style" not in attrs
        assert not any(a.startswith("on") for a in attrs)


# ---------------------------------------------------------------------------
# Frontend parity
# ---------------------------------------------------------------------------


def test_shared_corpus_matches_this_renderer() -> None:
    """The same corpus is replayed by the frontend's markdown.test.ts.

    Two renderers for one untrusted format is a divergence risk whatever
    the format; pinning both to a shared corpus is what turns that drift
    into a failing test instead of a difference nobody notices until a
    body renders one way in the app and another way in the assessor's PDF.
    """
    cases = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))["cases"]
    assert cases, "corpus must not be empty"
    for case in cases:
        assert render_html(case["markdown"]) == case["html"], (
            f"backend render drifted from the shared corpus for case {case['name']!r}"
        )
