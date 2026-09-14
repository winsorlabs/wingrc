// @vitest-environment jsdom
//
// Coverage for the Liongard-identities-to-contacts import wizard:
// selection-based (nothing pre-checked), a new identity requires an
// admin-picked affiliation before it can be submitted, a no-email identity
// can't be selected at all, an already-matched identity can be
// re-confirmed with zero or more explicit field refreshes, and a listing
// error (no Environment mapped) surfaces a real, actionable message.
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { Contact, LiongardIdentityCandidate, LiongardIdentityListResult } from "../types";
import { ContactImportWizard } from "./ContactImportWizard";

vi.mock("../api", () => ({
  api: {
    listLiongardContactCandidates: vi.fn(),
    importLiongardContacts: vi.fn(),
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function makeCandidate(overrides: Partial<LiongardIdentityCandidate> = {}): LiongardIdentityCandidate {
  return {
    liongard_id: "id-1",
    email: "ahmed@coopsys.com",
    name: "Ahmed Abdelrehim",
    phone: null,
    enabled: true,
    has_email: true,
    existing_contact: null,
    ...overrides,
  };
}

function makeExistingContact(overrides: Partial<Contact> = {}): Contact {
  return {
    id: "contact-1",
    org_id: "org1",
    name: "Ahmed Old Name",
    email: "ahmed@coopsys.com",
    affiliation: "customer",
    phone: "555-0000",
    role_title: null,
    contract_ref: null,
    notes: null,
    source: "manual",
    source_ref: null,
    documentation_roles: [],
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function makeListing(overrides: Partial<LiongardIdentityListResult> = {}): LiongardIdentityListResult {
  return {
    source_ref: "liongard:environment=8815 (Acme Corp):pulled_at=2026-09-14",
    candidates: [makeCandidate()],
    warnings: [],
    ...overrides,
  };
}

describe("ContactImportWizard — listing", () => {
  it("surfaces a real error and a hint when the org has no Liongard mapping", async () => {
    vi.mocked(api.listLiongardContactCandidates).mockRejectedValue(
      new Error("No Liongard Environment is mapped to this org yet -- set one first.")
    );
    render(<ContactImportWizard orgId="org1" onClose={vi.fn()} onImported={vi.fn()} />);

    await screen.findByText(/No Liongard Environment is mapped/);
    expect(screen.getByText(/Map this org to a Liongard Environment/)).toBeTruthy();
  });

  it("shows pull-level warnings for identities that couldn't be listed at all", async () => {
    vi.mocked(api.listLiongardContactCandidates).mockResolvedValue(
      makeListing({ candidates: [], warnings: ["A Liongard identity record had no email, name, or username -- skipped."] })
    );
    render(<ContactImportWizard orgId="org1" onClose={vi.fn()} onImported={vi.fn()} />);

    await screen.findByText(/no email, name, or username/);
  });
});

describe("ContactImportWizard — new contact", () => {
  it("requires affiliation before the row counts toward Import, then submits it", async () => {
    vi.mocked(api.listLiongardContactCandidates).mockResolvedValue(makeListing());
    vi.mocked(api.importLiongardContacts).mockResolvedValue({
      results: [{ email: "ahmed@coopsys.com", outcome: "created", contact_id: "new-1", detail: null }],
    });
    const onImported = vi.fn();
    render(<ContactImportWizard orgId="org1" onClose={vi.fn()} onImported={onImported} />);

    await screen.findByText("Ahmed Abdelrehim");
    fireEvent.click(screen.getByRole("checkbox"));

    expect(screen.getByRole("button", { name: /Import 1 Selected/ })).toHaveProperty("disabled", true);
    expect(screen.getByText(/Affiliation is required/)).toBeTruthy();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "mssp" } });
    expect(screen.getByRole("button", { name: /Import 1 Selected/ })).toHaveProperty("disabled", false);

    fireEvent.click(screen.getByRole("button", { name: /Import 1 Selected/ }));

    await waitFor(() =>
      expect(api.importLiongardContacts).toHaveBeenCalledWith(
        "org1",
        "liongard:environment=8815 (Acme Corp):pulled_at=2026-09-14",
        [
          {
            email: "ahmed@coopsys.com",
            name: "Ahmed Abdelrehim",
            phone: null,
            role_title: null,
            affiliation: "mssp",
          },
        ]
      )
    );
    await screen.findByText(/1 created, 0 refreshed/);
    expect(onImported).toHaveBeenCalled();
  });

  it("an identity with no email can't be selected", async () => {
    vi.mocked(api.listLiongardContactCandidates).mockResolvedValue(
      makeListing({ candidates: [makeCandidate({ email: null, has_email: false, name: "svc-backup" })] })
    );
    render(<ContactImportWizard orgId="org1" onClose={vi.fn()} onImported={vi.fn()} />);

    await screen.findByText(/svc-backup/);
    expect(screen.getByText(/can't be imported/)).toBeTruthy();
    expect(screen.getByRole("checkbox")).toHaveProperty("disabled", true);
  });
});

describe("ContactImportWizard — already-matched identity", () => {
  it("can be re-confirmed with no refresh (submits an empty refresh_fields list)", async () => {
    const existing = makeExistingContact();
    vi.mocked(api.listLiongardContactCandidates).mockResolvedValue(
      makeListing({ candidates: [makeCandidate({ existing_contact: existing })] })
    );
    vi.mocked(api.importLiongardContacts).mockResolvedValue({
      results: [{ email: "ahmed@coopsys.com", outcome: "unchanged", contact_id: existing.id, detail: null }],
    });

    render(<ContactImportWizard orgId="org1" onClose={vi.fn()} onImported={vi.fn()} />);
    await screen.findByText(/already a contact/);
    fireEvent.click(screen.getByRole("checkbox"));

    // No affiliation gate for an already-matched identity -- Import is
    // immediately enabled once selected.
    expect(screen.getByRole("button", { name: /Import 1 Selected/ })).toHaveProperty("disabled", false);
    fireEvent.click(screen.getByRole("button", { name: /Import 1 Selected/ }));

    await waitFor(() =>
      expect(api.importLiongardContacts).toHaveBeenCalledWith(
        "org1",
        expect.any(String),
        [
          expect.objectContaining({
            email: "ahmed@coopsys.com",
            contact_id: "contact-1",
            refresh_fields: [],
          }),
        ]
      )
    );
  });

  it("checking 'Update phone' includes phone in refresh_fields", async () => {
    const existing = makeExistingContact();
    vi.mocked(api.listLiongardContactCandidates).mockResolvedValue(
      makeListing({
        candidates: [makeCandidate({ existing_contact: existing, phone: "555-9999" })],
      })
    );
    vi.mocked(api.importLiongardContacts).mockResolvedValue({
      results: [{ email: "ahmed@coopsys.com", outcome: "refreshed", contact_id: existing.id, detail: null }],
    });

    render(<ContactImportWizard orgId="org1" onClose={vi.fn()} onImported={vi.fn()} />);
    await screen.findByText(/already a contact/);
    fireEvent.click(screen.getAllByRole("checkbox")[0]);

    const updatePhoneCheckbox = screen.getByRole("checkbox", { name: /Update phone to 555-9999/ });
    fireEvent.click(updatePhoneCheckbox);
    fireEvent.click(screen.getByRole("button", { name: /Import 1 Selected/ }));

    await waitFor(() =>
      expect(api.importLiongardContacts).toHaveBeenCalledWith(
        "org1",
        expect.any(String),
        [expect.objectContaining({ refresh_fields: ["phone"] })]
      )
    );
    await screen.findByText(/0 created, 1 refreshed/);
  });
});
