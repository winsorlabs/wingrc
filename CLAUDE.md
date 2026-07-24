# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read this first, every session. It captures *intent*, not just file structure.

---

## What WinGRC is

An open, AGPL-licensed CMMC (NIST 800-171 Rev 2 / CMMC L2) GRC platform
MSPs deploy to collaborate with their client organizations on compliance. It does
what high-priced commercial GRC tools do —
control-by-control met/not-met, SPRS scoring, AI-drafted implementation
statements, RACI/CRM, evidence storage, full assessment-bundle export — but
free, MSP-first, and deployable anywhere (commercial Azure, GCC High, on-prem,
air-gapped).

The **magic** is tool-driven control pre-population: select the security tools a
tenant runs, and the platform pre-populates the controls those products satisfy
from a curated **baseline library**, then queues only the evidence collection
still needed. The lists are one *output*, not the product.

> Do NOT over-index on the scope/lists module. That is one input pillar plus one
> output. The product is the assessment engine described below.

---

## The five layers

1. **Reference (shared across orgs):** the control catalog (800-171A
   assessment objectives + SPRS point weights) and the **product baseline
   library** (per-product: which objectives it covers when configured, the
   assumed config, the evidence spec, the responsibility split).
2. **Org setup:** select tools in place; define scope (spreadsheet upload OR
   Liongard/RMM API/MCP).
3. **Assessment core:** per-objective control state (met / not met / partial /
   N/A / inherited), responsibility (RACI → the MSP-vs-customer CRM), evidence.
4. **Generation:** AI-drafted implementation statements, grounded in baseline +
   scope + evidence, human-reviewed.
5. **Deliverables (the bundle):** point-in-time Lists, SSP, baseline docs,
   POA&M, CRM, and the SPRS score.

---

## Hard rules (these define correctness)

Violating these produces incorrect assessments. Enforce them in every review.

- **Candidates, never auto-met.** Imports and document ingestion *propose*; an
  engineer confirms. Nothing is "met" without confirmed config + attached
  evidence. An automated feed must never silently move the audit boundary.
  Status transitions: magic loop → `pending_evidence`; human confirms → `met`.
  Never skip `pending_evidence`.

- **Never auto-credit a vendor CRM.** A vendor CRM lists controls the product
  *touches*, most of which the customer still owns. Read the responsibility
  text: if the product says "the customer's IdP does this" (e.g., the entire IA
  family for RocketCyber), classify it `customer_owns` and route it to the
  product that actually owns it — do NOT credit the vendor. See
  `baselines/rocketcyber.yaml` for the worked example.

- **Never credit `coverage_basis = platform_only`.** The magic loop
  (`engine.py:_run_loop`) explicitly excludes baseline controls where
  `coverage_basis == 'platform_only'`. These are controls the vendor satisfies
  for its *own* infrastructure, not the customer's CUI systems. Do not remove
  this filter.

- **Minimize evidence — this is a first-class requirement.** Evidence sprawl is
  why MSPs hate GRC tools. One artifact can satisfy many objectives: capture
  once, reference many. Prefer one authoritative export over many screenshots.
  Only `provider_satisfies`/`shared` controls generate provider evidence tasks;
  `customer_owns`/inherited never do. Capture product-level config once and
  reuse across client orgs; re-capture only org-specific state. Batch tasks by
  collection session.

- **BYO-AI / pluggable provider.** Every AI call routes through a provider
  abstraction each deployment configures: Anthropic API, Azure OpenAI (GCC High),
  or a local model (Ollama/vLLM). CUI-sensitive deployments must be able to keep
  generation local; never assume CUI may go to a commercial cloud LLM.

- **Scope = denominator.** Control objectives are evaluated against scoped
  assets ("AV on all CUI assets" = devices where category = CUI Asset).

- **Contacts are separable from future users.** The `contact` table and all
  RACI/task-assignment FKs use `contact.id`. When auth lands, a `user` table
  gets a nullable `contact_id` FK so a login account maps to an existing
  contact — it does NOT replace it. This means RACI and task assignment work
  today without auth, and auth lands cleanly without a migration that renames or
  collapses the contact table.

