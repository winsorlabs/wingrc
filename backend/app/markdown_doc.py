"""Canonical document-body format for the document library (roadmap N.2).

**The format is GFM-subset Markdown.** `DocumentVersion.body` holds
Markdown text; this module is the only thing that turns it into HTML.

Why Markdown rather than HTML or editor JSON -- the decision N.2 exists to
make, recorded here because this is the module it constrains:

A document body has to render in *two* places: the React app, and the
backend's HTML -> WeasyPrint PDF/bundle path. Whatever the format, both
renderers see operator-supplied content, so both are injection surfaces.

  - **HTML** (what ProseMirror/TipTap produce natively) round-trips
    perfectly and diffs terribly -- "what changed between March and
    September" becomes a structural differ rather than a `difflib` call --
    and forces a correct-by-audit HTML sanitizer onto both render paths.
  - **Editor JSON** round-trips perfectly and does not diff at all.
  - **Markdown** diffs line-by-line with stdlib tooling, and lets both
    renderers *construct* their own output rather than forwarding the
    operator's markup. Raw HTML in a body is escaped to text, never
    passed through, so injection is structurally impossible instead of
    filtered. That is exactly `svg_sanitize.py`'s allowlist-rebuild
    philosophy, applied to a second untrusted-content path.

The cost, stated rather than discovered later: no merged table cells
(GFM cannot express them, and this module deliberately does not add an
HTML escape hatch that would hand the sanitizer problem straight back),
and no inline images until N.4 adds an upload path for
`DocumentVersion.storage_key` -- the `image` rule is disabled below, so
`![x](y)` renders as literal text rather than silently producing a broken
or externally-fetched `<img>`. Jarrod confirmed both costs before this
slice started; the image one is reversible (an N.4 `wingrc-asset:<uuid>`
scheme plus an allowlisted `img`), the merged-cell one is not.

N.4's `.docx` conversion can target this: pandoc emits GFM directly, and
mammoth's `.docx` -> HTML output converts onward to Markdown. The plan
doc's already-accepted cost ("complex Word formatting is lost and his
existing set gets re-styled once inside WinGRC") is what that amounts to.

**Parser parity with the frontend.** The browser renders the same bodies
for editing and preview using `markdown-it` (JS); this module uses
`markdown-it-py`, a direct port of it with matching rules and presets.
Two renderers is a divergence risk whatever the format, and picking a
matched pair is what bounds it -- `tests/test_markdown_doc.py` and the
frontend's `markdown.test.ts` run the same shared corpus
(`backend/tests/fixtures/markdown_corpus.json`) through both, so drift
fails a test instead of shipping.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlparse

from markdown_it import MarkdownIt
from markdown_it.common.utils import escapeHtml

# Every tag this module is allowed to emit. Enforced after rendering by
# `_AllowlistCheck` -- see its docstring for why a construction-time
# guarantee still gets a verification pass behind it.
_ALLOWED_TAGS = frozenset({
    "p", "br", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li",
    "blockquote", "pre", "code",
    "em", "strong", "s",
    "a",
    "table", "thead", "tbody", "tr", "th", "td",
})

# Per-tag attribute allowlist. Notably absent everywhere: `style`. GFM
# column alignment natively renders as `style="text-align:right"`, which
# would put a CSS-bearing attribute on operator-influenced output -- the
# same `url(...)`-smuggling loophole `svg_sanitize.py` refuses `style`
# for. `_align_class` below rewrites alignment to a class instead.
_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "rel", "target"}),
    "th": frozenset({"class"}),
    "td": frozenset({"class"}),
}

# Link schemes. A link with no scheme at all (relative) is allowed; a
# scheme that IS present must be one of these. Deliberately excludes
# `data:` (HTML smuggling) and `javascript:`/`vbscript:` (obviously).
_ALLOWED_SCHEMES = frozenset({"http", "https", "mailto"})

_ALIGN_CLASS = {
    "text-align:left": "ta-left",
    "text-align:center": "ta-center",
    "text-align:right": "ta-right",
}


def _validate_link(url: str) -> bool:
    """markdown-it's per-link hook. Returning False makes the renderer
    emit the link as plain text instead of an `<a href=...>`, which is
    the behaviour we want for anything off `_ALLOWED_SCHEMES`.

    markdown-it-py ships its own `validateLink` that blocks a similar set
    by pattern; this replaces it with an explicit allowlist rather than
    inheriting a blocklist, matching how every other untrusted-input path
    in this codebase is written.
    """
    try:
        scheme = urlparse(url.strip()).scheme.lower()
    except ValueError:
        return False
    return scheme == "" or scheme in _ALLOWED_SCHEMES


def _strip_align_style(token) -> None:
    """Rewrite GFM's `style="text-align:..."` into a class, in place.

    `Token.attrs` is a dict in markdown-it-py 4.x (it was a list of pairs
    in 3.x). If that shape changes again under us, the `style` attribute
    survives into the output and `_AllowlistCheck` rejects it -- a loud
    failure rather than a silently reintroduced CSS-bearing attribute.
    """
    style = token.attrGet("style")
    if style is None:
        return
    token.attrs = {k: v for k, v in token.attrs.items() if k != "style"}
    cls = _ALIGN_CLASS.get(str(style).replace(" ", "").lower())
    if cls:
        token.attrJoin("class", cls)


def _build_parser() -> MarkdownIt:
    md = MarkdownIt(
        "commonmark",
        {
            # The whole security argument in one option: raw HTML in the
            # source is escaped to text, never forwarded. Every tag in
            # the output is one markdown-it's own renderer constructed.
            "html": False,
            # Explicit links only -- bare text that merely looks like a
            # URL must not silently become a clickable external reference
            # in a document an assessor reads.
            "linkify": False,
            "typographer": False,
        },
    )
    md.enable("table")
    md.enable("strikethrough")
    # No upload path for document images until N.4 (see module
    # docstring). With the rule off, `![alt](url)` degrades to a literal
    # `!` followed by an ordinary link rather than vanishing: the
    # reference stays visible and clickable for whoever is reading the
    # policy, and no `<img>` is ever emitted. `_ALLOWED_TAGS` is the
    # backstop if that ever changes.
    md.disable("image")
    md.validateLink = _validate_link
    return md


_MD = _build_parser()


def _link_open(self, tokens, idx, options, env):
    token = tokens[idx]
    href = token.attrGet("href") or ""
    if urlparse(href).scheme.lower() in {"http", "https"}:
        token.attrSet("rel", "noopener noreferrer nofollow")
        token.attrSet("target", "_blank")
    return self.renderToken(tokens, idx, options, env)


def _table_cell_open(self, tokens, idx, options, env):
    _strip_align_style(tokens[idx])
    return self.renderToken(tokens, idx, options, env)


def _fence(self, tokens, idx, options, env):
    """Fenced code blocks without markdown-it's `class="language-..."`.

    The info string is operator-supplied and nothing here highlights
    syntax, so carrying it would put an attacker-influenced attribute
    value on the output for no rendering benefit.
    """
    return "<pre><code>" + escapeHtml(tokens[idx].content) + "</code></pre>\n"


# add_render_rule rather than assigning into `renderer.rules` directly:
# markdown-it-py invokes entries in that dict unbound, so a plain
# assignment loses `self` and the rule cannot call back into
# `renderToken`. This binds it as a method on the renderer instance.
_MD.add_render_rule("link_open", _link_open)
_MD.add_render_rule("th_open", _table_cell_open)
_MD.add_render_rule("td_open", _table_cell_open)
_MD.add_render_rule("fence", _fence)


class UnsafeRenderError(RuntimeError):
    """Rendered output contained something outside the allowlist."""


class _AllowlistCheck(HTMLParser):
    """Verification pass over rendered output.

    The renderer above cannot emit an unexpected tag by construction --
    `html=False` escapes source markup, and every tag comes from one of
    markdown-it's own rules. This runs anyway, and fails closed, so a
    future change (a newly enabled rule, a dependency upgrade that starts
    emitting a new tag or attribute) surfaces as a loud error here rather
    than silently widening what reaches a browser and WeasyPrint. Same
    reasoning as `svg_sanitize.py` rebuilding rather than filtering: on
    this path, being wrong is not recoverable after the fact.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.violations: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag not in _ALLOWED_TAGS:
            self.violations.append(f"tag <{tag}>")
            return
        allowed = _ALLOWED_ATTRS.get(tag, frozenset())
        for name, value in attrs:
            if name not in allowed:
                self.violations.append(f"attribute {name!r} on <{tag}>")
            elif name == "href" and not _validate_link(value or ""):
                self.violations.append(f"href scheme in {value!r}")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)


def render_html(body: str | None) -> str:
    """Render a document body to HTML safe to embed in the app, the ZIP
    bundle, and WeasyPrint's PDF input.

    Pure -- no DB, no storage, no network -- so `bundle_service`'s
    `render_bundle` can call it and stay the pure function over frozen
    dataclasses it already is.
    """
    if not body or not body.strip():
        return ""
    rendered = _MD.render(body)
    checker = _AllowlistCheck()
    checker.feed(rendered)
    checker.close()
    if checker.violations:
        raise UnsafeRenderError(
            "Rendered document body contained disallowed markup: "
            + "; ".join(sorted(set(checker.violations)))
        )
    return rendered
