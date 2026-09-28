/**
 * Document-body rendering for the browser (roadmap N.2).
 *
 * Counterpart to the backend's `app/markdown_doc.py`, which renders the
 * same GFM-subset Markdown for the ZIP bundle and the WeasyPrint PDF. Read
 * that module's docstring for why the format is Markdown at all; this file
 * is about why the browser half is safe.
 *
 * **No `dangerouslySetInnerHTML`, anywhere.** A document body is
 * operator-supplied content, and the obvious implementation -- run a
 * Markdown library, inject the HTML string -- makes correctness depend on
 * a sanitizer being right. This walks markdown-it's token stream and
 * constructs React elements instead: every element comes from the
 * allowlist below, every attribute is one this file chose, and all text
 * goes through React, which escapes it. There is no code path that turns
 * a string from the server into markup.
 *
 * **Matched to the backend on purpose.** `markdown-it` (here) and
 * `markdown-it-py` (there) are ports of each other, configured
 * identically: `html: false`, no linkify, no typographer, tables and
 * strikethrough on, images off, the same http/https/mailto link
 * allowlist, the same rewrite of GFM table alignment from a `style`
 * attribute to a class. `markdown.test.ts` replays
 * `backend/tests/fixtures/markdown_corpus.json` through this renderer and
 * asserts the result matches what the backend produced, so the two cannot
 * drift into rendering the same policy differently in the app and in the
 * assessor's PDF.
 *
 * Versions are pinned exactly in package.json rather than carets: this is
 * the rendering path for compliance documents, and an unattended minor
 * bump of the parser is not something to find out about from a customer.
 */

// Token comes from markdown-it itself: v15 ships its own types, and the
// separate @types/markdown-it package describes an older, incompatible
// Token shape (attrs as [string, string][] rather than allowing numeric
// values). Installing both is how you get two mutually unassignable
// Token types for one object.
import MarkdownIt, { type Token } from "markdown-it";
import type { ElementType, ReactNode } from "react";

/** Tags this renderer may construct. Anything else fails closed. */
const ALLOWED_TAGS = new Set([
  "p", "br", "hr",
  "h1", "h2", "h3", "h4", "h5", "h6",
  "ul", "ol", "li",
  "blockquote", "pre", "code",
  "em", "strong", "s",
  "a",
  "table", "thead", "tbody", "tr", "th", "td",
]);

const ALLOWED_SCHEMES = new Set(["http:", "https:", "mailto:"]);

const ALIGN_CLASS: Record<string, string> = {
  "text-align:left": "ta-left",
  "text-align:center": "ta-center",
  "text-align:right": "ta-right",
};

export class UnsafeMarkdownError extends Error {}

/**
 * Explicit scheme allowlist, replacing markdown-it's built-in blocklist --
 * same swap the backend makes, for the same reason: everything else in
 * this codebase that touches untrusted input allowlists.
 *
 * A link with no scheme (relative) is allowed. `new URL` needs a base to
 * parse one of those at all; the base is thrown away.
 */
function validateLink(url: string): boolean {
  const trimmed = url.trim();
  try {
    const parsed = new URL(trimmed, "https://wingrc.invalid/");
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed)) {
      return ALLOWED_SCHEMES.has(parsed.protocol);
    }
    return true;
  } catch {
    return false;
  }
}

const md = new MarkdownIt("commonmark", {
  html: false,
  linkify: false,
  typographer: false,
});
md.enable("table");
md.enable("strikethrough");
md.disable("image");
md.validateLink = validateLink;

/**
 * The token stream for a body, from the one configured parser instance.
 *
 * Exported so `tiptapMarkdown.ts` loads the editor from exactly the same
 * parse the renderer and the backend use. A second `MarkdownIt` with its
 * own options is how "the editor showed me something different from the
 * export" happens, so there is only ever one.
 */
export function parseMarkdown(source: string): Token[] {
  return md.parse(source, {});
}

/** Link-scheme policy, shared with the editor bridge. */
export function isAllowedLink(url: string): boolean {
  return validateLink(url);
}

interface Frame {
  tag: string;
  props: Record<string, unknown>;
  children: ReactNode[];
}

