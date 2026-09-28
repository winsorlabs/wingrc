/**
 * The bridge between stored Markdown and the TipTap editor (roadmap N.2).
 *
 * `DocumentVersion.body` is GFM-subset Markdown (see
 * `backend/app/markdown_doc.py` for why). TipTap's document model is a
 * ProseMirror tree, so editing needs a conversion each way:
 *
 *   markdownToDoc  -- loads a stored body into the editor
 *   docToMarkdown  -- turns what the author typed back into a body
 *
 * **Why this is in-repo rather than a library call.** TipTap 3 does ship
 * per-node Markdown specs (`parseMarkdown`/`renderMarkdown` on the
 * StarterKit nodes, driven by `marked`), and consolidating onto them is a
 * reasonable future simplification. Two reasons not to now. First, the
 * inbound direction has to agree with `markdown.tsx` and the backend, and
 * those are `markdown-it`; routing the editor through a *second* Markdown
 * parser is precisely the divergence this slice is trying to avoid, so
 * `markdownToDoc` consumes `parseMarkdown`'s token stream from
 * `markdown.tsx` -- one parse, three consumers. Second, owning the
 * outbound direction is what lets the supported subset be exactly the
 * subset: nothing can be typed that has no Markdown representation,
 * because there is no node type here that lacks one.
 *
 * **Lossless by construction, and tested that way.** The editor schema
 * (`documentEditorExtensions`) is constrained to exactly the nodes and
 * marks below, and `tiptapMarkdown.test.ts` asserts the fixpoint
 * `md -> doc -> md' -> doc'` with `md' === md''` and `doc === doc'` over
 * the shared corpus. A construct that cannot survive the round-trip fails
 * a test rather than quietly rewriting someone's policy on save.
 *
 * Known and deliberate: GFM cannot express merged table cells, so the
 * editor's table has no merge command (Jarrod accepted that cost when the
 * format was chosen). Column *alignment* is carried through as an `align`
 * attribute rather than dropped -- see `alignedTableExtensions`.
 */

// See markdown.tsx on why Token comes from markdown-it, not @types.
import type { Token } from "markdown-it";

import { isAllowedLink, parseMarkdown } from "./markdown";

export interface TiptapMark {
  type: string;
  attrs?: Record<string, unknown>;
}

export interface TiptapNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: TiptapNode[];
  marks?: TiptapMark[];
  text?: string;
}

const ALIGN_FROM_STYLE: Record<string, string> = {
  "text-align:left": "left",
  "text-align:center": "center",
  "text-align:right": "right",
};

// ---------------------------------------------------------------------------
// Markdown -> TipTap
// ---------------------------------------------------------------------------

interface Builder {
  type: string;
  attrs?: Record<string, unknown>;
  content: TiptapNode[];
}

function markFor(token: Token): TiptapMark | null {
  switch (token.tag) {
    case "strong":
      return { type: "bold" };
    case "em":
      return { type: "italic" };
    case "s":
      return { type: "strike" };
    case "a": {
      const href = token.attrGet("href") ?? "";
      // Defence in depth: markdown-it has already refused a bad scheme, so
      // an href here should always pass. If it somehow does not, the text
      // keeps its content and loses only the link.
      return isAllowedLink(href) ? { type: "link", attrs: { href } } : null;
    }
    case "code":
      return { type: "code" };
    default:
      return null;
  }
}

