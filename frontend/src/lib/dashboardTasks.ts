import type { DocumentReviewWidgetData, OpenEvidenceTaskItem } from "../types";

// The environment view's task list (Q.0): document reviews and open
// evidence tasks, each row saying which it is. Not POA&M items and not
// periodic review cycles -- one feed across those four models is its own
// roadmap item, and the widget says what it leaves out.
export type DashboardTaskRow =
  | {
      source: "document_review";
      key: string;
      title: string;
      detail: string;
      status: string;
    }
  | {
      source: "evidence_task";
      key: string;
      title: string;
      detail: string;
    };

export const TASK_SOURCE_LABELS: Record<DashboardTaskRow["source"], string> = {
  document_review: "Document review",
  evidence_task: "Evidence task",
};

function reviewDetail(status: string, daysUntilDue: number | null): string {
  if (status === "never_approved") return "never approved";
  if (daysUntilDue === null) return status.replace(/_/g, " ");
  if (daysUntilDue < 0) return `overdue by ${-daysUntilDue} day${daysUntilDue === -1 ? "" : "s"}`;
  return `due in ${daysUntilDue} day${daysUntilDue === 1 ? "" : "s"}`;
}

// Reviews first (worst first, as the server orders them), then tasks in
// the server's order. Totals are the server's counts, never the length
// of these capped lists.
export function dashboardTaskRows(
  reviews: DocumentReviewWidgetData,
  tasks: OpenEvidenceTaskItem[],
): DashboardTaskRow[] {
  return [
    ...reviews.items.map((r): DashboardTaskRow => ({
      source: "document_review",
      key: `doc-${r.document_id}`,
      title: `${r.doc_id} ${r.title}`,
      detail: reviewDetail(r.status, r.days_until_due),
      status: r.status,
    })),
    ...tasks.map((t): DashboardTaskRow => ({
      source: "evidence_task",
      key: `task-${t.task_id}`,
      title: t.title,
      detail: [t.collection_session, t.due_date ? `due ${t.due_date}` : null]
        .filter(Boolean)
        .join(" · ") || t.artifact_type,
    })),
  ];
}

export function documentReviewTotal(reviews: DocumentReviewWidgetData): number {
  return reviews.overdue_count + reviews.due_soon_count + reviews.never_approved_count;
}