function attrsFor(token: Token): Record<string, unknown> {
  const props: Record<string, unknown> = {};

  if (token.tag === "a") {
    const href = token.attrGet("href") ?? "";
    // markdown-it has already dropped the href for a scheme validateLink
    // rejected, which renders the link as plain text. This is the second
    // gate, not the first: if an href is here at all it gets re-checked
    // before it can reach the DOM.
    if (!href || !validateLink(href)) {
      throw new UnsafeMarkdownError(`link href rejected: ${href}`);
    }
    props.href = href;
    if (/^https?:/i.test(href)) {
      props.rel = "noopener noreferrer nofollow";
      props.target = "_blank";
    }
    return props;
  }

  if (token.tag === "th" || token.tag === "td") {
    // GFM column alignment arrives as `style="text-align:..."`. It is not
    // forwarded: a CSS-bearing attribute on operator-influenced output is
    // the exact loophole the backend's svg_sanitize-derived reasoning
    // refuses. Mapped to a class the stylesheet knows instead.
    const style = token.attrGet("style");
    if (style) {
      const cls = ALIGN_CLASS[style.replace(/\s/g, "").toLowerCase()];
      if (cls) props.className = cls;
    }
    return props;
  }

  return props;
}

function leaf(token: Token, key: number): ReactNode {
  switch (token.type) {
    case "text":
      return token.content;
    case "softbreak":
      return "\n";
    case "hardbreak":
      return <br key={key} />;
    case "hr":
      return <hr key={key} />;
    case "code_inline":
      return <code key={key}>{token.content}</code>;
    case "fence":
    case "code_block":
      // The fence info string (```python) is operator-supplied and nothing
      // here highlights syntax, so it is dropped rather than becoming a
      // class -- same call the backend renderer makes.
      return (
        <pre key={key}>
          <code>{token.content}</code>
        </pre>
      );
    case "html_block":
    case "html_inline":
      // Unreachable with `html: false` (markdown-it treats raw markup as
      // text), but if that ever changes the content renders as *text*
      // through React rather than as markup. Defence in depth, not a
      // filter.
      return token.content;
    default:
      return token.content || null;
  }
}

/**
 * Render a Markdown document body to React elements.
 *
 * Throws `UnsafeMarkdownError` rather than degrading if the token stream
 * ever asks for something off the allowlist -- see `MarkdownView` for the
 * visible-failure wrapper. Failing closed matters more than rendering
 * something here: this content also goes to an assessor.
 */
export function renderMarkdown(source: string | null | undefined): ReactNode[] {
  if (!source || !source.trim()) return [];

  const tokens = md.parse(source, {});
  const root: Frame = { tag: "", props: {}, children: [] };
  const stack: Frame[] = [root];

  const walk = (list: Token[]): void => {
    for (const token of list) {
      const top = stack[stack.length - 1];

      if (token.type === "inline") {
        walk(token.children ?? []);
        continue;
      }

      // A "tight" list's item paragraphs are marked hidden by markdown-it:
      // its own HTML renderer emits nothing for them, so `- a` becomes
      // `<li>a</li>` rather than `<li><p>a</p></li>`. Honouring that is
      // what keeps this renderer byte-comparable with the backend -- the
      // shared-corpus parity test caught exactly this, rendering every
      // tight list one level deeper here than in the PDF.
      if (token.hidden) continue;

      if (token.nesting === 1) {
        if (!ALLOWED_TAGS.has(token.tag)) {
          throw new UnsafeMarkdownError(`disallowed tag <${token.tag}>`);
        }
        stack.push({ tag: token.tag, props: attrsFor(token), children: [] });
        continue;
      }

      if (token.nesting === -1) {
        const frame = stack.pop();
        if (!frame || stack.length === 0) {
          throw new UnsafeMarkdownError("unbalanced token stream");
        }
        const parent = stack[stack.length - 1];
        const Tag = frame.tag as ElementType;
        parent.children.push(
          <Tag key={parent.children.length} {...frame.props}>
            {frame.children.length ? frame.children : null}
          </Tag>,
        );
        continue;
      }

      const node = leaf(token, top.children.length);
      if (node !== null && node !== undefined) top.children.push(node);
    }
  };

  walk(tokens);

  if (stack.length !== 1) {
    throw new UnsafeMarkdownError("unbalanced token stream");
  }
  return root.children;
}

/**
 * The rendered body, with a visible failure state.
 *
 * `renderMarkdown` fails closed by throwing; a thrown error inside React
 * render would blank the surrounding screen, so this catches it and shows
 * the operator that the document did not render, rather than either
 * silently showing nothing or taking the page down.
 */
export function MarkdownView({
  source,
  className,
}: {
  source: string | null | undefined;
  className?: string;
}) {
  let content: ReactNode[];
  try {
    content = renderMarkdown(source);
  } catch (err) {
    return (
      <div className={className}>
        <p className="form-error">
          This document could not be rendered safely and has been withheld.{" "}
          {err instanceof Error ? err.message : "Unknown rendering error."}
        </p>
      </div>
    );
  }
  if (!content.length) {
    return (
      <div className={className}>
        <p className="field-hint">This version has no content.</p>
      </div>
    );
  }
  return <div className={className}>{content}</div>;
}