- **Verify reference data against authoritative sources.** SPRS weights,
  control families, assessment objective text, and satisfaction types must be
  verified against NIST SP 800-171A Rev 2 and the DoD CMMC scoring methodology
  — not derived from vendor CRMs, blog posts, or third-party summaries. The
  verified weight distribution is: **44 controls × 5 pts + 14 controls × 3 pts
  + 52 controls × 1 pt** (max deduction 314 pts; score range −204 to 110). Any
  migration that touches control weights must be diffed against this.

---

## Stack & conventions

React 19 + Vite (SPA) · FastAPI (Python 3.13) · PostgreSQL 18 + pgvector ·
SQLAlchemy 2.0 + Alembic · S3-compatible storage (MinIO in dev, Azure Blob /
AWS S3 in cloud). One container image; deploy to Docker / Azure Container Apps /
GCC High / air-gapped.

- Keep the domain core DB-agnostic and unit-testable (`backend/app/domain.py`,
  `backend/app/assessment.py`).
- All DB-touching tests use `@pytest.mark.integration` and require
  `WINGRC_TEST_DATABASE_URL`. Unit tests run without a DB.
- `ruff check` must be clean before merge. B008 is suppressed per-file for
  FastAPI router files (see `backend/pyproject.toml`).
- Work on branches, small commits. Push after every commit — dev server is a
  separate Linux box that must `git pull` first.

### Key file locations

| Path | Purpose |
|---|---|
| `backend/app/models.py` | All SQLAlchemy models (single file, ~1100 lines) |
| `backend/app/assessment.py` | Pure domain functions: `compute_sprs`, `magic_loop_updates` |
| `backend/app/engine.py` | DB adapter: `start_assessment`, `activate_org_product`, `deactivate_org_product`, `recompute_sprs` |
| `backend/app/bundle_service.py` | Bundle snapshot + ZIP render (pure function over frozen dataclasses) |
| `backend/app/routers/` | FastAPI routers: `assessments`, `bundle`, `contacts`, `evidence`, `frameworks`, `orgs` |
| `backend/app/storage.py` | `StorageClient` ABC + `MinIOClient` + `NullStorageClient` |
| `backend/app/audit.py` | `log_event()` — writes `AuditLog` rows |
| `backend/migrations/` | Alembic migrations (currently 0001–0015) |
| `baselines/` | YAML product baselines (`heimdal.yaml`, `rocketcyber.yaml`, …) |
| `docs/fips.md` | FIPS 140-2/140-3 crypto boundary documentation |

---

## What is built and verified

Everything listed here has passing tests and is deployed on the dev server.

### Scope module (migrations 0001)
`scope_entity` table with JSONB attributes. Spreadsheet import (openpyxl),
dry-run reconcile diff, apply. Catalog views (CUI Assets, CUI Users, etc.) as
enumerated view definitions. Lists are views over the scope graph, not separate
documents. Scope is the denominator for control evaluation.

### Assessment engine (migrations 0002–0006)
Full model set: `Framework → Control → AssessmentObjective → ControlState`;
`Product → BaselineControl → BaselineEvidenceSpec`; `OrgProduct`.

**SPRS scoring** (`assessment.py:compute_sprs`): groups objectives by control,
applies worst-objective-wins rollup per control, deducts weight for any
non-passing control (statuses: `not_met`, `partial`, `pending_evidence`,
`needs_review`). `met`, `inherited`, `not_applicable` do not deduct. Score is
written to `assessment.sprs_score` on every recompute and is always recomputed
fresh before bundle export.

**Magic loop** (`engine.py:_run_loop`, pure function in `assessment.py`):
- Activating a product → objectives it covers flip `not_met → pending_evidence`
- `coverage_basis == 'platform_only'` is excluded — vendor self-coverage never
  credits the customer's CUI environment
- SPRS recomputed after every product activation/deactivation
- Re-activation after deactivation restores archived tasks/links; restored states
  go to `needs_review` (not `pending_evidence`) — MSP must re-confirm prior
  artifacts are still current

