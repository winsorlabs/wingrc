-- ============================================================================
-- One-off cleanup: delete throwaway test organizations (wl-util-1, 2026-08-12)
-- Not a product feature -- see scripts/one-off/README.md.
-- Scope: explicit org IDs only, hardcoded in step 0 below -- never a name
-- pattern, never "everything except X".
--
-- UNATTENDED / FILE-BASED RUN: this script is designed to run end-to-end via
-- `psql -f`, not pasted interactively (PuTTY truncates long pastes). Every
-- preflight check below is a hard assertion (DO $$ ... RAISE EXCEPTION ...
-- $$), not a SELECT meant to be eyeballed -- a failed check aborts the
-- transaction immediately instead of printing a row that scrolls past
-- unattended. ON_ERROR_STOP (set below) makes psql itself exit at the first
-- error rather than continuing into subsequent statements. And even without
-- that: once any statement in this transaction errors, every later statement
-- in the same transaction (including the final COMMIT) fails too or
-- implicitly rolls back -- Postgres will not commit a transaction that had
-- an error partway through. Closing the psql connection at that point (which
-- happens automatically once the script ends, whether by finishing or by
-- ON_ERROR_STOP exiting early) rolls back anything left open. There is no
-- path through this script that partially commits.
--
-- AUDIT LOG EXCEPTION -- read before running:
-- This script deletes audit_log rows for the target orgs (see the DELETE
-- marked "6." below). This is a deliberate, narrowly-scoped one-off
-- exception for test-fixture data, run here as raw SQL entirely outside
-- app/audit.py's code path -- it does not touch, weaken, or set a precedent
-- against ADR 0006's append-only guarantee, which governs log_event() and
-- real org data and is unaffected by this script. The schema offers no
-- orphaning alternative: audit_log.org_id has no ON DELETE CASCADE or SET
-- NULL, so leaving these rows pointing at a deleted org is not mechanically
-- possible -- the final `DELETE FROM organization` would simply fail with a
-- foreign-key violation. These rows document test fixtures, not real
-- compliance history.
--
-- Run as the wingrc owner role (bypasses RLS) -- NOT wingrc_app:
--     docker compose exec -T db psql -U wingrc -d wingrc \
--         -f /path/to/cleanup_test_orgs.sql
-- (after `git pull` on wl-util-1; this file lives at
-- scripts/one-off/cleanup_test_orgs.sql in the repo.)
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ----------------------------------------------------------------------------
-- 0. Target org IDs -- the four confirmed test orgs, hardcoded.
-- ----------------------------------------------------------------------------
CREATE TEMP TABLE _cleanup_targets (org_id uuid) ON COMMIT DROP;
INSERT INTO _cleanup_targets (org_id) VALUES
    ('d57e20df-43c7-42cc-81b2-c6387e1383f1'),  -- Test
    ('16307191-8e64-4433-a025-d830cb334cac'),  -- Test2
    ('a8e452ec-0e48-43da-9841-d9b54248f75d'),  -- Test3
    ('c30d0dab-8638-4772-95d3-e99e1175180a');  -- Test4


-- ============================================================================
-- PREFLIGHT CHECKS -- hard assertions. Each one either passes silently or
-- raises an exception that aborts the whole transaction. Nothing here is
-- meant to be read live; the script either completes or it doesn't.
-- ============================================================================

-- 0a. Each org ID must resolve to exactly its expected name.
DO $$
DECLARE
    matched int;
BEGIN
    SELECT count(*) INTO matched
    FROM organization
    WHERE (id, name) IN (
        ('d57e20df-43c7-42cc-81b2-c6387e1383f1', 'Test'),
        ('16307191-8e64-4433-a025-d830cb334cac', 'Test2'),
        ('a8e452ec-0e48-43da-9841-d9b54248f75d', 'Test3'),
        ('c30d0dab-8638-4772-95d3-e99e1175180a', 'Test4')
    );
    IF matched != 4 THEN
        RAISE EXCEPTION
            'Expected exactly 4 orgs matching (id, name) = (Test..Test4), found %. '
            'At least one UUID does not resolve to its expected name. Aborting.',
            matched;
    END IF;
END $$;

-- 0b. None of these orgs may be the deployment_settings anchor.
DO $$
DECLARE
    anchor_hits int;
BEGIN
    SELECT count(*) INTO anchor_hits
    FROM deployment_settings ds
    WHERE ds.msp_org_id IN (SELECT org_id FROM _cleanup_targets);
    IF anchor_hits > 0 THEN
        RAISE EXCEPTION
            'deployment_settings.msp_org_id matches one of the target orgs -- '
            'refusing to delete the deployment anchor org. Aborting.';
    END IF;
END $$;

-- 0c. audit_log's DB-level append-only hardening (migration 0010's
--     REVOKE/RULE step) must NOT be active. That step was documented as a
--     manual, per-environment action and was never confirmed as run on
--     wl-util-1. If it is active, the DELETE FROM audit_log below would
--     silently no-op (DO INSTEAD NOTHING) and the final DELETE FROM
--     organization would then fail with an FK violation instead --
--     surfacing that as a clean, named failure here rather than a confusing
--     one several statements later.
DO $$
DECLARE
    rule_hits int;
BEGIN
    SELECT count(*) INTO rule_hits
    FROM pg_rules
    WHERE tablename = 'audit_log' AND rulename = 'no_delete_audit_log';
    IF rule_hits > 0 THEN
        RAISE EXCEPTION
            'audit_log has the no_delete_audit_log rule active (DB-level '
            'append-only hardening from migration 0010 has been applied). '
            'The DELETE FROM audit_log below would silently no-op. This '
            'needs a deliberate decision, not an unattended script. Aborting.';
    END IF;
