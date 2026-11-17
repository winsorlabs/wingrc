// @vitest-environment jsdom
//
// The readiness bars on the rendered objective row (docs/roadmap.md, "The
// readiness bars"). The rules are pinned in lib/readiness.test.ts; this
// file asserts what a person sees, including without colour.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ControlStateRow } from "../types";
import { ObjectiveRow } from "./ObjectiveRow";

vi.mock("../api", () => ({ api: { patchControlState: vi.fn() } }));

afterEach(cleanup);

function row(overrides: Partial<ControlStateRow> = {}): ControlStateRow {
  return {
    id: "cs-1",
    objective_id: "o",
    control_id: "AC.L2-3.1.1",
    control_db_id: "d",
    family: "AC",
    control_title: "Test",
    objective_key: "a",
    objective_text: "Test objective",
    status: "pending_evidence",
    responsibility: "customer_owns",
    contributors: [],
    statement_status: null,
    evidence_count: 0,
    raci_letters: [],
    sprs_weight: 1,
    is_level_1: false,
    ...overrides,
  };
}

function draw(r: ControlStateRow) {
  render(
    <ObjectiveRow row={r} orgId="org" assessmentId="a" canWrite={false} onStatusChange={() => {}} />
  );
  const bar = screen.getByRole("group", { name: "Readiness" });
  return Array.from(bar.querySelectorAll<HTMLElement>(".rb-seg"));
}

describe("ReadinessBar on the objective row", () => {
  it("an objective whose only evidence is archived reads empty on Evidence", () => {
    // The server counts live links only (test_evidence_links.py
    // test_counts_and_widgets_read_live_links_only): an archived-only
    // objective arrives with evidence_count 0.
    const segs = draw(
      row({ statement_status: "approved", raci_letters: ["A", "R"], evidence_count: 0 })
    );
    const evidence = segs.find((s) => s.dataset.segment === "evidence")!;
    expect(evidence.className).toContain("rb-empty");
    expect(evidence.getAttribute("aria-label")).toBe("Evidence: empty (no live evidence)");
  });

  it("says every segment's state in words, so colour is not the only cue", () => {
    const segs = draw(row({ statement_status: "draft", raci_letters: ["R"], evidence_count: 2 }));
    expect(segs.map((s) => s.getAttribute("aria-label"))).toEqual([
      "Implementation statement: partial (draft)",
      "RACI: partial (R held; no A)",
      "Evidence: complete (2 attached)",
    ]);
    expect(segs.map((s) => s.getAttribute("title"))).toEqual(
      segs.map((s) => s.getAttribute("aria-label"))
    );
    expect(segs.map((s) => s.textContent)).toEqual(["Impl", "RACI", "Evid"]);
  });

  it("marks each state with its own fill class, not only its colour", () => {
    const segs = draw(row({ statement_status: "reviewed", raci_letters: ["C"], evidence_count: 0 }));
    expect(segs.map((s) => s.className)).toEqual([
      "rb-seg rb-good",
      "rb-seg rb-partial",
      "rb-seg rb-empty",
    ]);
    for (const s of segs) expect(s.querySelector(".rb-mark")).not.toBeNull();
  });

  it("a provider-satisfied objective with no statement reads empty on Implementation", () => {
    const segs = draw(
      row({ responsibility: "provider_satisfies", raci_letters: ["A", "R"], evidence_count: 1 })
    );
    expect(segs[0].getAttribute("aria-label")).toBe(
      "Implementation statement: empty (none)"
    );
    expect(screen.getByText("MSP")).toBeTruthy();
  });

  it("leaves the status badge as the only word for needs_review", () => {
    draw(row({ status: "needs_review", statement_status: "approved", raci_letters: ["A", "R"], evidence_count: 1 }));
    expect(screen.getAllByText("Needs Review")).toHaveLength(1);
    const bar = screen.getByRole("group", { name: "Readiness" });
    expect(bar.textContent).not.toMatch(/review/i);
  });
});
