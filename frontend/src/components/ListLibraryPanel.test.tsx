// @vitest-environment jsdom
//
// Library › Lists (L.2): one list serving several practices reads as one
// list, the merged device list says what it merged, a read-only role sees
// disabled controls rather than a 403 after a click, and a save that lost a
// race keeps the second editor's work and tells them what happened.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ListConflictError } from "../api";
import type { LibraryListDetail, LibraryListSummary, LibraryListVersion, ListViewSummary } from "../types";
import { ListLibraryPanel } from "./ListLibraryPanel";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ListConflictError: actual.ListConflictError,
    api: {
      listLibrary: vi.fn(),
      listViews: vi.fn(),
      getListView: vi.fn(),
      getListImportReport: vi.fn(),
      getLibraryList: vi.fn(),
      getLibraryListVersions: vi.fn(),
      createLibraryListVersion: vi.fn(),
      exportListView: vi.fn(),
      exportLibraryList: vi.fn(),
    },
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const WHEN = "2026-10-10T09:10:00Z";

function summary(over: Partial<LibraryListSummary>): LibraryListSummary {
  return {
    id: "x", list_key: "x", title: "x", is_template: true, source_ref: null, projection_view_id: null,
    control_keys: [], version_number: 1, column_count: 2, row_count: 1, updated_at: WHEN, saved_by: null,
    cause: "first_import", merged_from: [], has_changelog: false, ...over,
  };
}

const SOD = summary({
  id: "sod", title: "Separation of Duties", control_keys: ["AC.L2-3.1.4", "AU.L2-3.3.9", "CM.L2-3.4.5"],
});
const DEVICES = summary({
  id: "dev", title: "3.1.1c Authorized Devices", control_keys: ["AC.L2-3.1.1", "CM.L2-3.4.1"],
  projection_view_id: "3.1.1c-authorized-devices",
  merged_from: ["CM/3.4.1/Hardware and Software Asset Inventory.xlsx#3.4.1a Hardware"],
});
const RISK = summary({ id: "risk", title: "Risk Register", control_keys: ["RA.L2-3.11.1"], has_changelog: true });

const VIEW: ListViewSummary = {
  id: "3.1.1c-authorized-devices", sheet_title: "3.1.1c Authorized Devices", title: "Authorized Devices",
  control_ids: ["AC.L2-3.1.1"], description: "", entity_type: "device", row_count: 4, excluded_out_of_boundary: 0,
};

function version(n: number, rows: string[][], over: Partial<LibraryListVersion> = {}): LibraryListVersion {
  return {
    id: `v${n}`, version_number: n, title: "Risk Register", description: "Identified risks", responsible: null,
    review_cadence: "Annually", columns: ["Risk ID", "Status"], rows, provenance: {}, created_at: WHEN,
    saved_by: null, cause: "first_import", ...over,
  };
}

function detail(current: LibraryListVersion, over: Partial<LibraryListDetail> = {}): LibraryListDetail {
  return {
    id: "risk", list_key: "3-11-1a-risk-register", is_template: true, source_ref: "RA/3.11.1/Risk Register.xlsx#3.11.1a",
    projection_view_id: null, control_keys: ["RA.L2-3.11.1"], merge: null, current, ...over,
  };
}

function mockIndex() {
  vi.mocked(api.listLibrary).mockResolvedValue([SOD, DEVICES, RISK]);
  vi.mocked(api.listViews).mockResolvedValue([VIEW]);
  vi.mocked(api.getListImportReport).mockResolvedValue({
    recorded_at: WHEN,
    not_imported: [
      { source_ref: "SC/3.13.1/User.xlsx#3.13.1", status: "needs_normalization", reason: "wide repeat" },
      { source_ref: "MA/CMMC_372_Controls.xlsx#*", status: "non_conforming", reason: "no header block" },
      { source_ref: "CM/3.4.1/Hardware.xlsx#3.4.1a Hardware", status: "merged", reason: "merged into 3.1.1c" },
    ],
    not_ingested: ["Lists/AC/3.1.1.xlsx"],
  });
}

