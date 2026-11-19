// What the Tool Import review screen says, kept out of the component so it
// can be read and tested on its own (docs/PLAN-tool-import.md, T.1).
//
// The backend sends each problem as a kind and a value
// (baseline_import.PROBLEM_KINDS); this file writes the sentence. Its
// validation messages never reach the screen (plan section 5.8).
import type { RowProblem } from "../types";

export const COLUMN_HELP = {
  control: "Which requirement this row is about.",
  responsible:
    "Does the tool do this for the customer, do they share it, or does the customer still own it " +
    "whatever the tool does? A vendor CRM listing a control is not an answer: most controls a " +
    "product touches, the customer still owns.",
  systems:
    "Where the tool's part happens. The customer's systems, or only the vendor's own platform, " +
    "which is never credited. Assists is credited exactly like the customer's systems at activation.",
  source:
    "Where this row came from: the uploaded document, or a web page that research added. A web " +
    "page can add a row; it never overrides a control the CRM disclaims.",
  covers:
    "How many of the control's objectives this row credits, and how many evidence items it asks " +
    "for. Open the row to change them.",
  expansion:
    "Open a row for what the vendor does, what the customer must do, any scope limit, a note, the " +
    "objectives and the evidence. These are written for the assessor who reads the result.",
} as const;

export const LEGEND_EXAMPLE =
  "Heimdal's scheduled scans run on the customer's endpoints: the customer's systems. Single " +
  "sign-on into the Heimdal console protects only the vendor's platform: not coverage, and never credited.";

export const PRODUCT_FIELDS = [
  { field: "name", label: "Tool name", hint: "As it appears in the tools library." },
  { field: "provider", label: "Vendor", hint: "" },
  {
    field: "key",
    label: "Short ID",
    hint: "Used in file names. It cannot change once the tool exists.",
  },
  { field: "role", label: "What it does here", hint: "One or two sentences, shown on the tool's page." },
  {
    field: "category",
    label: "Provider type",
    hint:
      "ESP: an external service provider that handles CUI or security protection data for the " +
      "customer (32 CFR 170.4). Say when it is also a cloud service provider (CSP).",
  },
  {
    field: "asset_type",
    label: "Asset category",
    hint:
      "How the tool's assets are scoped. SPA, security protection assets, for a tool that provides " +
      "security functions to the assessment scope (32 CFR 170.19).",
  },
] as const;

export const FRAMEWORK_TEXT = "NIST SP 800-171 Rev 2 / CMMC Level 2";

/** One item per line. A comma is part of an item, never a separator. */
export function parseAssumedConfig(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function formatAssumedConfig(items: string[]): string {
  return items.join("\n");
}

type Severity = "decide" | "wrong";

interface KindText {
  severity: Severity;
  marker: string;
  detail: (value: string | null) => string;
  banner: (n: number) => string;
}

const rows = (n: number) => `${n} row${n === 1 ? "" : "s"}`;
const has = (n: number) => (n === 1 ? "has" : "have");
const need = (n: number) => (n === 1 ? "needs" : "need");

function objectiveParts(value: string | null): { control: string; key: string } {
  const m = value?.match(/^(.*)\[([^\]]*)\]$/);
  return m ? { control: m[1], key: m[2] } : { control: "its control", key: value ?? "?" };
}