**Deactivation/archive lifecycle** (`engine.py:deactivate_org_product`):
- All control states sourced from the deactivated product → `needs_review`
- Evidence-state links attributed to the product → archived (`is_archived=True`,
  `archived_by_product` FK set for provenance-based reversal on re-activation)
- Evidence tasks seeded by this product → archived + closed (`na`)
- `OrgProduct.status → decommissioned`, timestamp set
- SPRS recomputed — `needs_review` does not satisfy, score drops

**Audit logging**: every state change writes an `AuditLog` row via
`audit.log_event()`. Context dict captures `via`, `product_name`,
`assessment_id`, and any before/after values. The bundle export writes
`action="bundle.export"`.

**Control state history**: `ControlStateHistory` table captures every
`(previous_status, new_status, previous_responsibility, new_responsibility,
change_reason)` transition with FK to `control_state`.

### Evidence collection (migrations 0007–0010)
`Evidence` (file / reference), `EvidenceStateLink` (many-to-many, with
`is_archived` + `archived_by_product` for deactivation reversal),
`EvidenceTask`, `EvidenceTaskStateLink`.

**Upload**: magic-byte validation + extension allowlist + 50 MB cap. Storage key:
`{org_id}/evidence/{evidence_id}/{evidence_id}{ext}`. Stored in MinIO
(`StorageClient.upload_file`). Presigned URL via `StorageClient.presigned_url`
using the public endpoint so browser-facing URLs resolve (see FIPS section).

**Evidence task fan-out**: when a product is activated, `BaselineEvidenceSpec`
rows are translated into `EvidenceTask` rows with dedup:
1. By `baseline_spec_id` — idempotent on re-activation
2. By `(title.lower(), artifact_type)` — same artifact across specs shares one
   task (evidence minimization across controls)
Each task links to multiple `ControlState` rows via `EvidenceTaskStateLink`.

**Task collect** (`POST …/evidence-tasks/{task_id}/collect`): marks task
`collected`, creates the evidence artifact, links it to all control states the
task covers, optionally advances states from `pending_evidence → needs_review`.
One collect action satisfies many objectives.

**Triage / filtering**: evidence task list supports filters by `status`
(open/collected/na/archived), `collection_session`, `artifact_type`, and the
family/control the task is linked to.

### Onboarding wizard (migrations 0011–0013)
- **Org profile** (`Organization`): CAGE code, UEI, address, industry, phone,
  website, logo upload (stored in MinIO, presigned URL for display).
- **System description** (`SystemDescription`): system name/type, operational
  status, CUI categories (JSONB array), CUI storage locations (JSONB array),
  authorization boundary description, external connections (JSONB array), CUI
  flow description. One row per org (UNIQUE constraint).
- **Contacts** (`Contact` + `ContactDocumentationRole`): name, email, phone,
  affiliation (msp/customer/other), role_title, contract_ref. Documentation
  roles: `it_admin`, `security_officer`, `system_owner`, `authorizing_official`,
  `president`, `cui_user`, `assessor`, `mssp`, `consultant`, `other`. RACI
  assignments (`RaciAssignment`) link contacts to control states with
  letters A/R/C/I.

### FIPS 140-2/140-3 audit (no migration required)
Full crypto boundary documented in `docs/fips.md`. Application code is clean:
no direct `hashlib` usage, no hand-rolled crypto. Two fixes shipped:

1. **botocore MD5 suppression** (`storage.py`): `boto3.Config(
   request_checksum_calculation='when_required',
   response_checksum_validation='when_required')` prevents botocore from calling
   `hashlib.md5()` on every `put_object` (hard-fails in FIPS mode).

2. **Public endpoint for presigned URLs** (`storage.py` + `config.py`):
   `WINGRC_STORAGE_PUBLIC_ENDPOINT` env var; a second boto3 client `_s3_pub`
   uses the public LAN address for `generate_presigned_url` so browser-facing
   URLs contain the resolvable host, not the internal Docker `minio` hostname.
   Set in `docker-compose.yml` via `${WINGRC_STORAGE_PUBLIC_ENDPOINT:-}`.

