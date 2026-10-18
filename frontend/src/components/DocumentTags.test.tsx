// @vitest-environment jsdom
//
// Item X.1: a document is tagged from a screen, and removing a tag says what
// the server computed -- once, in the document's title.

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, TagRemovalRefusedError } from "../api";
import type { DocumentDetail, TaggedObjective } from "../types";
import { DocumentTags } from "./DocumentTags";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ApiError: actual.ApiError,
    TagRemovalRefusedError: actual.TagRemovalRefusedError,
    api: {
      addDocumentObjectiveTag: vi.fn(),
      checkDocumentTagRemoval: vi.fn(),
      removeDocumentObjectiveTag: vi.fn(),
    },
  };
});

const A: TaggedObjective = { objective_id: "oa", objective_key: "AC.L2-3.1.1[a]", text: "authorized users are identified" };
const B: TaggedObjective = { objective_id: "ob", objective_key: "AC.L2-3.1.1[b]", text: "processes acting for users are identified" };
const LINK = { version_number: 1, objective_key: "AC.L2-3.1.1[a]", assessment_name: "FY26" };

function doc(tags: TaggedObjective[]): DocumentDetail {
  return {
    id: "d1", doc_id: "AC-POL-001", doc_type: "policy", title: "Access Control Policy", cadence_months: 12,
    is_template_derived: false, template_ref: null, current_version: null,
    review: { status: "never_approved", last_approved_at: null, next_due_at: null, days_until_due: null, cadence_months: 12 },
    created_at: "2026-10-18T00:00:00Z", updated_at: "2026-10-18T00:00:00Z",
    versions: [], tagged_objective_ids: tags.map((t) => t.objective_id), tagged_objectives: tags,
  } as DocumentDetail;
}

function renderTags(tags: TaggedObjective[], canTag = true) {
  const onChanged = vi.fn();
  render(<DocumentTags orgId="org" doc={doc(tags)} canTag={canTag} onChanged={onChanged} />);
  return { region: screen.getByRole("region", { name: "Tagged objectives" }), onChanged };
}

beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