function inlineNodes(tokens: Token[]): TiptapNode[] {
  const out: TiptapNode[] = [];
  const active: TiptapMark[] = [];

  const push = (text: string) => {
    if (!text) return;
    const marks = active.length ? active.map((m) => ({ ...m })) : undefined;
    out.push(marks ? { type: "text", text, marks } : { type: "text", text });
  };

  for (const token of tokens) {
    switch (token.type) {
      case "text":
        push(token.content);
        break;
      case "code_inline":
        out.push({ type: "text", text: token.content, marks: [...active, { type: "code" }] });
        break;
      case "softbreak":
        push(" ");
        break;
      case "hardbreak":
        out.push({ type: "hardBreak" });
        break;
      case "html_inline":
        // Unreachable with html:false; if it ever fires the raw markup is
        // kept as literal text, never interpreted.
        push(token.content);
        break;
      default:
        if (token.nesting === 1) {
          const mark = markFor(token);
          if (mark) active.push(mark);
          else active.push({ type: "__ignored__" });
        } else if (token.nesting === -1) {
          active.pop();
        } else if (token.content) {
          push(token.content);
        }
    }
  }
  // `__ignored__` placeholders keep open/close balanced for a tag that maps
  // to no mark; strip them off the emitted nodes.
  for (const node of out) {
    if (node.marks) {
      node.marks = node.marks.filter((m) => m.type !== "__ignored__");
      if (!node.marks.length) delete node.marks;
    }
  }
  return out;
}

/** Load a stored Markdown body into a TipTap document. */
export function markdownToDoc(source: string | null | undefined): TiptapNode {
  const tokens = parseMarkdown(source ?? "");
  const root: Builder = { type: "doc", content: [] };
  const stack: Builder[] = [root];
  // Column alignment is declared once on the header row and applies to
  // every cell in that column, so it is tracked per-table rather than
  // read off each cell.
  const alignStack: (string | null)[][] = [];
  let columnIndex = 0;

  const top = () => stack[stack.length - 1];

  const open = (type: string, attrs?: Record<string, unknown>) => {
    const node: Builder = { type, attrs, content: [] };
    stack.push(node);
  };
  const close = () => {
    const node = stack.pop();
    if (!node) return;
    const built: TiptapNode = { type: node.type };
    if (node.attrs && Object.keys(node.attrs).length) built.attrs = node.attrs;
    if (node.content.length) built.content = node.content;
    top().content.push(built);
  };

  for (const token of tokens) {
    switch (token.type) {
      case "paragraph_open":
        open("paragraph");
        break;
      case "paragraph_close":
        close();
        break;
      case "heading_open":
        open("heading", { level: Number(token.tag.slice(1)) });
        break;
      case "heading_close":
        close();
        break;
      case "bullet_list_open":
        open("bulletList");
        break;
      case "bullet_list_close":
        close();
        break;
      case "ordered_list_open": {
        const start = token.attrGet("start");
        open("orderedList", { start: start ? Number(start) : 1 });
        break;
      }
      case "ordered_list_close":
        close();
        break;
      case "list_item_open":
        open("listItem");
        break;
      case "list_item_close":
        close();
        break;
      case "blockquote_open":
        open("blockquote");
        break;
      case "blockquote_close":
        close();
        break;
      case "hr":
        top().content.push({ type: "horizontalRule" });
        break;
      case "fence":
      case "code_block":
        top().content.push({
          type: "codeBlock",
          attrs: { language: null },
          content: token.content.replace(/\n$/, "")
            ? [{ type: "text", text: token.content.replace(/\n$/, "") }]
            : undefined,
        });
        break;
      case "table_open":
        open("table");
        alignStack.push([]);
        break;
      case "table_close":
        alignStack.pop();
        close();
        break;
      case "thead_open":
      case "tbody_open":
        // GFM has exactly one header row and one body; TipTap's table is a
        // flat list of rows with header cells marked per cell, so these
        // group tokens carry no structure worth keeping.
        break;
      case "thead_close":
      case "tbody_close":
        break;
      case "tr_open":
        open("tableRow");
        columnIndex = 0;
        break;
      case "tr_close":
        close();
        break;
      case "th_open":
      case "td_open": {
        const aligns = alignStack[alignStack.length - 1] ?? [];
        if (token.type === "th_open") {
          const style = token.attrGet("style");
          aligns[columnIndex] =
            (style && ALIGN_FROM_STYLE[style.replace(/\s/g, "").toLowerCase()]) || null;
        }
        const align = aligns[columnIndex] ?? null;
        open(token.type === "th_open" ? "tableHeader" : "tableCell", {
          colspan: 1,
          rowspan: 1,
          colwidth: null,
          align,
        });
        break;
      }
      case "th_close":
      case "td_close":
        // A GFM cell holds inline content; TipTap requires block content
        // inside a cell, so the inline run is wrapped in a paragraph.
        {
          const cell = stack[stack.length - 1];
          if (cell && cell.content.length && cell.content[0].type === "text") {
            cell.content = [{ type: "paragraph", content: cell.content }];
          } else if (cell && !cell.content.length) {
            cell.content = [{ type: "paragraph" }];
          }
        }
        close();
        columnIndex += 1;
        break;
      case "inline": {
        const nodes = inlineNodes(token.children ?? []);
        const parent = top();
        if (parent.type === "tableHeader" || parent.type === "tableCell") {
          parent.content.push(...nodes);
        } else {
          parent.content.push(...nodes);
        }
        break;
      }
      case "html_block":
        top().content.push({ type: "paragraph", content: [{ type: "text", text: token.content.trim() }] });
        break;
      default:
        break;
    }
  }

  const doc: TiptapNode = { type: "doc", content: root.content };
  if (!doc.content?.length) doc.content = [{ type: "paragraph" }];
  return doc;
}