export const PROBLEM_TEXT: Record<string, KindText> = {
  classification_unset: {
    severity: "decide",
    marker: "Answer: who is responsible?",
    detail: () => "Nobody has said who is responsible for this control yet.",
    banner: (n) => `${rows(n)} ${need(n)} “Who is responsible?” answered`,
  },
  coverage_basis_unset: {
    severity: "decide",
    marker: "Answer: on whose systems?",
    detail: () =>
      "Nobody has said whether the tool's part happens on the customer's systems or only on the " +
      "vendor's own platform. The documents cannot say; a person who knows the deployment does.",
    banner: (n) => `${rows(n)} ${need(n)} “On whose systems?” answered`,
  },
  control_missing: {
    severity: "wrong",
    marker: "Name a control",
    detail: () => "This row names no control.",
    banner: (n) => `${rows(n)} ${n === 1 ? "names" : "name"} no control`,
  },
  control_unknown: {
    severity: "wrong",
    marker: "Unknown control",
    detail: (v) => `${v ?? "This control"} is not a control in the catalog.`,
    banner: (n) => `${rows(n)} ${has(n)} an unknown control`,
  },
  classification_invalid: {
    severity: "wrong",
    marker: "Choose who is responsible",
    detail: (v) => `“${v}” is not one of the answers to who is responsible.`,
    banner: (n) => `${rows(n)} ${has(n)} an answer to “Who is responsible?” that is not one of the choices`,
  },
  coverage_basis_invalid: {
    severity: "wrong",
    marker: "Choose on whose systems",
    detail: (v) => `“${v}” is not one of the answers to on whose systems.`,
    banner: (n) => `${rows(n)} ${has(n)} an answer to “On whose systems?” that is not one of the choices`,
  },
  objectives_malformed: {
    severity: "wrong",
    marker: "Fix the objectives",
    detail: () => "The objectives are not a list of objective letters.",
    banner: (n) => `${rows(n)} ${has(n)} objectives that are not a list`,
  },
  objective_unknown: {
    severity: "wrong",
    marker: "Objective not in this control",
    detail: (v) => {
      const { control, key } = objectiveParts(v);
      return `[${key}] is not an objective of ${control}.`;
    },
    banner: (n) => `${rows(n)} ${n === 1 ? "lists" : "list"} an objective its control does not have`,
  },
  evidence_malformed: {
    severity: "wrong",
    marker: "Fix the evidence",
    detail: () => "An evidence entry is not in the expected shape.",
    banner: (n) => `${rows(n)} ${has(n)} evidence that is not in the expected shape`,
  },
  evidence_on_customer_owns: {
    severity: "wrong",
    marker: "Remove the evidence",
    detail: () =>
      "The customer owns this control, so the tool has nothing to collect for it. Remove the " +
      "evidence, or change who is responsible.",
    banner: (n) => `${rows(n)} ${n === 1 ? "asks" : "ask"} for evidence on a control the customer owns`,
  },
  evidence_artifact_missing: {
    severity: "wrong",
    marker: "Describe the evidence",
    detail: () => "An evidence entry does not say what the artifact is.",
    banner: (n) => `${rows(n)} ${has(n)} evidence with no description`,
  },
  evidence_type_invalid: {
    severity: "wrong",
    marker: "Choose an evidence type",
    detail: (v) => `“${v}” is not an evidence type.`,
    banner: (n) => `${rows(n)} ${has(n)} evidence with an unknown type`,
  },
};

const FALLBACK: KindText = {
  severity: "wrong",
  marker: "Needs attention",
  detail: () => "Something on this row cannot be imported as it stands.",
  banner: (n) => `${rows(n)} ${need(n)} attention`,
};

export function kindText(kind: string | null | undefined): KindText {
  return (kind && PROBLEM_TEXT[kind]) || FALLBACK;
}

export const PRODUCT_PROBLEM_TEXT: Record<string, (field: string | null) => string> = {
  product_field_required: (f) => `${productLabel(f)} is required.`,
  product_key_invalid: () =>
    "The short ID must be lowercase letters, digits and hyphens, as a file name would hold.",
  file_malformed: () => "The file is not a baseline mapping: it has no product, or its controls are not a list.",
  row_malformed: () => "A control entry in the file is not in the expected shape.",
};

export function productLabel(field: string | null): string {
  const f = field?.replace(/^product\./, "");
  return PRODUCT_FIELDS.find((p) => p.field === f)?.label ?? "A tool field";
}

export function nonRowSentence(p: RowProblem): string {
  const fn = p.kind ? PRODUCT_PROBLEM_TEXT[p.kind] : undefined;
  return fn ? fn(p.field) : "Something about this tool cannot be imported as it stands.";
}

export interface BannerItem {
  kind: string;
  rows: number[];
  text: string;
  severity: Severity;
}

/** One entry per kind, counting rows (not problems), in the order a person
 * meets them: questions to answer first, then what is wrong. */
export function bannerItems(problems: RowProblem[]): BannerItem[] {
  const byKind = new Map<string, Set<number>>();
  for (const p of problems) {
    if (p.row_index === null) continue;
    const k = p.kind && PROBLEM_TEXT[p.kind] ? p.kind : "_other";
    if (!byKind.has(k)) byKind.set(k, new Set());
    byKind.get(k)!.add(p.row_index);
  }
  const items: BannerItem[] = [];
  for (const [kind, set] of byKind) {
    const t = kind === "_other" ? FALLBACK : PROBLEM_TEXT[kind];
    items.push({ kind, rows: [...set].sort((a, b) => a - b), text: t.banner(set.size), severity: t.severity });
  }
  const order = Object.keys(PROBLEM_TEXT);
  return items.sort((a, b) => {
    if (a.severity !== b.severity) return a.severity === "decide" ? -1 : 1;
    return order.indexOf(a.kind) - order.indexOf(b.kind);
  });
}

/** True when the row has something wrong, as opposed to a question still to
 * answer. Only these rows are tinted. */
export function rowIsWrong(problems: RowProblem[], rowIndex: number): boolean {
  return problems.some((p) => p.row_index === rowIndex && kindText(p.kind).severity === "wrong");
}

export function coverSummary(
  objectives: string[],
  controlObjectives: string[] | undefined,
  evidenceCount: number,
): string {
  const obj = controlObjectives
    ? `${objectives.length} of ${controlObjectives.length} objective${controlObjectives.length === 1 ? "" : "s"}`
    : `${objectives.length} objective${objectives.length === 1 ? "" : "s"}`;
  return `${obj} · ${evidenceCount} evidence item${evidenceCount === 1 ? "" : "s"}`;
}
