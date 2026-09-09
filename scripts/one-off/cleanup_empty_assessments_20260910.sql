-- ============================================================================
-- One-off cleanup: delete two empty Acme MSP assessments (wl-util-1, 2026-09-10)
-- Not a product feature -- see scripts/one-off/README.md.
-- Scope: two explicit assessment IDs, hardcoded in step 0 below -- never a
-- name pattern, never "everything except X". Acme MSP itself (the
-- deployment_settings anchor org) is NOT touched -- only these two
-- Assessment rows and their dependents.
--
-- Context: Jarrod confirmed these two assessments ("CMMC L2 Assessment
-- 2026-09-08" and "...2026-09-09") carry no real data -- he's about to
-- start a fresh one. Verified empirically before writing this script, not
-- assumed: a preview query across every table with a real FK to
-- assessment_id or control_state_id found 632 control_state rows (316 x 2,
-- from start_assessment's own initial seed -- these assessments predate
-- the 2026-09 catalog fix's 320-objective count) and 2 sprs_snapshot rows
-- (one per assessment), and a flat ZERO everywhere else: control_state_history,
-- evidence_state_link, evidence_task_state_link, raci_assignment, finding,
-- poa_m_item, evidence_task, implementation_statement, and any evidence
-- reachable via a link. That's why this script has only two DELETEs before
-- the assessment rows themselves, unlike cleanup_test_orgs.sql's longer
-- chain -- there is nothing else here to delete.
--
-- NO audit_log deletion, and deliberately so -- unlike cleanup_test_orgs.sql
-- (which deletes audit_log rows because deleting the *organization* row
-- would otherwise hit audit_log.org_id's FK): this script never deletes
-- Acme MSP itself, and audit_log has no FK constraint on assessment_id or
-- control_state_id at all (confirmed against the live schema's
-- information_schema.referential_constraints before writing this), so
-- there is no FK violation to avoid. The preview below also found zero
-- audit_log rows referencing either assessment id or any of its
-- control_state ids directly, but even if there had been some, they would
-- simply survive as historical records referencing a now-deleted
-- assessment -- the same append-only posture ADR 0006 already uses for
-- deleted/anonymized users.
--
-- UNATTENDED / FILE-BASED RUN: designed to run end-to-end via `psql -f`.
-- Every preflight check is a hard assertion (DO $$ ... RAISE EXCEPTION ...
-- $$) that aborts the whole transaction on failure. ON_ERROR_STOP (set
-- below) makes psql itself exit at the first error; even without that, a
-- transaction with an earlier error cannot COMMIT. No path through this
-- script partially commits.
--
-- Run as the wingrc owner role (bypasses RLS) -- NOT wingrc_app:
--     docker compose exec -T db psql -U wingrc -d wingrc \
--         -f /path/to/cleanup_empty_assessments_20260910.sql
-- (after `git pull` on wl-util-1; this file lives at
-- scripts/one-off/cleanup_empty_assessments_20260910.sql in the repo.)
-- ============================================================================

\set ON_ERROR_STOP on

BEGIN;

-- ----------------------------------------------------------------------------
-- 0. Target assessment IDs -- the two confirmed-empty assessments, hardcoded.
-- ----------------------------------------------------------------------------
CREATE TEMP TABLE _cleanup_targets (assessment_id uuid) ON COMMIT DROP;
INSERT INTO _cleanup_targets (assessment_id) VALUES
    ('992fc97b-d59a-4bdc-811d-35459309cbb5'),  -- CMMC L2 Assessment 2026-09-08
    ('25542101-0378-4b9a-b625-f4a33f9de10d');  -- CMMC L2 Assessment 2026-09-09


-- ============================================================================
-- PREFLIGHT CHECKS
-- ============================================================================

-- 0a. Each assessment ID must resolve to exactly its expected name, under
--     Acme MSP specifically.
DO $$
DECLARE
    matched int;
BEGIN
    SELECT count(*) INTO matched
    FROM assessment a
    JOIN organization o ON o.id = a.org_id
    WHERE (a.id, a.name, o.name) IN (
        ('992fc97b-d59a-4bdc-811d-35459309cbb5', 'CMMC L2 Assessment 2026-09-08', 'Acme MSP'),
        ('25542101-0378-4b9a-b625-f4a33f9de10d', 'CMMC L2 Assessment 2026-09-09', 'Acme MSP')
    );
    IF matched != 2 THEN
        RAISE EXCEPTION
            'Expected exactly 2 assessments matching the hardcoded (id, name, org) '
            'triples, found %. At least one UUID does not resolve as expected. Aborting.',
            matched;
    END IF;
END $$;

-- 0b. Neither target may carry any evidence, finding, RACI assignment, or
--     implementation statement -- if either does, this contradicts "no real
--     data" and needs a human decision, not this script proceeding.
DO $$
DECLARE
    real_content int;
BEGIN
    SELECT
        (SELECT count(*) FROM evidence_state_link WHERE control_state_id IN (
            SELECT id FROM control_state WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets)))
        + (SELECT count(*) FROM finding WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets))
        + (SELECT count(*) FROM raci_assignment WHERE control_state_id IN (
            SELECT id FROM control_state WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets)))
        + (SELECT count(*) FROM implementation_statement WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets))
        + (SELECT count(*) FROM evidence_task WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets))
    INTO real_content;
    IF real_content != 0 THEN
        RAISE EXCEPTION
            'Found % row(s) of real content (evidence/finding/RACI/statement/task) '
            'attached to a target assessment -- this contradicts "no real data". Aborting.',
            real_content;
    END IF;
