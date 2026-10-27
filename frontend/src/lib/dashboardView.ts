// Which dashboard a role lands on (roadmap Q.0). A default, not a gate:
// every role can switch views, and every widget's data is refused or
// served by its endpoint, never by whether the widget is shown.
//
//   msp_engineer      environment: what is in the boundary, what needs
//                     attention -- the question an MSP engineer asks daily.
//   msp_admin         assessment: how the assessment is progressing.
//   consultant_admin  assessment: compliance data, the consultant's question.
//   customer_poc      environment, decided rather than defaulted: their own
//                     people and devices, their approvals and reviews are
//                     what they act on; statement progress and RACI load
//                     are the consultant's workflow, not theirs.
//   c3pao_assessor    assessment: read-only completeness is what an
//                     assessor examines.
//
// An org with no assessment opens on the environment view for every role
// (OrgDashboard.tsx): the assessment view would be one sentence.
export type DashboardView = "environment" | "assessment";

const ENVIRONMENT_DEFAULT_ROLES = new Set(["msp_engineer", "customer_poc"]);

export function defaultDashboardView(role: string | null | undefined): DashboardView {
  return role && ENVIRONMENT_DEFAULT_ROLES.has(role) ? "environment" : "assessment";
}
