import { describe, expect, it } from "vitest";
import type { DocumentReviewWidgetData, OpenEvidenceTaskItem } from "../types";
import { dashboardTaskRows, documentReviewTotal } from "./dashboardTasks";

const reviews: DocumentReviewWidgetData = {
  overdue_count: 1,
  due_soon_count: 0,
  never_approved_count: 1,
  items: [
    {
      document_id: "d1", doc_id: "POL-001", title: "Access Control Policy", status: "overdue",
      last_approved_at: null, next_due_at: null, days_until_due: -3,
    },
    {
      document_id: "d2", doc_id: "POL-002", title: "Draft", status: "never_approved",
      last_approved_at: null, next_due_at: null, days_until_due: null,
    },
  ],
};
const tasks: OpenEvidenceTaskItem[] = [
  { task_id: "t1", title: "Firewall export", artifact_type: "export", collection_session: "Network", due_date: null },
];

describe("dashboardTaskRows", () => {
  it("labels every row with its source, reviews first", () => {
    const rows = dashboardTaskRows(reviews, tasks);
    expect(rows.map((r) => [r.source, r.title, r.detail])).toEqual([
      ["document_review", "POL-001 Access Control Policy", "overdue by 3 days"],
      ["document_review", "POL-002 Draft", "never approved"],
      ["evidence_task", "Firewall export", "Network"],
    ]);
  });

  it("totals reviews from the server's counts, not the preview", () => {
    expect(documentReviewTotal({ ...reviews, items: [] })).toBe(2);
  });
});