FIPS deployment (not yet built, see roadmap): requires UBI 9 base image,
MinIO FIPS build (BoringCrypto), TLS everywhere, PostgreSQL `scram-sha-256`.
Auth must use `PBKDF2-HMAC-SHA256`; bcrypt/argon2/scrypt are not
CMVP-certified. `docs/fips.md` contains the full deployment checklist and
the ready-to-use SC.L2-3.13.11 SSP implementation text for customers.

### Bundle export (current session)
`GET /orgs/{org_id}/assessments/{assessment_id}/bundle` → ZIP archive.

**Point-in-time integrity**: SPRS recomputed first, all data copied to frozen
`BundleSnapshot` dataclasses (no ORM objects), logo and evidence file bytes
fetched from MinIO and embedded, `generated_at` stamped last.
`render_bundle(snapshot)` is a pure function — no DB or storage calls.

**ZIP layout**:
```
{org_slug}_{date}/
  index.html                    — table of contents
  cover.html                    — org profile, SPRS score, logo (base64)
  ssp/
    01_system_description.html  — SSP Section 1 narrative
    02_implementation.html      — per-control [a]/[b]/[c] statements, RACI, evidence
    03_personnel.html           — contacts with documentation roles
  evidence/
    manifest.html               — per-objective evidence index with zip paths
    files/                      — embedded evidence file bytes
  summary/
    scoring.html                — SPRS deduction table, family breakdown
    outstanding.html            — gaps, open tasks, open findings
```

Evidence files are embedded as bytes (not presigned URLs) so the bundle remains
valid after URL expiry and works in air-gapped delivery. HTML uses inline CSS
with `@media print` — no external dependencies.

`StorageClient.get_bytes(key)` is a non-abstract method (default `b""`);
`MinIOClient` overrides with `get_object`. Existing `InMemoryStorageClient`
stubs in other test files inherit the default and are unaffected.

---

## Data model snapshot (current migrations through 0017)

```
Organization
  └─ SystemDescription (1:1, UNIQUE org_id)
  └─ Contact → ContactDocumentationRole (roles)
  └─ ScopeEntity (scope graph)
  └─ OrgProduct → Product → BaselineControl → BaselineEvidenceSpec
  └─ Assessment
       └─ ControlState (per-objective; FK → AssessmentObjective)
            └─ ControlStateHistory (audit trail)
            └─ EvidenceStateLink → Evidence (file or reference)
            └─ EvidenceTaskStateLink → EvidenceTask
            └─ RaciAssignment → Contact
            └─ ImplementationStatement (body, status, grounded_in JSONB)
  └─ Finding → PoamItem
  └─ AuditLog

Framework → Control → AssessmentObjective
Product → BaselineControl → BaselineEvidenceSpec
```

`ControlState.status` values: `not_met | partial | pending_evidence |
needs_review | met | inherited | not_applicable`

`ControlState.responsibility` values: `customer_owns | provider_satisfies |
shared | inherited`

SPRS rollup: worst-objective-wins per control. Non-passing statuses:
`not_met`, `partial`, `pending_evidence`, `needs_review`.

---

## Roadmap — priority order

Do not build a slice until its prerequisites ship and tests pass.

### 1. Bundle export ✅ DONE (this session)
`GET …/bundle` → ZIP. See above.

### 2. Auth / RBAC — substantially built, completion tracked separately

**Plan and live status: `docs/PLAN-auth-rbac-completion.md`** (slices I.1–I.9).
Read that file before touching auth. Do not re-derive the design from this entry,
and do not infer completion status from it — the plan header is the only source
of truth for what has landed.

Shipped (migrations 0015–0017): local login + Entra SSO; session cookies
(`wingrc_session`, HttpOnly/SameSite=Lax, Secure in prod) and
`Authorization: Bearer wingrc_<token>` API tokens — NOT JWT. PBKDF2-HMAC-SHA256
@ 600k iterations. TOTP MFA with backup codes. Exponential-backoff lockout.
HIBP k-anonymity check. RLS via `SET LOCAL app.current_org` with SECURITY DEFINER
functions on a pinned search_path. `wingrc_app` role, NOBYPASSRLS. Router-level
guards on all routers.