describe("DocumentTags (X.1)", () => {
  it("shows each tag by its key and text, and says when there are none", () => {
    const { region } = renderTags([A, B]);
    expect(within(region).getByText("AC.L2-3.1.1[a]")).toBeTruthy();
    expect(within(region).getByText(/processes acting for users/)).toBeTruthy();
    expect(within(region).getByRole("button", { name: "Remove AC.L2-3.1.1[b]" })).toBeTruthy();
  });

  it("says an untagged document is offered nowhere", () => {
    const { region } = renderTags([]);
    expect(within(region).getByText(/offered as evidence nowhere/)).toBeTruthy();
  });

  it("tags what was typed, and says each objective a practice key expanded to", async () => {
    const { region, onChanged } = renderTags([A]);
    vi.mocked(api.addDocumentObjectiveTag).mockResolvedValue({ document_id: "d1", added: [B], already_tagged: [A] });
    fireEvent.change(within(region).getByRole("textbox", { name: "Objective to tag" }), { target: { value: "3.1.1" } });
    fireEvent.click(within(region).getByRole("button", { name: "Add tag" }));
    await waitFor(() => expect(api.addDocumentObjectiveTag).toHaveBeenCalledWith("org", "d1", "3.1.1"));
    expect(await within(region).findByText("Tagged AC.L2-3.1.1[b]. Already tagged: AC.L2-3.1.1[a].")).toBeTruthy();
    expect(onChanged).toHaveBeenCalled();
  });

  it("names an unknown key rather than failing silently", async () => {
    const { region } = renderTags([]);
    vi.mocked(api.addDocumentObjectiveTag).mockRejectedValue(
      new ApiError("'3.1.1[z]': AC.L2-3.1.1[z] is not in the catalog", 422),
    );
    fireEvent.change(within(region).getByRole("textbox", { name: "Objective to tag" }), { target: { value: "3.1.1[z]" } });
    fireEvent.click(within(region).getByRole("button", { name: "Add tag" }));
    expect(await within(region).findByText(/AC\.L2-3\.1\.1\[z\] is not in the catalog/)).toBeTruthy();
  });

  it("names the evidence that blocks a removal, once, in the document's title, with no way through", async () => {
    const { region } = renderTags([A, B]);
    vi.mocked(api.checkDocumentTagRemoval).mockResolvedValue({ removable: false, reason: "evidence", links: [LINK] });
    fireEvent.click(within(region).getByRole("button", { name: "Remove AC.L2-3.1.1[a]" }));
    const dialog = within(region).getByRole("group", { name: "Remove AC.L2-3.1.1[a]" });
    expect(within(dialog).getByText(/Checking what rests on/)).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: /Yes, remove/ })).toBeNull();
    await within(dialog).findByText(/version 1, evidence for AC\.L2-3\.1\.1\[a\] in/);
    const text = dialog.textContent ?? "";
    expect(text).toContain("AC.L2-3.1.1[a] can't be removed from Access Control Policy while evidence rests on it");
    expect(text).not.toContain("AC-POL-001");
    expect(text.match(/can.t be removed|cannot be removed/g)).toHaveLength(1);
    expect(within(dialog).queryByRole("button", { name: /Yes, remove/ })).toBeNull();
    expect(within(dialog).queryByText(/No evidence rests/)).toBeNull();
    expect(api.checkDocumentTagRemoval).toHaveBeenCalledWith("org", "d1", "oa");
    expect(api.removeDocumentObjectiveTag).not.toHaveBeenCalled();
  });

  it("says nothing rests on a tag only when the server found nothing, and a race is refused the same way", async () => {
    const { region } = renderTags([A, B]);
    vi.mocked(api.checkDocumentTagRemoval).mockResolvedValue({ removable: true, reason: null, links: [] });
    fireEvent.click(within(region).getByRole("button", { name: "Remove AC.L2-3.1.1[a]" }));
    const dialog = within(region).getByRole("group", { name: "Remove AC.L2-3.1.1[a]" });
    expect(await within(dialog).findByText(/No evidence rests on Access Control Policy for AC\.L2-3\.1\.1\[a\]\./)).toBeTruthy();
    vi.mocked(api.removeDocumentObjectiveTag).mockRejectedValue(
      new TagRemovalRefusedError("log", { removable: false, reason: "evidence", links: [LINK] }),
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Yes, remove AC.L2-3.1.1[a]" }));
    expect(await within(dialog).findByText(/version 1, evidence for/)).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: /Yes, remove/ })).toBeNull();
    expect(within(dialog).queryByText(/No evidence rests/)).toBeNull();
  });

  it("says when it could not check, rather than that nothing rests on it", async () => {
    const { region } = renderTags([A]);
    vi.mocked(api.checkDocumentTagRemoval).mockRejectedValue(new Error("network"));
    fireEvent.click(within(region).getByRole("button", { name: "Remove AC.L2-3.1.1[a]" }));
    const dialog = within(region).getByRole("group", { name: "Remove AC.L2-3.1.1[a]" });
    expect(await within(dialog).findByText(/Could not check what rests on AC\.L2-3\.1\.1\[a\]/)).toBeTruthy();
    expect(within(dialog).queryByText(/No evidence rests/)).toBeNull();
  });

  it("removes a tag the server said was free, and refreshes", async () => {
    const { region, onChanged } = renderTags([A, B]);
    vi.mocked(api.checkDocumentTagRemoval).mockResolvedValue({ removable: true, reason: null, links: [] });
    vi.mocked(api.removeDocumentObjectiveTag).mockResolvedValue(undefined);
    fireEvent.click(within(region).getByRole("button", { name: "Remove AC.L2-3.1.1[b]" }));
    const dialog = within(region).getByRole("group", { name: "Remove AC.L2-3.1.1[b]" });
    fireEvent.click(await within(dialog).findByRole("button", { name: "Yes, remove AC.L2-3.1.1[b]" }));
    await waitFor(() => expect(api.removeDocumentObjectiveTag).toHaveBeenCalledWith("org", "d1", "ob"));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("shows a read-only role the tags and no controls", () => {
    const { region } = renderTags([A], false);
    expect(within(region).getByText("AC.L2-3.1.1[a]")).toBeTruthy();
    expect(within(region).queryByRole("button")).toBeNull();
    expect(within(region).queryByRole("textbox")).toBeNull();
  });
});