END $$;


-- ============================================================================
-- PREVIEW -- informational only (this run is unattended; these are for the
-- captured output log, not a live checkpoint). Nothing here gates anything.
-- ============================================================================

-- 1. The orgs themselves.
SELECT id, name, created_at, logo_storage_key
FROM organization
WHERE id IN (SELECT org_id FROM _cleanup_targets)
ORDER BY name;

-- 2. Row counts per affected table, before any deletion.
SELECT 'evidence' AS table_name, count(*) FROM evidence
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'evidence_state_link', count(*) FROM evidence_state_link
  WHERE control_state_id IN (
    SELECT id FROM control_state WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
  )
UNION ALL
SELECT 'evidence_task', count(*) FROM evidence_task
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'evidence_task_state_link', count(*) FROM evidence_task_state_link
  WHERE control_state_id IN (
    SELECT id FROM control_state WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
  )
UNION ALL
SELECT 'control_state', count(*) FROM control_state
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'control_state_history', count(*) FROM control_state_history
  WHERE control_state_id IN (
    SELECT id FROM control_state WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
  )
UNION ALL
SELECT 'finding', count(*) FROM finding
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'poa_m_item', count(*) FROM poa_m_item
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'implementation_statement', count(*) FROM implementation_statement
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'assessment', count(*) FROM assessment
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'org_product', count(*) FROM org_product
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'raci_assignment', count(*) FROM raci_assignment
  WHERE contact_id IN (
    SELECT id FROM contact WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
  )
UNION ALL
SELECT 'contact_documentation_role', count(*) FROM contact_documentation_role
  WHERE contact_id IN (
    SELECT id FROM contact WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
  )
UNION ALL
SELECT 'contact', count(*) FROM contact
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'system_description', count(*) FROM system_description
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'scope_entity', count(*) FROM scope_entity
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'audit_log', count(*) FROM audit_log
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT '"user"', count(*) FROM "user"
  WHERE home_org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'user_session', count(*) FROM user_session
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'api_token', count(*) FROM api_token
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'org_membership', count(*) FROM org_membership
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'mfa_backup_code (via user)', count(*) FROM mfa_backup_code
  WHERE user_id IN (
    SELECT id FROM "user" WHERE home_org_id IN (SELECT org_id FROM _cleanup_targets)
  )
UNION ALL
SELECT 'password_history (via user)', count(*) FROM password_history
  WHERE user_id IN (
    SELECT id FROM "user" WHERE home_org_id IN (SELECT org_id FROM _cleanup_targets)
  )
ORDER BY 1;

-- 3. Evidence storage keys specifically -- cross-reference against the
--    MinIO listing (cleanup_test_orgs_minio_list.py, run separately).
--    Expected to return 0 rows per the confirmed evidence count for these
--    four orgs; logged here for the record, not a live gate.
SELECT id, org_id, kind, storage_key, reference_location
FROM evidence
WHERE org_id IN (SELECT org_id FROM _cleanup_targets);


-- ============================================================================
-- DELETES -- ordered leaves-to-root. psql prints "DELETE n" after each
-- statement -- that's the per-table record against the preview counts above.
-- ============================================================================

-- Tier 1: leaves with no other org-scoped table depending on them.
DELETE FROM control_state_history
  WHERE control_state_id IN (
    SELECT id FROM control_state WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
  );
DELETE FROM evidence_state_link
  WHERE control_state_id IN (
    SELECT id FROM control_state WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
  );
DELETE FROM evidence_task_state_link
  WHERE control_state_id IN (
    SELECT id FROM control_state WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
  );
DELETE FROM raci_assignment
  WHERE contact_id IN (
    SELECT id FROM contact WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
  );
DELETE FROM poa_m_item
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);
DELETE FROM contact_documentation_role
  WHERE contact_id IN (
    SELECT id FROM contact WHERE org_id IN (SELECT org_id FROM _cleanup_targets)
  );

-- Tier 2: depended on only by tier-1 tables, now empty of dependents.
DELETE FROM evidence_task
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);
DELETE FROM finding
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);

-- Tier 3: core assessment tables.
DELETE FROM control_state
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);
DELETE FROM evidence
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);

-- Tier 4: assessment itself, plus other org_id-only leaves.
DELETE FROM implementation_statement
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);
DELETE FROM assessment
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);
DELETE FROM org_product
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);

-- 6. audit_log -- see the header comment. Deliberate one-off exception.
DELETE FROM audit_log
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);

-- 7. scope_entity -- no FK at all, filtered by column value only.
DELETE FROM scope_entity
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);

-- 8. contact -- ON DELETE CASCADE from organization would get this anyway,
--    deleted explicitly here for a legible per-table count in psql's output.
DELETE FROM contact
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);

-- 9. The organization rows themselves. Everything else with ON DELETE
--    CASCADE to organization.id (system_description, user, and user's own
--    CASCADE children: user_session, mfa_backup_code, password_history,
--    api_token, org_membership) is cleaned up automatically by this one
--    statement -- no explicit DELETE needed for any of those.
DELETE FROM organization
  WHERE id IN (SELECT org_id FROM _cleanup_targets);


-- ============================================================================
-- FINAL CHECK -- informational, logged before COMMIT. Expected: 0 everywhere.
-- ============================================================================
SELECT 'organization' AS table_name, count(*) FROM organization
  WHERE id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'user (orphan check)', count(*) FROM "user"
  WHERE home_org_id IN (SELECT org_id FROM _cleanup_targets)
UNION ALL
SELECT 'audit_log (orphan check)', count(*) FROM audit_log
  WHERE org_id IN (SELECT org_id FROM _cleanup_targets);

COMMIT;
