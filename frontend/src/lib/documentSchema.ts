/**
 * The editor schema for document bodies (roadmap N.2).
 *
 * Constrained to exactly the nodes and marks that `tiptapMarkdown.ts` can
 * serialize, which is what makes the Markdown round-trip lossless by
 * construction rather than by care: an author cannot create something with
 * no Markdown representation because the schema has no node for it.
 *
 * Two things are deliberately off:
 *
 *   underline -- StarterKit 3 ships it, and Markdown has no underline. An
 *                author who could press Ctrl+U would lose that formatting
 *                silently on save.
 *   image     -- no upload path for `DocumentVersion.storage_key` until
 *                N.4, and the renderers emit no `<img>`.
 *
 * Tables have no merge-cells command, because GFM cannot express merged
 * cells. That was the acknowledged cost of choosing Markdown; the point of
 * leaving the command out is that the limit shows up as a button that
 * isn't there rather than as formatting that disappears on save.
 *
 * **The schema is asserted, not assumed.** `documentSchema.test.ts` checks
 * the resulting node and mark lists exactly. Extension option names are a
 * moving target across TipTap majors and an unrecognised key is silently
 * ignored rather than rejected, so "we passed underline: false" is not
 * evidence that underline is off -- the test is.
 */

import { TableKit } from "@tiptap/extension-table";
import { StarterKit } from "@tiptap/starter-kit";

/**
 * Column alignment, carried on cells so a GFM alignment row survives an
 * editor round-trip instead of being silently flattened on save.
 * `tiptapMarkdown.ts` reads and writes this attribute; nothing renders it
 * to a `style` attribute (see markdown.tsx on why alignment becomes a
 * class, never inline CSS).
 */
const alignAttribute = {
  align: {
    default: null as string | null,
    parseHTML: (element: HTMLElement) => element.getAttribute("data-align"),
    renderHTML: (attributes: Record<string, unknown>) =>
      attributes.align ? { "data-align": String(attributes.align) } : {},
  },
};

export const documentEditorExtensions = [
  StarterKit.configure({
    underline: false,
    link: {
      openOnClick: false,
      autolink: false,
      // Mirrors the renderers' allowlist. The editor is not the security
      // boundary -- markdown.tsx and markdown_doc.py are, and both
      // re-check every href -- but offering to create a link the exporter
      // will refuse to write is a bad experience, not a safe one.
      protocols: ["http", "https", "mailto"],
    },
  }),
  TableKit.configure({
    table: { resizable: false, allowTableNodeSelection: false },
    tableHeader: { HTMLAttributes: {} },
  }),
];

/** Node types the editor may contain, for the schema assertion test. */
export const EXPECTED_NODES = [
  "blockquote",
  "bulletList",
  "codeBlock",
  "doc",
  "hardBreak",
  "heading",
  "horizontalRule",
  "listItem",
  "orderedList",
  "paragraph",
  "table",
  "tableCell",
  "tableHeader",
  "tableRow",
  "text",
] as const;

/** Mark types the editor may contain, for the schema assertion test. */
export const EXPECTED_MARKS = ["bold", "code", "italic", "link", "strike"] as const;
