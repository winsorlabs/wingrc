"""Every request that changes state writes an audit row naming what changed
(roadmap: "Marking an objective met is recorded nowhere", 2026-10-20).

The rule is CLAUDE.md's "every state change writes an AuditLog row". It was
set after the assessment core was written and never applied backwards, so
this checks behaviour, not HTTP verbs. Each table a request *committed* a
write to must be accounted for by an `audit_log` row the same request
committed: one whose `entity_type` is that table, or one of the parent types
in ACCOUNTED_BY. "Some audit row exists" is not enough -- that is satisfied
by something irrelevant (an assessment's creation once passed on its RACI
copy-forward row alone). A POST that writes nothing is never in scope.

How it observes: `_app_session` (conftest.py) brackets exactly one request.
While a request is open, a cursor-level listener records every
INSERT/UPDATE/DELETE (table, and an UPDATE's columns), which catches ORM
flushes and raw SQL alike, and reads `entity_type` from each audit_log
INSERT's parameters. Writes stay pending until the request's session commits
and are dropped on rollback, so a request that writes, fails and rolls back
is not counted. A SECURITY DEFINER function called by SELECT writes where
no statement shows it, so each `auth.` function the app calls is classified
below as a writer (and what it writes) or a reader; test_audit_check.py fails
on one that is neither.

`WINGRC_AUDIT_CHECK`:
- unset or `enforce`: a test fails if any request it made broke the rule;
- `report`: nothing fails, every request is appended as one JSON line to
  `WINGRC_AUDIT_REPORT` (default `audit_check_report.jsonl`) for measuring;
- `off`: no observation at all.
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rule:
    """How a write to `table` is accounted for without an audit row of its
    own type.

    `op` is INSERT, UPDATE or DELETE (None: any). `columns`, for an UPDATE,
    are the columns it may touch: an UPDATE of any other column is not
    covered, so a rule for a login counter never hides a role change.
    `by` names the audit entity types whose row in the same request accounts
    for the write: a derived write that always accompanies an audited change
    needs no exemption, it names what accounts for it. `by=None` is
    bookkeeping: an observation the system records, not a change a person
    makes, so no audit row is owed.

    `actions`, where set, are the audit actions that may account for it: a
    parent type broad enough that one of its rows can stand beside the
    write without describing it (a mark-met row is a `control_state` row
    too) is held to the actions measured making that write. A new path
    writing it fails until its action is added here.

    Every rule says why (tests/test_audit_check.py fails on an empty
    reason). An exemption list is only as strong as the friction of adding
    to it; writing the reason is what makes someone ask whether they are
    exempting or evading.
    """

    table: str
    op: str | None
    columns: frozenset[str] | None
    by: frozenset[str] | None
    reason: str
    actions: frozenset[str] | None = None


def _r(table, op, by, reason, columns=None, actions=None):
    return Rule(table, op, frozenset(columns) if columns else None,
                frozenset(by) if by is not None else None, reason,
                frozenset(actions) if actions else None)


RULES: list[Rule] = [
    # -- bookkeeping: no audit row is owed --------------------------------
    _r("user_session", "UPDATE", None, columns={"last_activity_at"},
       reason="the inactivity timeout's sliding clock (I.4), moved by every authenticated request"),
    _r("api_token", "UPDATE", None, columns={"last_used_at"},
       reason="when a token was last used, stamped on use (throttled to once a minute)"),
    _r("user", "UPDATE", None, columns={"failed_login_count", "locked_until"},
       reason="a sign-in whose password was accepted clears the failure counter and an expired "
              "lock; setting a lock comes from a failed sign-in, which writes auth.login.failed"),
    _r("review_cycle_reviewer", "UPDATE", None, columns={"status", "viewed_at"},
       reason="a reviewer opening the cycle is observed, not a change they make; the row records "
              "who and when, and close_cycle and the attestation document read it "
              "(Jarrod, 2026-10-20)"),
    _r("evidence", "UPDATE", None, columns={"sha256_hash"},
       reason="the bundle export computes an artifact's hash and caches it; the bytes are "
              "unchanged"),
    # -- derived: accounted for by the parent's audit row -----------------
    _r("sprs_snapshot", "INSERT", {"control_state", "org_product", "assessment"},
       reason="recompute_sprs records the score after a change to the states it is computed from"),
    _r("assessment", "UPDATE", {"control_state", "org_product"}, columns={"sprs_score"},
       reason="recompute_sprs writes the score a control-state change or a product change moved"),
    _r("control_state_history", "INSERT", {"control_state", "org_product"},
       reason="the per-objective history of a status change the parent row records"),
    _r("control_state_contributor", None, {"org_product"},
       reason="which products cover an objective, changed only by activating, deactivating or "
              "moving a product's version"),
    _r("control_state", "UPDATE", {"org_product"},
       columns={"status", "responsibility", "updated_at"},
       reason="the magic loop's flips on activation; each is in control_state_history"),
    _r("control_state", "INSERT", {"assessment"},
       reason="an assessment is created with one state per objective"),
    _r("raci_assignment", "INSERT", {"assessment"},
       reason="a new assessment's RACI copied forward from the last one (raci.copy_forward)"),
    _r("evidence_task", "INSERT", {"org_product"},
       reason="a product's evidence tasks fanned out on activation"),
    _r("evidence_task_state_link", "INSERT", {"org_product"},
       reason="which objectives an activation's evidence task serves"),
    _r("evidence", "INSERT", {"evidence_task", "document_version", "review_cycle",
                              "system_description", "control_state"},
       reason="the artifact row a collect, a publish, an attestation, a diagram upload or a "
              "list link creates, each audited on what it was created for",
       # Measured over the suite 2026-10-20. A list link's row is on the
       # objective, so without this a mark-met row would account for it.
       actions={"evidence_task.collect", "document.publish", "review_cycle.attest",
                "system_description.network_diagram.upload",
                "system_description.data_flow_diagram.upload",
                "evidence.list_link", "evidence.list_move_forward"}),
    _r("evidence_state_link", None, {"evidence", "evidence_task", "document_version",
                                     "review_cycle", "control_state"},
       reason="the link between an artifact and an objective, audited on the artifact, the "
              "task, the document version or the objective it was made or archived for",
       # Measured over the suite 2026-10-20; a deactivation's and a
       # reactivation's links have audit rows of their own type.
       actions={"evidence.upload", "evidence.add_reference", "evidence.detach",
                "evidence_task.collect", "review_cycle.attest",
                "evidence.document_link", "evidence.document_move_forward",
                "evidence.document_unlink",
                "evidence.list_link", "evidence.list_move_forward", "evidence.list_unlink"}),
    _r("list_link_event", "INSERT", {"control_state"},
       reason="a list version's link history (L.10), beside the link's own audit row"),
    _r("list_version", "INSERT", {"list_definition"},
       reason="a list's versions, audited on the list"),
    _r("list_control_tag", None, {"list_definition"}, reason="a list's tags, audited on the list"),
    _r("list_tag_event", "INSERT", {"list_definition"}, reason="a list's tag history (L.2.2)"),
    _r("document_version", "INSERT", {"document"},
       reason="a document's versions, audited on the document"),
    _r("document", "UPDATE", {"document_version"}, columns={"current_version_id", "updated_at"},
       reason="a document's pointer to the version just written"),
    _r("document_objective_tag", None, {"document"}, reason="a document's tags (X.1)"),
    _r("document_approval", "INSERT", {"document", "document_version"},
       reason="the approval record a publish or a reaffirmation writes (N.3)"),
    _r("baseline_control", "INSERT", {"product"}, reason="a baseline import's controls"),
    _r("baseline_evidence_spec", "INSERT", {"product"},
       reason="a baseline import's evidence specs"),
    _r("product_baseline_version", "INSERT", {"product"},
       reason="the version a baseline import writes"),
    _r("liongard_sync_result_change", None, {"liongard_sync_result", "asset_approval"},
       reason="one observed change in a Liongard sync, and its approval or rejection"),
    _r("liongard_sync_result", "UPDATE", {"asset_approval"}, columns={"status"},
       reason="a sync result's status, moved by approving or rejecting its last change"),
    _r("scope_entity", "INSERT", {"asset_approval"},
       reason="the entity an approved or rejected Liongard change creates"),
    _r("asset_approval_checklist_item", "INSERT", {"asset_approval"},
       reason="the checklist confirmed at approval"),
    _r("review_cycle_item", "INSERT", {"review_cycle"},
       reason="a cycle's snapshot of users and devices"),
    _r("review_cycle_reviewer", None, {"review_cycle"},
       reason="a cycle's reviewers and their attestations"),
    _r("review_cycle_flag", "INSERT", {"review_cycle_item"},
       reason="a flag raised on a cycle item"),
    _r("password_history", None, {"user"}, reason="a user's password history (I.5)"),
    _r("mfa_backup_code", None, {"user"}, reason="a user's backup codes"),
    _r("user_session", None, {"user"},
       reason="sessions minted at sign-in or revoked by a password reset, revoke-all, a "
              "deactivation or a role change, each audited on the user"),
    _r("api_token", None, {"user"},
       reason="a user's tokens, minted with an API user or removed with them"),
    _r("org_membership", "UPDATE", {"user"}, columns={"role"},
       reason="the membership's role, moved with the user's role (user.role_change)"),
    _r("org_membership", "INSERT", {"organization", "user"},
       reason="memberships a new org or a new user is provisioned with (ADR 0009 M.2)"),
]

# SECURITY DEFINER functions in the `auth` schema the app calls, by what they
# write. A call to a writer counts as that write.
DEFINER_WRITES: dict[str, tuple[str, str]] = {
    "grant_org_membership": ("INSERT", "org_membership"),
    "expire_stale_invites": ("UPDATE", "user"),
    "mark_sprs_reminder_sent": ("INSERT", "sprs_reminder_log"),
}
DEFINER_READS: frozenset[str] = frozenset({
    "all_users_directory", "find_user_for_invite", "find_user_for_login",
    "liongard_environment_holder", "list_template_adoptions", "list_template_heads",
    "msp_role_users", "msp_staff_emails", "my_org_memberships", "org_reviewer_candidates",
    "orgs_due_for_review_cycle_open", "orgs_due_for_sprs_reminder", "orgs_with_documents",
    "orgs_with_liongard_mapping", "product_deployment_footprint", "resolve_api_token",
    "resolve_session", "resolve_user_identity", "review_cycles_due_for_sweep",
})

_DEFINER_CALL = re.compile(r"\bauth\.(\w+)\s*\(", re.IGNORECASE)

_WRITE = re.compile(
    r'^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(?:ONLY\s+)?"?(?:public"?\."?)?"?(\w+)"?',
    re.IGNORECASE,
)
_SET = re.compile(r"\bSET\b(.*?)(?:\bWHERE\b|\bRETURNING\b|$)", re.IGNORECASE | re.DOTALL)


def mode() -> str:
    return os.environ.get("WINGRC_AUDIT_CHECK", "enforce")


def _update_columns(statement: str) -> frozenset[str]:
    m = _SET.search(statement)
    if not m:
        return frozenset()
    cols, depth, cur = [], 0, ""
    for ch in m.group(1):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            cols.append(cur)
            cur = ""
        else:
            cur += ch
    cols.append(cur)
    return frozenset(c.split("=", 1)[0].strip().strip('"').lower() for c in cols if "=" in c)


def _audit_params(parameters, name: str) -> set[str]:
    rows = parameters if isinstance(parameters, (list, tuple)) else [parameters]
    found = set()
    for row in rows:
        if isinstance(row, dict):
            for k, v in row.items():
                if (k == name or k.startswith(name + "__")) and v:
                    found.add(str(v))
    return found


def _entity_types(parameters) -> set[str]:
    return _audit_params(parameters, "entity_type")


@dataclass(frozen=True)
class Write:
    op: str  # INSERT | UPDATE | DELETE
    table: str
    columns: frozenset[str] = frozenset()


def accounted(w: Write, audited: set[str], actions: set[str] = frozenset()) -> bool:
    if w.table == "audit_log" or w.table in audited:
        return True
    for r in RULES:
        if r.table != w.table or (r.op is not None and r.op != w.op):
            continue
        if r.columns is not None and not (w.columns and w.columns <= r.columns):
            continue
        if r.actions is not None and not actions & r.actions:
            continue
        if r.by is None or audited & r.by:
            return True
    return False


@dataclass
class RequestWrites:
    method: str
    route: str
    pending: list[Write] = field(default_factory=list)
    committed: list[Write] = field(default_factory=list)
    pending_audit: set[str] = field(default_factory=set)
    audited: set[str] = field(default_factory=set)
    pending_actions: set[str] = field(default_factory=set)
    actions: set[str] = field(default_factory=set)

    def unaccounted(self) -> dict[str, list[str]]:
        """Each committed table no audit row of this request accounts for,
        with how it was written; empty when the request kept the rule."""
        out: dict[str, list[str]] = {}
        for w in self.committed:
            if accounted(w, self.audited, self.actions):
                continue
            how = w.op + (f"({','.join(sorted(w.columns))})" if w.columns else "")
            out.setdefault(w.table, [])
            if how not in out[w.table]:
                out[w.table].append(how)
        return out


_lock = threading.Lock()
_current: RequestWrites | None = None
violations: list[tuple[str, str, dict[str, list[str]], set[str]]] = []


def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    cur = _current
    if cur is None:
        return
    m = _WRITE.match(statement)
    if not m:
        for name in _DEFINER_CALL.findall(statement):
            if name.lower() in DEFINER_WRITES:
                op, table = DEFINER_WRITES[name.lower()]
                cur.pending.append(Write(op, table))
        return
    op = m.group(1).split()[0].upper()
    table = m.group(2).lower()
    if table == "audit_log" and op == "INSERT":
        cur.pending_audit |= _entity_types(parameters)
        cur.pending_actions |= _audit_params(parameters, "action")
    cols = _update_columns(statement) if op == "UPDATE" else frozenset()
    cur.pending.append(Write(op, table, cols))


def begin(method: str, route: str) -> None:
    global _current
    with _lock:
        _current = RequestWrites(method, route)


def committed() -> None:
    cur = _current
    if cur is not None:
        cur.committed += cur.pending
        cur.audited |= cur.pending_audit
        cur.actions |= cur.pending_actions
        cur.pending, cur.pending_audit, cur.pending_actions = [], set(), set()


def rolled_back() -> None:
    cur = _current
    if cur is not None:
        cur.pending, cur.pending_audit, cur.pending_actions = [], set(), set()


def end() -> None:
    global _current
    with _lock:
        cur, _current = _current, None
    if cur is None:
        return
    bad = cur.unaccounted()
    if cur.committed:
        routes_writing.add((cur.method, cur.route))
    if mode() == "report":
        path = os.environ.get("WINGRC_AUDIT_REPORT", "audit_check_report.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "test": os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" ", 1)[0],
                "method": cur.method,
                "route": cur.route,
                "writes": sorted(
                    {(w.op, w.table, ",".join(sorted(w.columns))) for w in cur.committed}
                ),
                "audited": sorted(cur.audited),
                "actions": sorted(cur.actions),
                "unaccounted": bad or None,
            }) + "\n")
    elif bad:
        violations.append((cur.method, cur.route, bad, set(cur.audited)))


# -- The companion: every mutating route has a test that reaches a write ----
#
# The check above judges only the requests the suite makes, so a route no test
# drives to a write is never judged. With WINGRC_AUDIT_COVERAGE set (the CI
# integration job sets it; a narrow local run does not), the session fails
# when a POST/PUT/PATCH/DELETE route committed no write in any test and is not
# listed here. Each entry says why. An entry whose route now reaches a write
# also fails the session, so the list cannot quietly go stale.
NOT_REACHING_A_WRITE: dict[tuple[str, str], str] = {
    ("POST", "/admin/products/import/dry-run"):
        "writes nothing by design: a dry run of a baseline import",
    ("POST", "/admin/products/import/dry-run-structured"):
        "writes nothing by design: a dry run of a structured baseline import",
    ("POST", "/admin/products/import/from-documents"):
        "writes nothing by design: drafts a baseline from documents for review",
    ("POST", "/admin/products/{product_id}/import/from-documents-with-research"):
        "writes nothing by design: drafts a baseline with research for review",
    ("POST", "/admin/products/{product_id}/research/suggest-urls"):
        "writes nothing by design: suggests URLs to research",
    ("POST", "/orgs/{org_id}/imports/workbook/dry-run"):
        "writes nothing by design: a dry run of a scope workbook import",
    ("POST", "/auth/mfa/enroll"):
        "writes nothing by design: holds a pending secret in the state cookie until confirm",
    ("POST", "/auth/mfa/reenroll"):
        "writes nothing by design: holds a pending secret in the state cookie until confirm",
    ("POST", "/auth/logout"):
        "no test calls it (2026-10-20): its own roadmap item, security-relevant",
    ("POST", "/orgs/{org_id}/users/{user_id}/reset-mfa"):
        "no test calls it (2026-10-20): its own roadmap item, security-relevant",
    ("POST", "/orgs/{org_id}/review-cycles/{cycle_id}/flags/{flag_id}/resolve"):
        "only the customer_poc refusal is tested (2026-10-20); a resolve that writes is not",
}

routes_writing: set[tuple[str, str]] = set()


def coverage_problems(mutating: set[tuple[str, str]]) -> list[str]:
    problems = []
    for key in sorted(mutating - routes_writing - NOT_REACHING_A_WRITE.keys()):
        problems.append(f"no test reaches a write: {key[0]} {key[1]}")
    for key in sorted(NOT_REACHING_A_WRITE.keys() & routes_writing):
        problems.append(f"listed as not reaching a write, but a test now does: {key[0]} {key[1]}")
    for key in sorted(NOT_REACHING_A_WRITE.keys() - mutating):
        problems.append(f"listed, but no such route: {key[0]} {key[1]}")
    for key, why in NOT_REACHING_A_WRITE.items():
        if not why.strip():
            problems.append(f"listed with no reason: {key[0]} {key[1]}")
    return problems