END $$;


-- ============================================================================
-- PREVIEW -- informational only (logged output, not a live checkpoint).
-- ============================================================================

SELECT a.id, a.name, a.status, a.started_at, a.sprs_score, o.name AS org_name
FROM assessment a JOIN organization o ON o.id = a.org_id
WHERE a.id IN (SELECT assessment_id FROM _cleanup_targets);

SELECT 'control_state' AS t, count(*) FROM control_state
  WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets)
UNION ALL
SELECT 'sprs_snapshot', count(*) FROM sprs_snapshot
  WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets)
ORDER BY 1;


-- ============================================================================
-- DELETES -- ordered leaves-to-root.
-- ============================================================================

-- Tier 1: control_state's own children (all confirmed 0 above, deleted
-- anyway for a legible per-table count and so this script still works
-- correctly if ever reused against a target that isn't actually empty).
DELETE FROM control_state_history
  WHERE control_state_id IN (
    SELECT id FROM control_state WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets)
  );
DELETE FROM evidence_state_link
  WHERE control_state_id IN (
    SELECT id FROM control_state WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets)
  );
DELETE FROM evidence_task_state_link
  WHERE control_state_id IN (
    SELECT id FROM control_state WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets)
  );
-- raci_assignment.control_state_id is ON DELETE CASCADE (confirmed against
-- the live schema) -- no explicit DELETE needed; included as a no-op-safe
-- statement anyway for the same reusability reason as above.
DELETE FROM raci_assignment
  WHERE control_state_id IN (
    SELECT id FROM control_state WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets)
  );
DELETE FROM poa_m_item
  WHERE finding_id IN (
    SELECT id FROM finding WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets)
  );

-- Tier 2: depended on only by tier-1 tables.
DELETE FROM evidence_task
  WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets);
DELETE FROM finding
  WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets);

-- Tier 3: core assessment tables.
DELETE FROM control_state
  WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets);
DELETE FROM implementation_statement
  WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets);
DELETE FROM sprs_snapshot
  WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets);

-- Tier 4: the assessment rows themselves.
DELETE FROM assessment
  WHERE id IN (SELECT assessment_id FROM _cleanup_targets);


-- ============================================================================
-- FINAL CHECK -- informational, logged before COMMIT. Expected: 0 everywhere.
-- ============================================================================
SELECT 'assessment' AS table_name, count(*) FROM assessment
  WHERE id IN (SELECT assessment_id FROM _cleanup_targets)
UNION ALL
SELECT 'control_state (orphan check)', count(*) FROM control_state
  WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets)
UNION ALL
SELECT 'sprs_snapshot (orphan check)', count(*) FROM sprs_snapshot
  WHERE assessment_id IN (SELECT assessment_id FROM _cleanup_targets);

COMMIT;
