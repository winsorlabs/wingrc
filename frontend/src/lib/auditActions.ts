import type { AuditLogRow } from "../types";

// A note shown under the action in the audit list, so a row's meaning is
// read without opening Details. An attempt to use an offboarded account is
// the event someone reading a compliance audit log looks for, and until
// 2026-11-20 it was auth.login.failed, the same action as a typo.
//
// Since then it is auth.login.refused_deactivated. Rows written before
// keep auth.login.failed with reason "deactivated" (a record is not
// rewritten), so the reason decides the note for those.
export const OFFBOARDED_ATTEMPT = "Sign-in by a deactivated account";

const LOGIN_FAILED_REASONS: Record<string, string> = {
  bad_password: "Wrong password",
  deactivated: OFFBOARDED_ATTEMPT,
};

export function actionNote(row: Pick<AuditLogRow, "action" | "context">): string | null {
  if (row.action === "auth.login.refused_deactivated") return OFFBOARDED_ATTEMPT;
  if (row.action === "auth.login.failed") {
    const reason = row.context?.["reason"];
    if (typeof reason === "string") return LOGIN_FAILED_REASONS[reason] ?? reason;
  }
  return null;
}