const titles = () =>
  within(screen.getByRole("table")).queryAllByRole("button", { name: /Separation|Devices|Risk/ }).map((b) => b.textContent);

describe("ListLibraryPanel index", () => {
  it("shows a three-practice list once overall and once in each of its families", async () => {
    mockIndex();
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    await screen.findByText("Separation of Duties");
    expect(titles().filter((t) => t === "Separation of Duties")).toHaveLength(1);

    for (const fam of ["AC", "AU", "CM"]) {
      fireEvent.click(screen.getByRole("button", { name: new RegExp(`^${fam} \\(`) }));
      expect(titles().filter((t) => t === "Separation of Duties")).toHaveLength(1);
      fireEvent.click(screen.getByRole("button", { name: /^All \(/ }));
    }
    fireEvent.click(screen.getByRole("button", { name: /^RA \(/ }));
    expect(titles()).toEqual(["Risk Register"]);
  });

  it("keeps live lists, this org's own lists and templates in separate sections", async () => {
    const KEPT = summary({ id: "kept", title: "Kept Risk Register", is_template: false, control_keys: ["RA.L2-3.11.1"] });
    mockIndex();
    vi.mocked(api.listLibrary).mockResolvedValue([SOD, DEVICES, RISK, KEPT]);
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    await screen.findByText("Separation of Duties");
    const section = (name: RegExp) => {
      const head = screen.getByRole("rowheader", { name });
      return head.closest("tbody") as HTMLElement;
    };
    const names = (el: HTMLElement) =>
      within(el).queryAllByRole("button", { name: /Separation|Devices|Risk/ }).map((b) => b.textContent);
    // Same title, different kinds: the live inventory and the template never share a section.
    expect(names(section(/^Live from scope \(1\)/))).toEqual(["3.1.1c Authorized Devices"]);
    expect(names(section(/^Templates \(3\)/))).toEqual([
      "3.1.1c Authorized Devices", "Separation of Duties", "Risk Register",
    ]);
    expect(names(section(/^This org's lists \(1\)/))).toEqual(["Kept Risk Register"]);
    expect(within(section(/^Templates/)).getByText(/illustrative examples/)).toBeTruthy();
    expect(within(section(/^Live from scope/)).getByText(/Real records/)).toBeTruthy();
  });

  it("keeps a place for this org's own lists when it has none yet", async () => {
    mockIndex();
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    const head = await screen.findByRole("rowheader", { name: /^This org's lists \(0\)/ });
    expect(head.textContent).toMatch(/copied from a template/);
  });

  it("shows a merged template's practices in the index, not only on its page", async () => {
    mockIndex();
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    await screen.findByText("Separation of Duties");
    const row = screen
      .getAllByRole("button", { name: "3.1.1c Authorized Devices" })
      .map((b) => b.closest("tr") as HTMLElement)
      .find((tr) => within(tr).queryByText(/merged:/))!;
    expect(within(row).getByRole("button", { name: "AC.L2-3.1.1" })).toBeTruthy();
    expect(within(row).getByRole("button", { name: "CM.L2-3.4.1" })).toBeTruthy();
  });

  it("says what was never imported, and does not count a merge as missing", async () => {
    mockIndex();
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    const summaryLine = await screen.findByText(/Not in this library/);
    expect(summaryLine.textContent).toMatch(/2 template sheets not imported/);
    expect(summaryLine.textContent).toMatch(/1 archived file deliberately not ingested/);
  });

  it("filters by a practice chip", async () => {
    mockIndex();
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    await screen.findByText("Separation of Duties");
    fireEvent.click(screen.getAllByRole("button", { name: "CM.L2-3.4.1" })[0]);
    expect(titles()).toEqual(["3.1.1c Authorized Devices"]);
  });
});

describe("the merged device list", () => {
  it("reads as one list serving both practices, with its column mapping", async () => {
    mockIndex();
    vi.mocked(api.getLibraryList).mockResolvedValue(
      detail(version(1, [["Laptop", "Workstation"]], { title: "3.1.1c Authorized Devices", columns: ["Name", "Device Type"] }), {
        id: "dev", control_keys: ["AC.L2-3.1.1", "CM.L2-3.4.1"], projection_view_id: "3.1.1c-authorized-devices",
        merge: {
          target_ref: "AC/3.1.1/Authorized-Entities.xlsx#3.1.1c Authorized Devices",
          absorbed_ref: "CM/3.4.1/Hardware and Software Asset Inventory.xlsx#3.4.1a Hardware",
          columns: [
            { source_column: "Asset Name", target_column: "Name", added: false },
            { source_column: "Type", target_column: "Device Type", added: true },
          ],
        },
      }),
    );
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([]);
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    await screen.findByText("Separation of Duties");
    const templates = screen.getByRole("rowheader", { name: /^Templates/ }).closest("tbody") as HTMLElement;
    fireEvent.click(within(templates).getByRole("button", { name: "3.1.1c Authorized Devices" }));

    const serves = await screen.findByRole("region", { name: "Practices this list serves" });
    expect(serves.textContent).toMatch(/One list serving 2 practices in 2 families/);
    const merge = screen.getByRole("region", { name: "How this list was merged" });
    expect(merge.textContent).toMatch(/3\.4\.1a Hardware/);
    expect(within(merge).getByText("Device Type").closest("td")?.textContent).toMatch(/added by the merge/);
    // Why the added column is empty, said rather than filled: the examples are the target sheet's.
    expect(merge.textContent).toMatch(/example rows come from the 3\.1\.1c Authorized Devices sheet/);
    expect(merge.textContent).toMatch(/Device Type is empty in every one of them/);
    expect(screen.getByText(/drawn from Scope › Assets/)).toBeTruthy();
  });
});

describe("editing", () => {
  const v1 = version(1, [["R-1", "Open"], ["R-2", "Open"]]);

  async function openRisk(canWrite: boolean) {
    mockIndex();
    vi.mocked(api.getLibraryList).mockResolvedValue(detail(v1));
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([v1]);
    render(<ListLibraryPanel orgId="org" canWrite={canWrite} />);
    await screen.findByText("Separation of Duties");
    fireEvent.click(screen.getByRole("button", { name: "Risk Register" }));
    return screen.findByRole("button", { name: "Edit rows" });
  }

  it("lets a read-only role export the version it is looking at", async () => {
    const edit = await openRisk(false);
    expect((edit as HTMLButtonElement).disabled).toBe(true);
    vi.mocked(api.exportLibraryList).mockResolvedValue(undefined);
    const exportButton = screen.getByRole("button", { name: "Export v1 (.xlsx)" }) as HTMLButtonElement;
    expect(exportButton.disabled).toBe(false);
    fireEvent.click(exportButton);
    await waitFor(() => expect(api.exportLibraryList).toHaveBeenCalledWith("org", "risk", 1));
  });

  it("is a disabled control, with the reason, for a read-only role", async () => {
    const edit = await openRisk(false);
    expect((edit as HTMLButtonElement).disabled).toBe(true);
    expect(edit.getAttribute("title")).toMatch(/can read lists but not change them/);
    expect(screen.queryByRole("textbox", { name: /row 1/ })).toBeNull();
  });

  it("saves rows as a new version against the version it started from", async () => {
    fireEvent.click(await openRisk(true));
    fireEvent.change(screen.getByRole("textbox", { name: "Status, row 1" }), { target: { value: "Closed" } });
    fireEvent.click(screen.getByRole("button", { name: "Remove row 2" }));
    vi.mocked(api.createLibraryListVersion).mockResolvedValue(version(2, [["R-1", "Closed"]]));
    fireEvent.click(screen.getByRole("button", { name: "Save as version 2" }));
    await waitFor(() =>
      expect(api.createLibraryListVersion).toHaveBeenCalledWith("org", "risk", [["R-1", "Closed"]], "v1", ""),
    );
  });

  it("keeps the second editor's work on a stale save and re-applies it onto the newer version", async () => {
    fireEvent.click(await openRisk(true));
    fireEvent.change(screen.getByRole("textbox", { name: "Status, row 1" }), { target: { value: "Closed" } });

    const v2 = version(2, [["R-1", "Open"], ["R-2", "Open"], ["R-3", "Open"]], { saved_by: "Ada Admin", cause: "edit" });
    vi.mocked(api.createLibraryListVersion).mockRejectedValueOnce(
      new ListConflictError({
        message: "This list changed while you were editing.",
        base_version_id: "v1", current_version_id: "v2", current_version_number: 2,
        saved_by: "Ada Admin", saved_at: WHEN,
      }),
    );
    vi.mocked(api.getLibraryList).mockResolvedValue(detail(v2));
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([v1, v2]);
    fireEvent.click(screen.getByRole("button", { name: "Save as version 2" }));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toMatch(/Version 2 was saved by Ada Admin/);
    expect(alert.textContent).toMatch(/Your edit was not saved, and it is still here/);
    expect(alert.textContent).toMatch(/1 change of yours can be re-applied/);
    // Still the editor's own draft.
    expect((screen.getByRole("textbox", { name: "Status, row 1" }) as HTMLInputElement).value).toBe("Closed");
    // Saving again from the stale base could only conflict again.
    expect((screen.getByRole("button", { name: "Save as version 2" }) as HTMLButtonElement).disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Re-apply my changes onto version 2" }));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText(/now on top of version 2/)).toBeTruthy();

    vi.mocked(api.createLibraryListVersion).mockResolvedValue(version(3, []));
    fireEvent.click(screen.getByRole("button", { name: "Save as version 3" }));
    await waitFor(() =>
      expect(api.createLibraryListVersion).toHaveBeenLastCalledWith(
        "org", "risk", [["R-1", "Closed"], ["R-2", "Open"], ["R-3", "Open"]], "v2", "",
      ),
    );
  });
});

describe("version history", () => {
  // Importer-caused versions recur; the history must say the product wrote
  // one, where a reader looks first -- not only in the audit log.
  it("tells an importer change from a person's edit, without opening the audit log", async () => {
    const v1 = version(1, [["R-1", "Open"]], {
      provenance: { via: "template_import", source_file: "New Lists/RA/3.11.1/Risk Register.xlsx", source_sheet: "3.11.1a" },
    });
    const v2 = version(2, [["R-1", "Closed"]], {
      cause: "edit", saved_by: "Ada Admin", provenance: { via: "api", based_on_version: 1, saved_by: "Ada Admin" },
    });
    const v3 = version(3, [["R-1", "Closed"]], {
      title: "Risk Register & Treatment",
      cause: "importer_changed",
      provenance: {
        via: "template_import", cause: "importer_changed", previous_import_version: 1,
        changed_fields: ["title"],
      },
    });
    mockIndex();
    vi.mocked(api.listLibrary).mockResolvedValue([SOD, DEVICES, { ...RISK, cause: "importer_changed", version_number: 3 }]);
    vi.mocked(api.getLibraryList).mockResolvedValue(detail(v3));
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([v1, v2, v3]);
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    await screen.findByText("Separation of Duties");
    // The index already says the product wrote the current version.
    expect(screen.getByRole("row", { name: /Risk Register/ }).textContent).toMatch(/importer changed/);
    fireEvent.click(screen.getByRole("button", { name: "Risk Register" }));

    const history = await screen.findByRole("region", { name: "Version history" });
    await waitFor(() => expect(within(history).getAllByRole("listitem")).toHaveLength(3));
    const [third, second, first] = within(history).getAllByRole("listitem").map((li) => li.textContent);
    expect(third).toMatch(/importer changed.*WinGRC importer/);
    expect(third).toMatch(/not by a person: the importer changed how it reads the source, and the source sheet is unchanged since version 1\. Changed: title\./);
    expect(second).toMatch(/edited.*Ada Admin/);
    expect(second).toMatch(/Saved by Ada Admin, from version 1\./);
    expect(first).toMatch(/imported.*Imported from New Lists\/RA\/3\.11\.1\/Risk Register\.xlsx \(sheet 3\.11\.1a\)/);
    expect(screen.getByText(/importer changed by WinGRC importer on/)).toBeTruthy();
  });
});
