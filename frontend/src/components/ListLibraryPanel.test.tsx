// @vitest-environment jsdom
//
// Library › Lists (L.2): one list serving several practices reads as one
// list, the merged device list says what it merged, a read-only role sees
// disabled controls rather than a 403 after a click, and a save that lost a
// race keeps the second editor's work and tells them what happened.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, ListConflictError } from "../api";
import type {
  LibraryListDetail,
  LibraryListSummary,
  LibraryListVersion,
  ListEvidenceStatus,
  ListViewSummary,
} from "../types";
import { ListLibraryPanel } from "./ListLibraryPanel";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ListConflictError: actual.ListConflictError,
    ApiError: actual.ApiError,
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
      getListTagEvents: vi.fn().mockResolvedValue([]),
      addListTag: vi.fn(),
      removeListTag: vi.fn(),
      createLibraryList: vi.fn(),
      adoptTemplates: vi.fn(),
      takeTemplateVersion: vi.fn(),
      getOrgs: vi.fn(),
      listEvidenceStatus: vi.fn(),
    },
  };
});

// L.8: History sits under the rows, collapsed, its size on the control.
function historyDetails(summary: HTMLElement): HTMLDetailsElement {
  const details = summary.closest("details");
  if (!details) throw new Error("History is not a <details>");
  expect(summary.textContent).toMatch(/^History: versions and practices \(\d+\)$/);
  expect(details.open).toBe(false);
  fireEvent.click(summary);
  details.open = true;
  return details;
}

async function openHistory(): Promise<HTMLDetailsElement> {
  return historyDetails(await screen.findByText(/^History: versions and practices/));
}

function openHistorySync(): HTMLDetailsElement {
  return historyDetails(screen.getByText(/^History: versions and practices/));
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  // clearAllMocks keeps implementations; a test's tag history must not leak.
  vi.mocked(api.getListTagEvents).mockResolvedValue([]);
});

const WHEN = "2026-10-10T09:10:00Z";

function summary(over: Partial<LibraryListSummary>): LibraryListSummary {
  return {
    id: "x", list_key: "x", title: "x", is_template: true, source_ref: null, projection_view_id: null,
    control_keys: [], version_number: 1, column_count: 2, row_count: 1, updated_at: WHEN, saved_by: null,
    cause: "first_import", merged_from: [], has_changelog: false, example_rows: 0, adopted_from: null,
    adopted_copies: 0, ...over,
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
    saved_by: null, cause: "first_import", example_rows: 0, ...over,
  };
}

