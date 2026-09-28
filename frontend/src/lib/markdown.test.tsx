// @vitest-environment jsdom
//
// Two things are under test here, and the second is the one that matters
// most over time.
//
// 1. The browser renderer never produces live markup from a hostile
//    document body (roadmap N.2 section 3). Bodies are operator-supplied
//    and go on to an assessor, so "it renders" and "it renders safely" are
//    separate claims and only the second one is asserted here.
//
// 2. It agrees with the backend renderer. The same corpus
//    (backend/tests/fixtures/markdown_corpus.json) is replayed by
//    backend/tests/test_markdown_doc.py. Two renderers for one untrusted
//    format is a divergence risk whatever the format; pinning both to a
//    shared corpus is what turns drift into a failing test rather than a
//    policy that reads one way in the app and another way in the PDF.
//
// jsdom is scoped to this file, and cleanup is registered explicitly --
// neither this file nor vite.config.ts's test block does it automatically
// (same isolation note the component tests carry).
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MarkdownView, UnsafeMarkdownError, renderMarkdown } from "./markdown";

afterEach(cleanup);

const CORPUS_PATH = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
  "..",
  "backend",
  "tests",
  "fixtures",
  "markdown_corpus.json",
);

interface CorpusCase {
  name: string;
  markdown: string;
  html: string;
}

const corpus: CorpusCase[] = JSON.parse(readFileSync(CORPUS_PATH, "utf8")).cases;

const ALLOWED_TAGS = new Set([
  "P", "BR", "HR", "H1", "H2", "H3", "H4", "H5", "H6",
  "UL", "OL", "LI", "BLOCKQUOTE", "PRE", "CODE",
  "EM", "STRONG", "S", "A",
  "TABLE", "THEAD", "TBODY", "TR", "TH", "TD",
]);

const ALLOWED_ATTRS: Record<string, Set<string>> = {
  A: new Set(["href", "rel", "target"]),
  TH: new Set(["class"]),
  TD: new Set(["class"]),
};

/**
 * Canonical structural form of a DOM subtree: tag names, sorted
 * attributes, and text.
 *
 * Whitespace between block elements is normalised away, because the
 * comparison is about structure/attributes/text rather than the newlines
 * markdown-it puts between blocks in a string that React never emits.
 * Text inside <pre>/<code> is preserved exactly -- that whitespace is
 * content. The same normalisation runs over both sides, so a real
 * difference still fails.
 */
