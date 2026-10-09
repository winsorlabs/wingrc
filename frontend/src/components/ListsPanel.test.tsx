// @vitest-environment jsdom
//
// Scope > Lists: placeholders render as known-absent rather than blank, an
// empty 3.1.1b explains itself instead of looking like a failed sync, a
// read-only role can still export, and "+ Add process" is offered only
// where it belongs.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { ListViewData, ListViewSummary } from "../types";
import { ListsPanel } from "./ListsPanel";

vi.mock("../api", () => ({
  api: {
    listViews: vi.fn(),
    getListView: vi.fn(),
    exportListView: vi.fn(),
    createScopeEntity: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const SUMMARIES: ListViewSummary[] = [
  {
    id: "3.1.1c-authorized-devices", sheet_title: "3.1.1c Authorized Devices",
    title: "Authorized Devices", control_ids: ["AC.L2-3.1.1"], description: "",
    entity_type: "device", row_count: 1, excluded_out_of_boundary: 1,
  },
  {
    id: "3.1.1b-auth-processes", sheet_title: "3.1.1b Auth Processes",
    title: "Processes Acting on Behalf of Authorized Users", control_ids: ["AC.L2-3.1.1"],
    description: "", entity_type: "process", row_count: 0, excluded_out_of_boundary: 0,
  },
];

const DEVICES: ListViewData = {
  ...SUMMARIES[0],
  columns: ["Name", "BIOS FW Ver", "Location"],
  rows: [
    {
      natural_key: "SN-1",
      cells: [
        { value: "WS-0001", placeholder_reason: null },
        {
          value: "[PLACEHOLDER - BIOS FW version not collected]",
          placeholder_reason: "BIOS FW version not collected",
        },
        { value: "", placeholder_reason: null },
      ],
    },
  ],
  empty_explanation: "",
  excluded_out_of_boundary: 1,
  excluded_note: "1 entity excluded as out of the CUI boundary (in_boundary = false).",
};

const PROCESSES: ListViewData = {
  ...SUMMARIES[1],
  columns: ["Process Name", "Running On", "Associated Account", "Description / Purpose"],
  rows: [],
  empty_explanation: "No processes recorded. ... add them manually.",
  excluded_out_of_boundary: 0,
  excluded_note: "",
};

function mockViews() {
  vi.mocked(api.listViews).mockResolvedValue(SUMMARIES);
  vi.mocked(api.getListView).mockImplementation(async (_org, id) =>
    id === PROCESSES.id ? PROCESSES : DEVICES,
  );
}

describe("ListsPanel", () => {
  it("renders a placeholder as known absent with its reason, distinct from empty", async () => {
    mockViews();
    render(<ListsPanel orgId="org-1" canWrite={true} />);

    const cell = await screen.findByText("[PLACEHOLDER - BIOS FW version not collected]");
    expect(cell.tagName).toBe("TD");
    expect(cell.className).toContain("list-cell-placeholder");
    expect(cell.getAttribute("title")).toContain("BIOS FW version not collected");
    expect(screen.getByText("—").closest("td")?.className ?? "").not.toContain(
      "list-cell-placeholder",
    );
  });

  it("states how many entities were excluded as out of boundary", async () => {
    mockViews();
    render(<ListsPanel orgId="org-1" canWrite={false} />);
    expect((await screen.findByRole("note")).textContent).toMatch(
      /1 entity excluded as out of the CUI boundary/,
    );
  });

  it("explains an empty 3.1.1b instead of showing a bare table", async () => {
    mockViews();
    render(<ListsPanel orgId="org-1" canWrite={true} />);
    await screen.findByText("WS-0001");

    fireEvent.click(screen.getByRole("tab", { name: /3\.1\.1b Auth Processes/ }));

    await screen.findByText(/No processes recorded/);
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByRole("button", { name: "+ Add process" })).toBeTruthy();
  });

  it("adds a process keyed by the workbook's own headers", async () => {
    mockViews();
    vi.mocked(api.createScopeEntity).mockResolvedValue({} as never);
    render(<ListsPanel orgId="org-1" canWrite={true} />);
    await screen.findByText("WS-0001");
    fireEvent.click(screen.getByRole("tab", { name: /3\.1\.1b/ }));
    fireEvent.click(await screen.findByRole("button", { name: "+ Add process" }));

    fireEvent.change(screen.getByPlaceholderText("Liongard inspector agent"), {
      target: { value: "Liongard inspector agent" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add process" }));

    await waitFor(() => expect(api.createScopeEntity).toHaveBeenCalled());
    expect(vi.mocked(api.createScopeEntity).mock.calls[0][1]).toMatchObject({
      entity_type: "process",
      natural_key: "Liongard inspector agent",
      attributes: { "Process Name": "Liongard inspector agent" },
    });
  });

  it("lets a read-only role export but not add", async () => {
    mockViews();
    vi.mocked(api.exportListView).mockResolvedValue();
    render(<ListsPanel orgId="org-1" canWrite={false} />);
    await screen.findByText("WS-0001");

    fireEvent.click(screen.getByRole("button", { name: "Export .xlsx" }));
    await waitFor(() =>
      expect(api.exportListView).toHaveBeenCalledWith("org-1", "3.1.1c-authorized-devices"),
    );

    fireEvent.click(screen.getByRole("tab", { name: /3\.1\.1b/ }));
    await screen.findByText(/No processes recorded/);
    expect(screen.queryByRole("button", { name: "+ Add process" })).toBeNull();
  });
});
