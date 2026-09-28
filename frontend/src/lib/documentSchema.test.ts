// @vitest-environment node
//
// The editor schema is the thing that makes the Markdown round-trip
// lossless by construction, so it is asserted rather than assumed.
//
// TipTap extension option names move between majors, and passing an
// unrecognised option is silently ignored rather than rejected -- so
// "StarterKit.configure({ underline: false })" is not by itself evidence
// that underline is off. If a future upgrade renames that key, or adds a
// node with no Markdown representation to StarterKit's defaults, this test
// fails instead of authors quietly losing formatting on save.
import { getSchema } from "@tiptap/core";
import { describe, expect, it } from "vitest";

import {
  EXPECTED_MARKS,
  EXPECTED_NODES,
  documentEditorExtensions,
} from "./documentSchema";
import { docToMarkdown, markdownToDoc } from "./tiptapMarkdown";

const schema = getSchema(documentEditorExtensions);

describe("editor schema", () => {
  it("contains exactly the node types the serializer handles", () => {
    expect(Object.keys(schema.nodes).sort()).toEqual([...EXPECTED_NODES].sort());
  });

  it("contains exactly the mark types the serializer handles", () => {
    expect(Object.keys(schema.marks).sort()).toEqual([...EXPECTED_MARKS].sort());
  });

  it("has no underline mark, because Markdown cannot express one", () => {
    expect(schema.marks.underline).toBeUndefined();
  });

  it("has no image node, because there is no upload path until N.4", () => {
    expect(schema.nodes.image).toBeUndefined();
  });

  it("carries an align attribute on table cells so GFM alignment survives", () => {
    expect(schema.nodes.tableHeader.spec.attrs?.align).toBeDefined();
    expect(schema.nodes.tableCell.spec.attrs?.align).toBeDefined();
  });
});

describe("every schema node round-trips through Markdown", () => {
  // Paired with the schema assertion above: that one proves the schema
  // holds no *extra* node; this one proves the serializer handles every
  // node it does hold. Together they close the gap in both directions.
  const samples: Record<string, string> = {
    doc: "text",
    text: "text",
    paragraph: "A paragraph.",
    heading: "# Heading",
    bulletList: "- item",
    orderedList: "1. item",
    listItem: "- item",
    blockquote: "> quoted",
    codeBlock: "```\ncode\n```",
    horizontalRule: "---",
    hardBreak: "line one  \nline two",
    table: "| H |\n| --- |\n| c |",
    tableRow: "| H |\n| --- |\n| c |",
    tableHeader: "| H |\n| --- |\n| c |",
    tableCell: "| H |\n| --- |\n| c |",
  };

  it("has a sample for every node in the schema", () => {
    expect(Object.keys(samples).sort()).toEqual(Object.keys(schema.nodes).sort());
  });

  for (const [node, markdown] of Object.entries(samples)) {
    it(`survives a round-trip for ${node}`, () => {
      const once = docToMarkdown(markdownToDoc(markdown));
      expect(docToMarkdown(markdownToDoc(once))).toBe(once);
      expect(once.trim()).not.toBe("");
    });
  }

  const markSamples: Record<string, string> = {
    bold: "**bold**",
    italic: "_italic_",
    strike: "~~struck~~",
    code: "`code`",
    link: "[text](https://e.test)",
  };

  it("has a sample for every mark in the schema", () => {
    expect(Object.keys(markSamples).sort()).toEqual(Object.keys(schema.marks).sort());
  });

  for (const [mark, markdown] of Object.entries(markSamples)) {
    it(`survives a round-trip for the ${mark} mark`, () => {
      const once = docToMarkdown(markdownToDoc(markdown));
      expect(docToMarkdown(markdownToDoc(once))).toBe(once);
      expect(once).toMatch(/\w/);
    });
  }
});