function canon(node: Node, inPre = false): string {
  if (node.nodeType === Node.TEXT_NODE) {
    const text = node.textContent ?? "";
    if (inPre) return text;
    const collapsed = text.replace(/\s+/g, " ").trim();
    return collapsed;
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return "";

  const el = node as Element;
  const tag = el.tagName;
  const attrs = Array.from(el.attributes)
    .map((a) => `${a.name}="${a.value}"`)
    .sort()
    .join(" ");
  const nextInPre = inPre || tag === "PRE" || tag === "CODE";
  const children = Array.from(el.childNodes)
    .map((c) => canon(c, nextInPre))
    .filter((s) => s !== "")
    .join("");
  return `<${tag}${attrs ? " " + attrs : ""}>${children}</${tag}>`;
}

function canonFragment(nodes: Iterable<Node>, inPre = false): string {
  return Array.from(nodes)
    .map((n) => canon(n, inPre))
    .filter((s) => s !== "")
    .join("");
}

function canonHtmlString(html: string): string {
  // Test-only: turns the backend's recorded output into a DOM so the two
  // sides can be compared structurally. Never a code path the app takes --
  // the app has no innerHTML anywhere, which is the point of markdown.tsx.
  const host = document.createElement("div");
  host.innerHTML = html;
  return canonFragment(host.childNodes);
}

function renderToCanon(markdown: string): string {
  const { container } = render(<>{renderMarkdown(markdown)}</>);
  const result = canonFragment(container.childNodes);
  cleanup();
  return result;
}

// ---------------------------------------------------------------------------
// Backend parity
// ---------------------------------------------------------------------------

describe("parity with the backend renderer", () => {
  it("has a non-empty corpus", () => {
    expect(corpus.length).toBeGreaterThan(20);
  });

  for (const testCase of corpus) {
    it(`matches the backend for ${testCase.name}`, () => {
      expect(renderToCanon(testCase.markdown)).toBe(canonHtmlString(testCase.html));
    });
  }
});

// ---------------------------------------------------------------------------
// Safety
// ---------------------------------------------------------------------------

const HOSTILE = [
  "<script>alert(1)</script>",
  "<SCRIPT>alert(1)</SCRIPT>",
  "<img src=x onerror=alert(1)>",
  "<svg/onload=alert(1)>",
  "<iframe src=//evil.test></iframe>",
  "<style>body{background:url(//evil.test)}</style>",
  "<object data=//evil.test></object>",
  "<form action=//evil.test><input name=p></form>",
  '<a href="javascript:alert(1)">x</a>',
  "<div onclick=alert(1)>x</div>",
  "<!-- <script>alert(1)</script> -->",
  "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)>",
  "[x](javascript:alert(1))",
  "[x](JaVaScRiPt:alert(1))",
  "[x](data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==)",
  "[x](vbscript:msgbox(1))",
  "![x](javascript:alert(1))",
  "> <script>alert(1)</script>",
  "- <img src=x onerror=alert(1)>",
  "| a |\n|---|\n| <script>alert(1)</script> |",
];

describe("hostile bodies never become live markup", () => {
  for (const body of HOSTILE) {
    it(`neutralises ${JSON.stringify(body).slice(0, 60)}`, () => {
      const { container } = render(<>{renderMarkdown(body)}</>);

      for (const el of Array.from(container.querySelectorAll("*"))) {
        expect(ALLOWED_TAGS.has(el.tagName)).toBe(true);
        for (const attr of Array.from(el.attributes)) {
          expect(ALLOWED_ATTRS[el.tagName]?.has(attr.name) ?? false).toBe(true);
          expect(attr.name.startsWith("on")).toBe(false);
          if (attr.name === "href") {
            expect(/^\s*(javascript|vbscript|data):/i.test(attr.value)).toBe(false);
          }
        }
      }
      // Nothing executable, and no element the renderer does not construct.
      expect(container.querySelector("script")).toBeNull();
      expect(container.querySelector("img")).toBeNull();
      expect(container.querySelector("iframe")).toBeNull();
      expect(container.innerHTML).not.toMatch(/\son[a-z]+=/i);
    });
  }

  it("escapes rather than silently dropping the author's text", () => {
    // An author documenting <script> in an appendix must still see it.
    const { container } = render(<>{renderMarkdown("<script>alert(1)</script>")}</>);
    expect(container.textContent).toContain("<script>alert(1)</script>");
  });
});

describe("link policy", () => {
  it("allows http, https and mailto", () => {
    const { container } = render(
      <>{renderMarkdown("[a](https://e.test) [b](http://e.test) [c](mailto:x@e.test)")}</>,
    );
    expect(container.querySelectorAll("a")).toHaveLength(3);
  });

  it("marks external links noopener and new-tab", () => {
    const { container } = render(<>{renderMarkdown("[a](https://e.test)")}</>);
    const link = container.querySelector("a")!;
    expect(link.getAttribute("rel")).toBe("noopener noreferrer nofollow");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("allows relative links without a target", () => {
    const { container } = render(<>{renderMarkdown("[a](./annex-a)")}</>);
    const link = container.querySelector("a")!;
    expect(link.getAttribute("href")).toBe("./annex-a");
    expect(link.getAttribute("target")).toBeNull();
  });

  it("renders a rejected scheme as plain text, not a link", () => {
    const { container } = render(<>{renderMarkdown("[a](javascript:alert(1))")}</>);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("javascript:alert(1)");
  });
});

describe("format coverage", () => {
  it("renders the structures a real policy needs", () => {
    const { container } = render(
      <>
        {renderMarkdown(
          "# Policy\n\n## Scope\n\n**Bold** and _italic_ and ~~struck~~ and `code`.\n\n" +
            "- one\n- two\n\n1. first\n2. second\n\n> quoted\n\n---\n\n" +
            "| A | B |\n|:--|--:|\n| 1 | 2 |\n",
        )}
      </>,
    );
    for (const sel of [
      "h1", "h2", "strong", "em", "s", "code", "ul li", "ol li",
      "blockquote", "hr", "table thead th", "table tbody td",
    ]) {
      expect(container.querySelector(sel), `${sel} missing`).not.toBeNull();
    }
  });

  it("renders table alignment as a class, never a style attribute", () => {
    const { container } = render(
      <>{renderMarkdown("| A | B |\n|:--|--:|\n| 1 | 2 |")}</>,
    );
    expect(container.innerHTML).not.toContain("style=");
    expect(container.querySelector("th.ta-left")).not.toBeNull();
    expect(container.querySelector("th.ta-right")).not.toBeNull();
  });

  it("never emits an img, because there is no upload path until N.4", () => {
    const { container } = render(
      <>{renderMarkdown("![diagram](https://e.test/d.png)")}</>,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("a")?.getAttribute("href")).toBe("https://e.test/d.png");
  });

  it("returns nothing for empty bodies", () => {
    expect(renderMarkdown(null)).toEqual([]);
    expect(renderMarkdown("")).toEqual([]);
    expect(renderMarkdown("   \n  ")).toEqual([]);
  });
});

describe("MarkdownView failure handling", () => {
  it("shows a withheld message instead of taking the page down", () => {
    const { container } = render(<MarkdownView source="ok" />);
    expect(container.textContent).toContain("ok");
  });

  it("reports an empty version rather than rendering blank", () => {
    const { container } = render(<MarkdownView source="" />);
    expect(container.textContent).toContain("no content");
  });

  it("UnsafeMarkdownError is an Error subclass callers can branch on", () => {
    expect(new UnsafeMarkdownError("x")).toBeInstanceOf(Error);
  });
});