Roles: `msp_admin`, `msp_engineer`, `customer_poc`, `c3pao_assessor`.

The completion plan covers, in order: audit coverage for privilege-affecting
mutations plus a deny-by-default route harness (I.1); `c3pao_assessor` write
enforcement, currently defined but unenforced (I.2); API token role-downgrade
tracking (I.3); session inactivity timeout for 3.1.11 (I.4); password reset,
admin unlock, and password history (I.5); a hardening sweep (I.6); and the
frontend admin surface, role-aware rendering, and account self-service
(I.7–I.9).

Design constraints that override anything written elsewhere in this file or in
older notes:
- **No SMTP dependency.** One-time tokens (invite, password reset) are returned
  in the response body for out-of-band delivery. GCC High and air-gapped
  deployments have no mail relay. Disregard any "email verification" reference
  in older roadmap text.
- **PBKDF2 only.** bcrypt/argon2/scrypt are not FIPS-140-validated.
- Never log, echo, or persist a raw token, invite token, reset token, TOTP
  secret, or backup code. Hash at rest, return once in the response body, never
  in the audit log.

### 3. FIPS deployment profile
UBI 9 base image, MinIO FIPS build (BoringCrypto), TLS for app↔Postgres and
app↔MinIO, PostgreSQL `scram-sha-256`, nginx FIPS cipher suites. Startup
self-test (`fips_check.py`; `WINGRC_REQUIRE_FIPS` env var). All documented in
`docs/fips.md`.

### 4. RACI assignment UI
Bulk-assign contacts to control families / individual controls in the assessment
UI. The backend model (`RaciAssignment`) and contacts CRUD already exist.
Magic loop pre-populates suggested assignments from `BaselineControl.
responsibility` field (MSP-vs-customer split). UI: family-level assign cascades
to all child control states; override at individual objective level.

### 5. AI-drafted implementation statements
`POST …/assessments/{id}/objectives/{obj_id}/draft-statement` → calls the
configured AI provider with the baseline spec + scope context + existing evidence
titles as grounding. Returns draft body, stores as `ImplementationStatement`
with `status='draft'`. Human reviews → `reviewed` → `approved`. Provider
abstraction already exists in `config.py` (`ai_provider` setting).

### 6. SPRS score display (dashboard widget)
Read `assessment.sprs_score` (already computed and persisted). Show score,
trend (if multiple assessments), family breakdown. Score is already in the DB
after every product activation/deactivation; no new computation needed — just
expose it in the UI.

### 7. Document library / SSP templates
Org-level document store for policies, procedures, and plans that flow into
implementation statements. `Document` model (title, category, body_text or
storage_key for uploaded files). AI generation can cite document library items
in `grounded_in` JSONB. Templates for common CMMC policies (AUP, IR plan,
media sanitization SOP).

### 8. Connectors (Liongard / RMM scope ingestion)
API/MCP-based scope ingestion to replace manual spreadsheet upload. Liongard
inspector data → `scope_entity` records (dry-run + apply same as workbook
importer). Connector credentials stored per-org. This feeds Layer 2 (tenant
setup) without manual data entry.

### 9. Evidence task enhancements

**(a) Task assignment to contacts** — add nullable `assigned_to UUID FK → contact`
to `evidence_task`. Schema change is one column + one index; add after auth
lands so the assignee becomes a real login target for "my tasks" and email
notification.

**(b) Recurrence engine** — `AssessmentObjective.cadence` (annual/quarterly/
monthly) and `EvidenceTask.due_date` already exist. Auto-regenerate a new task
when the current one is marked collected and the next due date is within the
collection window. Tracks `last_completed_at`, `next_due_at`; flags overdue/
at-risk. Pushes recurring tickets to PSA (Autotask) via outbound webhook.
Prerequisites: task assignment, auth.

### 10. Continuous-compliance dashboard
Live posture view: SPRS trend, overdue tasks, controls regressing from met →
needs_review, coverage gap heatmap by family. Powered by `ControlStateHistory`
and `AuditLog`. Alerts via webhook (Teams/Slack) when SPRS drops or a control
regresses.
