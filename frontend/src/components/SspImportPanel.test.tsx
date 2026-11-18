// @vitest-environment jsdom
//
// The SSP import picker: only offered candidates, with their catalog text, and none preselected.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { RowCard } from "./SspImportPanel";
import type { SspImportRow } from "../types";

afterEach(cleanup);

const nearRow: SspImportRow = {
  id: "r3", number: 3, family: "AC", heading: "8.1 Access Control",
  control_text: "Devices including other systems authorized to connect to the system are identified.",
  statement: "Devices are enrolled before they connect.", source_status: "Met",
  notes_present: false, raci_present: true, match_kind: "unmatched",
  objective_id: null, objective_ref: null, objective_text: null,
  candidates: [
    { objective_id: "c", ref: "AC.L2-3.1.1[c]", score: 0.94, text: "Devices (and other systems) authorized to connect to the system are identified.", exact_text: false, offered: true },
    { objective_id: "b", ref: "AC.L2-3.1.1[b]", score: 0.57, text: "Processes acting on behalf of authorized users are identified.", exact_text: false, offered: false },
  ],
  near_match_floor: 0.8, acceptable_onto: ["c"], not_acceptable: null, decision: "pending",
  resolution: null, decided_objective_id: null, decided_ref: null, statement_id: null,
  decided_by: null, decided_at: null, existing: null, shares_objective_with: [],
};

describe("the near-match picker", () => {
  it("offers only candidates above the floor, with their text, and preselects none", () => {
    render(<RowCard orgId="o" assessmentId="a" sessionId="s" row={nearRow} onView={() => {}} edit={undefined} onEdit={() => {}} />);
    const radios = screen.getAllByRole("radio") as HTMLInputElement[];
    expect(radios).toHaveLength(1);
    expect(radios.filter((r) => r.checked)).toHaveLength(0);
    expect(screen.getByText("Devices (and other systems) authorized to connect to the system are identified.")).toBeTruthy();
    expect(screen.getByText(/Below the 0.80 floor, not offered: AC.L2-3.1.1\[b\] \(0.57\)/)).toBeTruthy();
    const accept = screen.getByRole("button", { name: /^Accept/ }) as HTMLButtonElement;
    expect(accept.disabled).toBe(true);
  });
});
