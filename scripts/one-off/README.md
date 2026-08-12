# One-off scripts

Throwaway operational scripts for a specific dated task — not product
features, not reusable tooling, not covered by tests. Committed here only
so `git pull` can get a script onto a deployment box (PuTTY truncates long
interactive pastes); safe to delete once the task they were written for is
done. Do not import from these or build on them.

## cleanup_test_orgs.sql / cleanup_test_orgs_minio_list.py / cleanup_test_orgs_minio_delete.py

2026-08-12, wl-util-1: deletes four hardcoded throwaway test organizations
(`Test`, `Test2`, `Test3`, `Test4`) and their full FK cascade, plus the
matching MinIO objects. See the header comment in `cleanup_test_orgs.sql`
for the exact table-by-table cascade order and the audit_log deletion
rationale (a deliberate, narrowly-scoped exception to ADR 0006's append-only
guarantee for this specific test-fixture data — read that comment before
reusing this script's shape for anything else).

Run order:
1. `cleanup_test_orgs.sql` via `psql -f` (file-based, not pasted — every
   preflight check is a hard assertion that aborts the transaction on
   failure; the whole script commits atomically or not at all).
2. `cleanup_test_orgs_minio_list.py` — review its output.
3. `cleanup_test_orgs_minio_delete.py` — only after step 1 has committed.
