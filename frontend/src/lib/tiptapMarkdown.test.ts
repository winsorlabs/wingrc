// @vitest-environment node
//
// Round-trip coverage for the editor bridge (roadmap N.2).
//
// The claim this file defends is the one that makes a WYSIWYG editor safe
// to point at a Markdown store: opening a policy in the editor and saving
// it without typing anything must not change it. The editor schema is
// constrained to exactly the nodes with a Markdown representation, so the
// round-trip should be lossless by construction -- these tests are what
// prove "should be".
//
// Fixpoint, not equality: `md -> doc -> md'` may legitimately normalise
// (a `*` bullet becoming `-`, `__bold__` becoming `**bold**`). What must
// hold is that normalising again changes nothing (`md' === md''`) and that
// the document tree is stable (`doc' === doc''`) -- so a save is
// idempotent and a document does not drift a little on every edit.
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { docToMarkdown, markdownToDoc } from "./tiptapMarkdown";

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
}

const corpus: CorpusCase[] = JSON.parse(readFileSync(CORPUS_PATH, "utf8")).cases;

/** One normalisation pass: Markdown in, Markdown out. */
function pass(markdown: string): string {
  return docToMarkdown(markdownToDoc(markdown));
}

describe("round-trip is a fixpoint", () => {
  for (const testCase of corpus) {
    it(`stabilises after one pass for ${testCase.name}`, () => {
      const once = pass(testCase.markdown);
      const twice = pass(once);
      expect(twice).toBe(once);
    });

    it(`produces a stable document tree for ${testCase.name}`, () => {
      const once = pass(testCase.markdown);
      expect(markdownToDoc(pass(once))).toEqual(markdownToDoc(once));
    });
  }
});

describe("content survives the round-trip", () => {
  const cases: Array<[string, string]> = [
    ["heading", "# Access Control Policy"],
    ["paragraph", "All users must authenticate."],
    ["bold", "This is **mandatory** for staff."],
    ["italic", "This is _recommended_ only."],
    ["strike", "This rule is ~~withdrawn~~."],
    ["inline code", "Set `PasswordMinimumLength` to 14."],
    ["link", "See [NIST](https://csrc.nist.gov) for detail."],
    ["bullet list", "- One\n- Two\n- Three"],
    ["ordered list", "1. One\n2. Two\n3. Three"],
    ["blockquote", "> Exceptions require approval."],
    ["horizontal rule", "Before\n\n---\n\nAfter"],
    ["code block", "```\nliteral text\n```"],
    ["table", "| Role | Owner |\n| --- | --- |\n| ISSO | MSP |"],
    ["aligned table", "| L | C | R |\n| :--- | :---: | ---: |\n| a | b | c |"],
    ["nested list", "- Outer\n  - Inner"],
    ["multi paragraph", "First paragraph.\n\nSecond paragraph."],
  ];

  for (const [name, markdown] of cases) {
    it(`keeps the visible text for a ${name}`, () => {
      const out = pass(markdown);
      // Every word the author wrote is still present. Structure is checked
      // by the fixpoint tests; this catches silent content loss.
      const words = markdown.match(/[A-Za-z][A-Za-z0-9]+/g) ?? [];
      for (const word of words) {
        expect(out, `${name}: lost ${word}`).toContain(word);
      }
    });
  }

  it("preserves table column alignment", () => {
    const out = pass("| L | C | R |\n| :--- | :---: | ---: |\n| a | b | c |");
    expect(out).toContain(":---");
    expect(out).toContain(":---:");
    expect(out).toContain("---:");
  });

  it("keeps a table's header row distinct from its body", () => {
    const doc = markdownToDoc("| H |\n| --- |\n| b |");
    const table = doc.content?.[0];
    expect(table?.type).toBe("table");
    expect(table?.content?.[0].content?.[0].type).toBe("tableHeader");
    expect(table?.content?.[1].content?.[0].type).toBe("tableCell");
  });
});

describe("structure that would change meaning is escaped", () => {
  it("does not let literal text become a heading", () => {
    const out = pass("\\# not a heading");
    expect(markdownToDoc(out).content?.[0].type).toBe("paragraph");
  });

  it("does not let literal text become a list", () => {
    const doc = markdownToDoc("\\- not a list");
    expect(doc.content?.[0].type).toBe("paragraph");
    const out = docToMarkdown(doc);
    expect(markdownToDoc(out).content?.[0].type).toBe("paragraph");
  });

  it("keeps asterisks that are not emphasis", () => {
    const out = pass("A literal \\*asterisk\\* here.");
    expect(pass(out)).toBe(out);
    expect(out).toContain("asterisk");
  });

  it("keeps a pipe inside a table cell", () => {
    const out = pass("| A |\n| --- |\n| a \\| b |");
    expect(pass(out)).toBe(out);
  });
});

describe("link safety carries into the editor bridge", () => {
  it("never carries a javascript: href as a link mark", () => {
    // The text `[x](javascript:alert(1))` IS present in the document -- it
    // is what the author wrote, and markdown-it correctly renders it as
    // prose rather than a link. What must not exist is a link mark, which
    // is the only thing that could become a clickable href.
    const doc = markdownToDoc("[x](javascript:alert(1))");
    const marks = JSON.stringify(doc).match(/"type":"link"/g) ?? [];
    expect(marks).toHaveLength(0);
    expect(JSON.stringify(doc)).toContain("[x](javascript:alert(1))");
  });

  it("refuses to serialize a disallowed href back out", () => {
    const out = docToMarkdown({
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [
            { type: "text", text: "x", marks: [{ type: "link", attrs: { href: "javascript:alert(1)" } }] },
          ],
        },
      ],
    });
    expect(out).not.toContain("javascript:");
    expect(out).toContain("x");
  });

  it("keeps an allowed href", () => {
    const out = docToMarkdown({
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [
            { type: "text", text: "x", marks: [{ type: "link", attrs: { href: "https://e.test" } }] },
          ],
        },
      ],
    });
    expect(out).toContain("[x](https://e.test)");
  });
});

describe("empty input", () => {
  it("produces an empty-but-valid document", () => {
    expect(markdownToDoc("")).toEqual({ type: "doc", content: [{ type: "paragraph" }] });
    expect(markdownToDoc(null)).toEqual({ type: "doc", content: [{ type: "paragraph" }] });
  });

  it("serializes an empty document to an empty body", () => {
    expect(docToMarkdown({ type: "doc", content: [{ type: "paragraph" }] }).trim()).toBe("");
  });
});
