// @vitest-environment jsdom
//
// Coverage for roadmap N.3's UI: the review-cadence badge in the library
// list, and the reaffirm control on an approved document.
//
// The badge assertions include a negative -- a `current` document must show
// no badge at all. A badge on every healthy document is noise that trains
// people to stop reading badges, so "renders nothing when fine" is part of
// the behaviour, not an omission.
//
// The reaffirm assertions check that the named approver is what gets sent,
// and that the hint tells the operator they are recorded separately. That
// distinction (approver vs. whoever typed it in) is the whole reason
// approval attaches to a Contact rather than a User, and the UI is where it
// would quietly be lost.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type {
  Contact,
  DocumentDetail,
  DocumentReviewState,
  DocumentRow,
  DocumentVersionRow,
} from "../types";
import { DocumentsPanel } from "./DocumentsPanel";

vi.mock("../api", () => ({
  api: {
    listDocuments: vi.fn(),
    getDocument: vi.fn(),
    getContacts: vi.fn(),
    createDocument: vi.fn(),
    publishDocument: vi.fn(),
    reaffirmDocument: vi.fn(),
  },
}));

// DocumentEditor is lazy-loaded and pulls in the TipTap editor, which is
// irrelevant here and slow to mount. Stubbed so these tests exercise the
// panel's own N.3 surface rather than the editor.
vi.mock("./DocumentEditor", () => ({
  DocumentEditor: () => <div data-testid="editor-stub" />,
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const REVIEW_CURRENT: DocumentReviewState = {
  status: "current",
  last_approved_at: "2026-09-01T00:00:00Z",
  next_due_at: "2027-09-01T00:00:00Z",
  days_until_due: 336,
};

function version(overrides: Partial<DocumentVersionRow> = {}): DocumentVersionRow {
  return {
    id: "v1",
    version_number: 1,
    status: "approved",
    body: "Policy text.",
    is_template_derived: false,
    template_ref: null,
    approved_at: "2026-09-01T00:00:00Z",
    approved_by_contact_id: "c1",
    created_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function row(overrides: Partial<DocumentRow> = {}): DocumentRow {
  return {
    id: "d1",
    doc_id: "AC-POL-001",
    doc_type: "policy",
    title: "Access Control Policy",
    cadence_months: 12,
    is_template_derived: false,
    template_ref: null,
    current_version: version(),
    review: REVIEW_CURRENT,
    created_at: "2026-09-01T00:00:00Z",
    updated_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function detail(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    ...row(),
    versions: [version()],
    tagged_objective_ids: [],
    ...overrides,
  };
}

const CONTACTS: Contact[] = [
  {
    id: "c1",
    org_id: "org1",
    name: "Jane Approver",
    email: "jane@example.com",
    phone: null,
    affiliation: "customer",
    role_title: null,
    contract_ref: null,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
  } as Contact,
];

function mountWith(docs: DocumentRow[], selected?: DocumentDetail) {
  vi.mocked(api.listDocuments).mockResolvedValue(docs);
  vi.mocked(api.getContacts).mockResolvedValue(CONTACTS);
  vi.mocked(api.getDocument).mockResolvedValue(selected ?? detail());
  return render(<DocumentsPanel orgId="org1" currentUserRole="msp_admin" />);
}

describe("review cadence badge", () => {
  it("shows no badge for a document within its cadence", async () => {
    mountWith([row()]);
    await screen.findByText("AC-POL-001");
    expect(screen.queryByText(/review overdue/i)).toBeNull();
    expect(screen.queryByText(/review due soon/i)).toBeNull();
    expect(screen.queryByText(/never approved/i)).toBeNull();
  });

  it("shows an overdue badge in the list", async () => {
    mountWith([
      row({
        review: {
          status: "overdue",
          last_approved_at: "2025-01-01T00:00:00Z",
          next_due_at: "2026-01-01T00:00:00Z",
          days_until_due: -272,
        },
      }),
    ]);
    await screen.findByText("AC-POL-001");
    expect(screen.getByText(/review overdue/i)).toBeTruthy();
  });

  it("distinguishes never-approved from overdue", async () => {
    mountWith([
      row({
        current_version: version({ status: "draft", approved_at: null }),
        review: {
          status: "never_approved",
          last_approved_at: null,
          next_due_at: null,
          days_until_due: null,
        },
      }),
    ]);
    await screen.findByText("AC-POL-001");
    expect(screen.getByText(/never approved/i)).toBeTruthy();
    expect(screen.queryByText(/review overdue/i)).toBeNull();
  });
});

describe("reaffirm control", () => {
  it("sends the named approver and says who is recorded separately", async () => {
    const overdue = detail({
      review: {
        status: "overdue",
        last_approved_at: "2025-01-01T00:00:00Z",
        next_due_at: "2026-01-01T00:00:00Z",
        days_until_due: -272,
      },
    });
    vi.mocked(api.reaffirmDocument).mockResolvedValue(detail());
    mountWith([row({ review: overdue.review })], overdue);

    fireEvent.click(await screen.findByText("AC-POL-001"));

    const select = await screen.findByLabelText(/reviewed and approved by/i);
    fireEvent.change(select, { target: { value: "c1" } });
    fireEvent.change(screen.getByLabelText(/note \(optional\)/i), {
      target: { value: "Annual review, no changes" },
    });
    fireEvent.click(screen.getByRole("button", { name: /record review/i }));

    await waitFor(() =>
      expect(api.reaffirmDocument).toHaveBeenCalledWith(
        "org1",
        "d1",
        "c1",
        "Annual review, no changes",
      ),
    );

    // Section 2: the UI must not imply the clicker approved it.
    expect(screen.getByText(/recorded separately as the person who entered it/i)).toBeTruthy();
    expect(screen.getByText(/no new version/i)).toBeTruthy();
  });

  it("cannot be submitted without naming an approver", async () => {
    mountWith([row()], detail());
    fireEvent.click(await screen.findByText("AC-POL-001"));
    const button = await screen.findByRole("button", { name: /record review/i });
    expect(button).toHaveProperty("disabled", true);
    expect(api.reaffirmDocument).not.toHaveBeenCalled();
  });

  it("is read-only for a c3pao_assessor but still shows the verdict", async () => {
    const overdue = detail({
      review: {
        status: "overdue",
        last_approved_at: "2025-01-01T00:00:00Z",
        next_due_at: "2026-01-01T00:00:00Z",
        days_until_due: -272,
      },
    });
    vi.mocked(api.listDocuments).mockResolvedValue([row({ review: overdue.review })]);
    vi.mocked(api.getContacts).mockResolvedValue(CONTACTS);
    vi.mocked(api.getDocument).mockResolvedValue(overdue);
    render(<DocumentsPanel orgId="org1" currentUserRole="c3pao_assessor" />);

    fireEvent.click(await screen.findByText("AC-POL-001"));

    // The verdict is what an assessor is looking for, so it must be visible.
    await waitFor(() => expect(screen.getAllByText(/review overdue/i).length).toBeGreaterThan(0));
    expect(screen.getByText(/Review was due/i)).toBeTruthy();
    // But no way to act on it.
    expect(screen.queryByRole("button", { name: /record review/i })).toBeNull();
  });
});