// ---------------------------------------------------------------------------
// TipTap -> Markdown
// ---------------------------------------------------------------------------

/**
 * Escape the characters that would otherwise change the structure when the
 * body is parsed back.
 *
 * Deliberately conservative rather than minimal: over-escaping renders
 * identically, under-escaping silently changes a policy's meaning (a line
 * beginning "- " becoming a list, "#" becoming a heading). The fixpoint
 * test is what keeps it honest -- a wrong escape shows up as a round-trip
 * mismatch.
 */
function escapeInline(text: string): string {
  return text.replace(/([\\`*_[\]~|])/g, "\\$1");
}

function escapeLineStart(text: string): string {
  return text.replace(/^(\s*)(#{1,6}\s|>|[-+*]\s|\d+[.)]\s)/, (_m, ws, marker) => `${ws}\\${marker}`);
}

function inlineCode(text: string): string {
  const runs = text.match(/`+/g) ?? [];
  const longest = runs.reduce((n, r) => Math.max(n, r.length), 0);
  const fence = "`".repeat(longest + 1);
  const pad = text.startsWith("`") || text.endsWith("`") || text.startsWith(" ") ? " " : "";
  return `${fence}${pad}${text}${pad}${fence}`;
}

function serializeTextNode(node: TiptapNode): string {
  const marks = node.marks ?? [];
  const has = (type: string) => marks.some((m) => m.type === type);
  let out: string;

  if (has("code")) {
    // Inline code takes no nested emphasis in Markdown, so the other
    // character marks are dropped rather than emitted as literal
    // asterisks inside the code span.
    out = inlineCode(node.text ?? "");
  } else {
    out = escapeInline(node.text ?? "");
    if (has("italic")) out = `_${out}_`;
    if (has("strike")) out = `~~${out}~~`;
    if (has("bold")) out = `**${out}**`;
  }

  const link = marks.find((m) => m.type === "link");
  if (link) {
    const href = String(link.attrs?.href ?? "");
    if (isAllowedLink(href)) out = `[${out}](${href})`;
  }
  return out;
}

function serializeInline(nodes: TiptapNode[] | undefined): string {
  if (!nodes) return "";
  return nodes
    .map((node) => {
      if (node.type === "text") return serializeTextNode(node);
      if (node.type === "hardBreak") return "  \n";
      return "";
    })
    .join("");
}

function indentBlock(text: string, indent: string, firstPrefix: string): string {
  const lines = text.replace(/\n+$/, "").split("\n");
  return lines
    .map((line, i) => {
      if (i === 0) return `${firstPrefix}${line}`;
      return line ? `${indent}${line}` : "";
    })
    .join("\n");
}

function alignMarker(align: string | null | undefined, width = 3): string {
  const dashes = "-".repeat(Math.max(width, 3));
  switch (align) {
    case "left":
      return `:${dashes}`;
    case "center":
      return `:${dashes}:`;
    case "right":
      return `${dashes}:`;
    default:
      return dashes;
  }
}

function serializeTable(node: TiptapNode): string {
  const rows = node.content ?? [];
  if (!rows.length) return "";

  const cellText = (cell: TiptapNode): string => {
    const blocks = cell.content ?? [];
    // GFM cells are inline-only. A cell containing more than one block (or
    // a list, or a nested table) has no Markdown representation, so the
    // editor schema does not permit one -- see documentEditorExtensions.
    return blocks
      .map((b) => (b.type === "paragraph" ? serializeInline(b.content) : serializeInline(b.content)))
      .join(" ")
      .trim();
  };

  const [headerRow, ...bodyRows] = rows;
  const headerCells = headerRow.content ?? [];
  const aligns = headerCells.map((c) => (c.attrs?.align as string | null) ?? null);

  const lines: string[] = [];
  lines.push(`| ${headerCells.map(cellText).join(" | ")} |`);
  lines.push(`| ${aligns.map((a) => alignMarker(a)).join(" | ")} |`);
  for (const row of bodyRows) {
    const cells = row.content ?? [];
    lines.push(`| ${cells.map(cellText).join(" | ")} |`);
  }
  return lines.join("\n") + "\n\n";
}

function serializeBlock(node: TiptapNode, depth = 0): string {
  switch (node.type) {
    case "paragraph": {
      const text = serializeInline(node.content);
      return text.trim() ? `${escapeLineStart(text)}\n\n` : "\n";
    }
    case "heading": {
      const level = Math.min(Math.max(Number(node.attrs?.level ?? 1), 1), 6);
      return `${"#".repeat(level)} ${serializeInline(node.content)}\n\n`;
    }
    case "blockquote": {
      const inner = (node.content ?? []).map((c) => serializeBlock(c, depth)).join("");
      return (
        inner
          .replace(/\n+$/, "")
          .split("\n")
          .map((l) => (l ? `> ${l}` : ">"))
          .join("\n") + "\n\n"
      );
    }
    case "bulletList":
    case "orderedList": {
      const ordered = node.type === "orderedList";
      const start = ordered ? Number(node.attrs?.start ?? 1) : 1;
      const items = (node.content ?? []).map((item, i) => {
        const marker = ordered ? `${start + i}. ` : "- ";
        const indent = " ".repeat(marker.length);
        const inner = (item.content ?? []).map((c) => serializeBlock(c, depth + 1)).join("");
        return indentBlock(inner, indent, marker);
      });
      return items.join("\n") + "\n\n";
    }
    case "codeBlock": {
      const text = (node.content ?? []).map((c) => c.text ?? "").join("");
      return "```\n" + text + "\n```\n\n";
    }
    case "horizontalRule":
      return "---\n\n";
    case "table":
      return serializeTable(node);
    default:
      // An unknown node is a schema bug, not user input: the editor cannot
      // produce one. Serialize its inline content rather than dropping the
      // author's words on the floor.
      return node.content ? serializeInline(node.content) + "\n\n" : "";
  }
}

/** Turn the editor's document back into a storable Markdown body. */
export function docToMarkdown(doc: TiptapNode | null | undefined): string {
  if (!doc || !doc.content) return "";
  const out = doc.content.map((node) => serializeBlock(node)).join("");
  return out.replace(/\n{3,}/g, "\n\n").replace(/^\n+/, "").replace(/\s+$/, "") + "\n";
}