function detail(current: LibraryListVersion, over: Partial<LibraryListDetail> = {}): LibraryListDetail {
  return {
    id: "risk", list_key: "3-11-1a-risk-register", is_template: true, source_ref: "RA/3.11.1/Risk Register.xlsx#3.11.1a",
    projection_view_id: null, control_keys: ["RA.L2-3.11.1"], merge: null, current, adopted_from: null,
    distribution: [], own_copy_id: null, ...over,
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

    const history = await openHistory();
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

describe("the List Builder (L.2.2)", () => {
  it("is offered only to a role that may build, and opens the new list once created", async () => {
    mockIndex();
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    await screen.findByText("Separation of Duties");
    expect(screen.queryByRole("button", { name: "+ Add list" })).toBeNull();
    cleanup();

    mockIndex();
    const v1 = version(1, [], {
      title: "Visitor Log", columns: ["Date", "Visitor"], cause: "created_by_hand", saved_by: "Pat Builder",
      created_at: "2026-10-15T14:02:00Z",
      provenance: { via: "api", cause: "created_by_hand", saved_by: "Pat Builder", origin_note: "Old SharePoint sheet" },
    });
    const built = detail(v1, { id: "vis", list_key: "visitor-log", is_template: false, source_ref: null, control_keys: ["PE.L2-3.10.3"] });
    vi.mocked(api.createLibraryList).mockResolvedValue(built);
    vi.mocked(api.getLibraryList).mockResolvedValue(built);
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([v1]);
    render(<ListLibraryPanel orgId="org" canWrite={true} canDefine={true} />);
    fireEvent.click(await screen.findByRole("button", { name: "+ Add list" }));

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Visitor Log" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Practice to add" }), { target: { value: "3.10.3" } });
    fireEvent.click(screen.getByRole("button", { name: "Add practice" }));
    fireEvent.change(screen.getByLabelText("What goes here"), { target: { value: "Escorted visitors" } });
    fireEvent.change(screen.getByLabelText("Responsible"), { target: { value: "Office Manager" } });
    fireEvent.change(screen.getByLabelText("Review cadence"), { target: { value: "Monthly" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Column 1" }), { target: { value: "Date" } });
    fireEvent.click(screen.getByRole("button", { name: "+ Add column" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Column 2" }), { target: { value: "Visitor" } });
    fireEvent.change(screen.getByLabelText(/Where it came from/), { target: { value: "Old SharePoint sheet" } });
    fireEvent.click(screen.getByRole("button", { name: "Create list (version 1)" }));

    await waitFor(() =>
      expect(api.createLibraryList).toHaveBeenCalledWith("org", {
        title: "Visitor Log", control_keys: ["3.10.3"], description: "Escorted visitors",
        responsible: "Office Manager", review_cadence: "Monthly", columns: ["Date", "Visitor"],
        origin_note: "Old SharePoint sheet",
      }),
    );
    const history = await openHistory();
    await waitFor(() =>
      expect(within(history).getByText(/Written by Pat Builder, from nothing: built by hand in WinGRC on 2026-10-15/)).toBeTruthy(),
    );
    // A person's note, kept apart from any import source.
    expect(screen.queryByText("Imported from")).toBeNull();
    expect(screen.getByText("Where it came from")).toBeTruthy();
    expect(screen.getByText(/a note by Pat Builder, not an import source/)).toBeTruthy();
  });

  it("shows the server's refusal, and keeps the form, when a practice is not a requirement", async () => {
    mockIndex();
    vi.mocked(api.createLibraryList).mockRejectedValue(new ApiError("'3.1.23' is not a NIST SP 800-171 Rev 2 requirement", 422));
    render(<ListLibraryPanel orgId="org" canWrite={true} canDefine={true} />);
    fireEvent.click(await screen.findByRole("button", { name: "+ Add list" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "X" } });
    fireEvent.change(screen.getByRole("textbox", { name: "Practice to add" }), { target: { value: "3.1.23" } });
    fireEvent.click(screen.getByRole("button", { name: "Add practice" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Column 1" }), { target: { value: "A" } });
    fireEvent.click(screen.getByRole("button", { name: "Create list (version 1)" }));
    expect(await screen.findByText(/not a NIST SP 800-171 Rev 2 requirement/)).toBeTruthy();
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("X");
  });
});

describe("tags (L.2.2)", () => {
  const v1 = version(1, [["R-1", "Open"]]);
  const tagged = (keys: string[]) => detail(v1, { control_keys: keys });

  async function openRisk(keys: string[], canWrite = true, canDefine = canWrite) {
    mockIndex();
    vi.mocked(api.getLibraryList).mockResolvedValue(tagged(keys));
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([v1]);
    render(<ListLibraryPanel orgId="org" canWrite={canWrite} canDefine={canDefine} />);
    await screen.findByText("Separation of Duties");
    fireEvent.click(screen.getByRole("button", { name: "Risk Register" }));
    return screen.findByRole("region", { name: "Practices this list serves" });
  }

  it("adds a practice, refreshes the list and the index, and shows who did it in the history", async () => {
    const serves = await openRisk(["RA.L2-3.11.1"]);
    vi.mocked(api.addListTag).mockResolvedValue({} as never);
    vi.mocked(api.getLibraryList).mockResolvedValue(tagged(["CM.L2-3.4.2", "RA.L2-3.11.1"]));
    vi.mocked(api.getListTagEvents).mockResolvedValue([
      { id: "t1", control_key: "CM.L2-3.4.2", change: "added", via: "api", saved_by: "Tess Tagger", at_version: 1, note: null, created_at: "2026-10-15T15:00:00Z" },
    ]);
    fireEvent.change(within(serves).getByRole("textbox", { name: "Practice to tag" }), { target: { value: "3.4.2" } });
    fireEvent.click(within(serves).getByRole("button", { name: "Add tag" }));
    await waitFor(() => expect(api.addListTag).toHaveBeenCalledWith("org", "risk", "3.4.2"));
    await waitFor(() => expect(api.listLibrary).toHaveBeenCalledTimes(2));
    const history = await openHistory();
    await waitFor(() =>
      expect(within(history).getByText(/Tagged CM\.L2-3\.4\.2: this list became a candidate for it\. By Tess Tagger\./)).toBeTruthy(),
    );
  });

  it("asks before removing a practice, and shows the evidence that blocks it", async () => {
    await openRisk(["CM.L2-3.4.1", "RA.L2-3.11.1"]);
    fireEvent.click(screen.getByRole("button", { name: "Remove CM.L2-3.4.1" }));
    expect(api.removeListTag).not.toHaveBeenCalled();
    expect(screen.getByText(/refused while evidence rests on this list/)).toBeTruthy();
    vi.mocked(api.removeListTag).mockRejectedValue(
      new ApiError("CM.L2-3.4.1 cannot be removed from 3-11-1a-risk-register: evidence rests on it (Evidence 'Q3 export' in assessment 'FY26'). Unlink that evidence first.", 409),
    );
    fireEvent.click(screen.getByRole("button", { name: "Yes, remove CM.L2-3.4.1" }));
    expect(await screen.findByText(/Evidence 'Q3 export' in assessment 'FY26'/)).toBeTruthy();
  });

  it("offers no removal of the last practice, and no tag controls to a read-only role", async () => {
    await openRisk(["RA.L2-3.11.1"]);
    expect(screen.queryByRole("button", { name: "Remove RA.L2-3.11.1" })).toBeNull();
    cleanup();
    await openRisk(["CM.L2-3.4.1", "RA.L2-3.11.1"], false);
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Practice to tag" })).toBeNull();
  });

  it("offers no tag controls to a customer contact, who fills lists in", async () => {
    await openRisk(["CM.L2-3.4.1", "RA.L2-3.11.1"], true, false);
    expect(screen.queryByRole("button", { name: /^Remove / })).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Practice to tag" })).toBeNull();
  });
});

describe("column edits (L.2.2)", () => {
  const v1 = version(1, [["R-1", "Open"], ["R-2", ""]]);

  it("says what removing a column loses before saving, and saves the new columns", async () => {
    mockIndex();
    vi.mocked(api.getLibraryList).mockResolvedValue(detail(v1));
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([v1]);
    render(<ListLibraryPanel orgId="org" canWrite={true} canDefine={true} />);
    await screen.findByText("Separation of Duties");
    fireEvent.click(screen.getByRole("button", { name: "Risk Register" }));
    fireEvent.click(await screen.findByRole("button", { name: "Edit rows" }));

    fireEvent.click(screen.getByRole("button", { name: "Remove column Status" }));
    expect(screen.getByLabelText("Column changes").textContent).toMatch(
      /Saving removes the column Status \(1 filled cell\) from version 2: 1 filled cell will not be in it\. Version 1 keeps it, with every value\./,
    );
    expect(api.createLibraryListVersion).not.toHaveBeenCalled();
    vi.mocked(api.createLibraryListVersion).mockResolvedValue(version(2, [["R-1"], ["R-2"]]));
    fireEvent.click(screen.getByRole("button", { name: "Save as version 2, removing 1 filled cell" }));
    await waitFor(() =>
      expect(api.createLibraryListVersion).toHaveBeenCalledWith("org", "risk", [["R-1"], ["R-2"]], "v1", "", ["Risk ID"]),
    );
  });

  it("lets a customer contact edit rows but not columns", async () => {
    mockIndex();
    vi.mocked(api.getLibraryList).mockResolvedValue(detail(v1));
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([v1]);
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    await screen.findByText("Separation of Duties");
    expect(screen.queryByRole("button", { name: "+ Add list" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Risk Register" }));
    fireEvent.click(await screen.findByRole("button", { name: "Edit rows" }));
    expect(screen.getByRole("button", { name: "+ Add row" })).toBeTruthy();
    expect(screen.getByRole("textbox", { name: "Status, row 1" })).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Column 1 name" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove column/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "+ Add column" })).toBeNull();
  });

  it("says a rename keeps its cells", async () => {
    mockIndex();
    vi.mocked(api.getLibraryList).mockResolvedValue(detail(v1));
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([v1]);
    render(<ListLibraryPanel orgId="org" canWrite={true} canDefine={true} />);
    await screen.findByText("Separation of Duties");
    fireEvent.click(screen.getByRole("button", { name: "Risk Register" }));
    fireEvent.click(await screen.findByRole("button", { name: "Edit rows" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Column 2 name" }), { target: { value: "State" } });
    expect(screen.getByLabelText("Column changes").textContent).toMatch(/Renamed: Status → State\. A renamed column keeps its cells\./);
    expect(screen.getByRole("button", { name: "Save as version 2" })).toBeTruthy();
  });
});

describe("adopting templates (L.7)", () => {
  const ADOPTED = {
    template_list_id: "risk", template_org_id: "msp", same_org: false, template_title: "Risk Register",
    template_key: "3-11-1a-risk-register", based_on_version_number: 1, head_version_number: 2, update_available: true,
  };
  const ex = [["[PLACEHOLDER]", "Open"], ["R-2", "[PLACEHOLDER]"]];

  async function openCopy(canDefine: boolean) {
    const v1 = version(1, ex, { cause: "adopted_from_template", example_rows: 2, saved_by: "Ada Adopter",
      provenance: { via: "api", cause: "adopted_from_template", saved_by: "Ada Adopter", template_title: "Risk Register",
        template_version_number: 1 } });
    vi.mocked(api.listLibrary).mockResolvedValue([
      summary({ id: "copy", title: "Risk Register", is_template: false, control_keys: ["RA.L2-3.11.1"],
        adopted_from: ADOPTED, example_rows: 2, cause: "adopted_from_template" }),
    ]);
    vi.mocked(api.listViews).mockResolvedValue([]);
    vi.mocked(api.getListImportReport).mockResolvedValue({ recorded_at: null, not_imported: [], not_ingested: [] });
    vi.mocked(api.getLibraryList).mockResolvedValue(
      detail(v1, { id: "copy", is_template: false, source_ref: null, adopted_from: ADOPTED }),
    );
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([v1]);
    render(<ListLibraryPanel orgId="org" canWrite={true} canDefine={canDefine} />);
    expect(await screen.findByText("from template v1")).toBeTruthy();
    expect(screen.getByText("template v2 available")).toBeTruthy();
    expect(screen.getByText("2 example rows to fill in")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Risk Register" }));
    return screen.findByRole("region", { name: "Where this copy came from" });
  }

  it("says where a copy came from, that it is unfinished, and that a newer template version waits", async () => {
    const where = await openCopy(true);
    expect(where.textContent).toMatch(/Copied from the template Risk Register, version 1\./);
    // L.8: the update is actionable, so it sits above the rows; where the copy came from is reference, below them.
    const update = screen.getByRole("region", { name: "Newer template version" });
    expect(update.textContent).toMatch(/Template version 2 is available\. This list stays on version 1 until someone takes it\./);
    const rows = screen.getByRole("region", { name: /: 2 rows, \d+ columns$/ });
    expect(update.compareDocumentPosition(rows) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(rows.compareDocumentPosition(where) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText(/All 2 rows are still the template's examples, with a fill-in/)).toBeTruthy();
    expect(within(openHistorySync()).getByText(
      /Copied by Ada Adopter from the template Risk Register at version 1 on 2026-10-10\./,
    )).toBeTruthy();
  });

  it("shows what taking the newer version changes, and names the edit it replaces before it does", async () => {
    await openCopy(true);
    vi.mocked(api.takeTemplateVersion).mockResolvedValueOnce({
      from_template_version: 1, to_template_version: 2, rows_from: "copy", changed_fields: ["columns", "rows"],
      columns_added: ["Owner"], columns_removed: [], dropped_cells: 0, person_changed: ["columns"],
      edit_ref: "3-11-1a-risk-register-own@v3", tag_add: [], tag_remove: [], applied: false, version_number: null,
    });
    fireEvent.click(screen.getByRole("button", { name: "Review template version 2" }));
    expect(await screen.findByText(/This org's rows are kept, matched to the template's columns by name\. Columns added: Owner\./)).toBeTruthy();
    expect(screen.getByText(/It replaces an edit made here/)).toBeTruthy();
    expect(api.takeTemplateVersion).toHaveBeenLastCalledWith("org", "copy", false, undefined);
    vi.mocked(api.takeTemplateVersion).mockResolvedValueOnce({} as never);
    fireEvent.click(screen.getByRole("button", { name: "Take version 2, replacing the edit in version 3" }));
    await waitFor(() =>
      expect(api.takeTemplateVersion).toHaveBeenLastCalledWith("org", "copy", true, "3-11-1a-risk-register-own@v3"),
    );
  });

  it("tells a customer contact the update exists without offering to take it", async () => {
    await openCopy(false);
    const update = screen.getByRole("region", { name: "Newer template version" });
    expect(within(update).queryByRole("button", { name: /Review template version/ })).toBeNull();
    expect(update.textContent).toMatch(/Someone who defines lists here and in the template's org takes it\./);
  });

  it("shows a template's copies by version, and copies it into this org when it is its own tenant", async () => {
    mockIndex();
    const v1 = version(1, ex);
    vi.mocked(api.getLibraryList).mockResolvedValue(
      detail(v1, { distribution: [{ version_number: 1, copies: 2, this_org: false }, { version_number: 2, copies: 1, this_org: false }] }),
    );
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([v1]);
    render(<ListLibraryPanel orgId="org" canWrite={true} canDefine={true} />);
    await screen.findByText("Separation of Duties");
    fireEvent.click(screen.getByRole("button", { name: "Risk Register" }));
    const copies = await screen.findByRole("region", { name: "Copies of this template" });
    expect(copies.textContent).toMatch(/Adopted by 3 orgs: 2 at version 1, 1 at version 2\./);
    vi.mocked(api.adoptTemplates).mockResolvedValue({
      source_org_id: "org", same_org: true, applied: true,
      items: [{ template_id: "risk", template_title: "Risk Register", template_version_number: 1, control_keys: [],
        row_count: 2, example_rows: 2, action: "create", list_id: "copy", list_key: "risk-own" }],
    });
    fireEvent.click(within(copies).getByRole("button", { name: "Copy into this org's lists" }));
    await waitFor(() => expect(api.adoptTemplates).toHaveBeenCalledWith("org", "org", ["risk"], true));
  });

  it("shows the set before writing it, each template its own copy", async () => {
    mockIndex();
    vi.mocked(api.getOrgs).mockResolvedValue([
      { id: "org", name: "Winsorlabs", created_at: WHEN },
      { id: "acme", name: "Acme", created_at: WHEN },
    ]);
    render(<ListLibraryPanel orgId="org" canWrite={true} canDefine={true} />);
    await screen.findByText("Separation of Duties");
    fireEvent.click(screen.getByRole("button", { name: "Adopt templates" }));
    expect(await screen.findByText(/These are this org's own templates\./)).toBeTruthy();
    const choices = screen.getByRole("list", { name: "Templates" });
    fireEvent.click(within(choices).getByRole("checkbox", { name: /Risk Register/ }));
    fireEvent.click(within(choices).getByRole("checkbox", { name: /Separation of Duties/ }));
    vi.mocked(api.adoptTemplates).mockResolvedValueOnce({
      source_org_id: "org", same_org: true, applied: false,
      items: [
        { template_id: "risk", template_title: "Risk Register", template_version_number: 1, control_keys: ["RA.L2-3.11.1"],
          row_count: 2, example_rows: 2, action: "create", list_id: null, list_key: "risk-own" },
        { template_id: "sod", template_title: "Separation of Duties", template_version_number: 1, control_keys: [],
          row_count: 1, example_rows: 1, action: "already_adopted", list_id: "sod-own", list_key: "sod-own" },
      ],
    });
    fireEvent.click(screen.getByRole("button", { name: "Review 2 templates" }));
    const set = await screen.findByRole("region", { name: "What adopting will write" });
    expect(api.adoptTemplates).toHaveBeenLastCalledWith("org", "org", ["risk", "sod"], false);
    expect(set.textContent).toMatch(/Will copy 1 into this org's lists/);
    expect(set.textContent).toMatch(/Already in this org's lists — not copied again/);
    vi.mocked(api.adoptTemplates).mockResolvedValueOnce({} as never);
    fireEvent.click(within(set).getByRole("button", { name: "Copy 1 into this org's lists" }));
    await waitFor(() => expect(api.adoptTemplates).toHaveBeenLastCalledWith("org", "org", ["risk", "sod"], true));
  });

  it("offers no adoption to a customer contact", async () => {
    mockIndex();
    render(<ListLibraryPanel orgId="org" canWrite={true} />);
    await screen.findByText("Separation of Duties");
    expect(screen.queryByRole("button", { name: "Adopt templates" })).toBeNull();
  });
});

describe("as evidence (L.4)", () => {
  const filled = version(2, [["R-1", "Open"]]);

  async function openOwn(status: ListEvidenceStatus) {
    mockIndex();
    vi.mocked(api.getLibraryList).mockResolvedValue(detail(filled, { is_template: false }));
    vi.mocked(api.getLibraryListVersions).mockResolvedValue([filled]);
    vi.mocked(api.listEvidenceStatus).mockResolvedValue(status);
    render(<ListLibraryPanel orgId="org" canWrite />);
    await screen.findByText("Separation of Duties");
    fireEvent.click(screen.getByRole("button", { name: "Risk Register" }));
    return screen.findByRole("region", { name: "As evidence" });
  }

  it("says why a list is not offered, rather than leaving it out", async () => {
    const section = await openOwn({
      offered: false, not_offered: "still showing template examples (2 of 2 rows)", links: [],
    });
    expect(section.textContent).toContain("Not offered as evidence: still showing template examples (2 of 2 rows).");
  });

  it("names where it is evidence, and says the drift in L.7's words", async () => {
    const drift = "List version 2 is available. This evidence stays on version 1 until someone moves it forward.";
    const section = await openOwn({
      offered: true, not_offered: null,
      links: [{
        list_id: "risk", list_key: "3-11-1a-risk-register", title: "Risk Register", current_version_number: 2,
        linked_version_id: "v1", linked_version_number: 1, assessment_id: "a", assessment_name: "FY26 L2",
        control_state_id: "cs", control_key: "RA.L2-3.11.1", objective_key: "a", drift,
      }],
    });
    expect(section.textContent).toContain("FY26 L2: RA.L2-3.11.1[a], version 1");
    expect(section.textContent).toContain(drift);
  });
});
