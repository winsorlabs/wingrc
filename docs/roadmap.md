# WinGRC Roadmap

Planned features in build sequence. Items marked **Done** are shipped and tested.
Items without a status are planned but not yet started.

---

## Done

- **Scope module** — `scope_entity` graph; CSV/spreadsheet import; authorized-user and device lists as views. AC.L2-3.1.1 authorized-entities slice end-to-end.
- **Assessment engine** — control catalog (800-171A objectives + SPRS weights), product baseline library, `control_state`, SPRS scoring.
- **Magic loop** — activate product → covered objectives → `pending_evidence`; evidence tasks seeded; SPRS recomputed.
- **Evidence** — file upload (MinIO), URL/path references, `evidence_state_link` (one artifact → many objectives), evidence manifest endpoint.
- **Implementation statements** — per-objective SSP narrative; draft/reviewed/approved status; AI-generation scaffolding.
- **Contacts + RACI** — `contact` table; `raci_assignment` per-objective; affiliation (msp/customer/mssp/government/other).
- **Deactivation + audit log** — provenance-based deactivation (all tool-sourced states → `needs_review`; evidence archived); reactivation restores archived evidence; append-only `audit_log`.
- **Reactivation** — re-activating a product restores archived evidence and sets `needs_review` (not `pending_evidence`) so MSP must re-confirm coverage is current.
- **`needs_review` status** — amber state for tool-sourced controls awaiting re-confirmation; deducts from SPRS like other non-met statuses.
- **Evidence tasks panel** — frontend task list grouped by collection session; status patch endpoint; archived tasks hidden with toggle.
- **Findings + POA&M models** — `finding` and `poa_m_item` tables; gap/deficiency/weakness/observation types; severity; remediation milestones.
- **Assessor Bundle Export** — downloadable ZIP (SSP + evidence + scores + status) for C3PAO handoff; `backend/app/bundle_service.py` assembly, `GET /orgs/{org_id}/assessments/{assessment_id}/bundle`, "Generate Assessor Bundle" button on the board. Verified against a real downloaded zip.
- **Onboarding Wizard v1** — Organization Profile (SSP header fields: CAGE/UEI/address/phone/logo), System Description (system type, CUI categories/storage/boundary/flow narrative), and Personnel Repository (contacts + documentation-role assignment) — migrations 0011/0012/0013; `GET/PATCH /orgs/{org_id}/profile`, `POST /orgs/{org_id}/logo`, `GET/PUT /orgs/{org_id}/system-description`, contacts CRUD + role endpoints (`contacts.py`). 3-step wizard on org creation, plus a persistent tabbed Settings page for later edits.
- **Authentication** — session-based login (opaque tokens, HttpOnly+Secure cookie), local password (PBKDF2-HMAC-SHA256, FIPS-140 rationale) + TOTP MFA + backup codes, Microsoft Entra ID SSO, API tokens for machine access — migration 0015. Four roles shipped (`msp_admin`/`msp_engineer`/`customer_poc`/`c3pao_assessor`); see Deferred for role-guard coverage.

---

## Planned

### N. Document Library

Two new tables (a new migration — 0011 through 0015 are already in use by other shipped features, see Done above). Prerequisite: M (for `approved_by_contact_id` FK).

**Monetization boundary:** The matching engine, tagging, and publish/approve flow are core (free, open-source). The curated template content (polished ready-to-use policies) is a separately distributed seed script — not in this repo. `is_template_derived` and `template_ref` columns mark template-derived rows; no code-level paywall.

**Table `document`:** `org_id`, `doc_id` (stable human-readable ID: "AC-POL-001"; UNIQUE per org, MSP-assigned), `title`, `doc_type` (policy/procedure/plan/list/sop/form/other), `status` (draft/under_review/approved/superseded), `version`, `body` (Text; markdown/plain-text), `storage_key` (nullable; for uploaded-file documents), `is_template_derived` (bool), `template_ref` (nullable), `approved_at`, `approved_by_contact_id`.

**Table `document_objective_tag`:** `(document_id, objective_id)` UNIQUE. Tags a document to any number of framework objectives it satisfies.

**Publish action** — `POST /orgs/{org_id}/documents/{doc_id}/publish`:
1. Sets `document.status → approved`, `approved_at → now`.
2. Creates one `Evidence` record (`kind='reference'`, location = document's stable `doc_id`).
3. For each tagged objective: finds the active assessment's `control_state` → creates `EvidenceStateLink`. `control_state.status` is NOT changed — evidence is attached; engineer must review and manually mark objectives met. Same "candidates, never auto-met" discipline as tool activation.

**API:** `GET/POST /orgs/{org_id}/documents`, `GET/PATCH /orgs/{org_id}/documents/{doc_id}`, `POST/DELETE /orgs/{org_id}/documents/{doc_id}/objective-tags`, `POST /orgs/{org_id}/documents/{doc_id}/publish`.

---

### O. Public Documentation / Knowledge Base Site (docs.wingrc.us)

Independent initiative — does not block or get blocked by other roadmap items; schedule wherever makes sense.

**Tooling:** Docusaurus (React-based static site generator). Chosen for stack alignment with the existing frontend, built-in versioned-docs support, and MDX support for embedding interactive components later.

**Approach:** docs-as-code — markdown/MDX content lives in git, changes go through normal PR review, same discipline as the rest of the project.

**Repository — decision needed before scaffolding:** recommend a separate repo (e.g. `wingrc-docs`) rather than folding into the main app repo, so the docs deploy pipeline and contribution surface stay decoupled from the app's own CI/CD. **Jarrod to confirm before any scaffolding begins.**

**Hosting:** static output on Cloudflare Pages, Netlify, or GitHub Pages — any of these give free automatic HTTPS for the docs domain itself. This is separate infrastructure from the app's own nginx/Certbot setup, which is for deployed WinGRC instances, not this docs site.

**Visual direction:** aim for the clean, minimal, sidebar-nav look of docs.fenixpyre.com. Docusaurus's default theme will need custom CSS to get there — budget this as real work, not a quick tweak.

**First planned content, in order:**
1. HTTPS/Certbot + DNSimple DNS-01 deployment runbook — write once the current HTTPS work on wl-util-1 is complete and validated. Document the real, verified process, not in advance.
2. Azure App Registration / M365 SSO setup how-to — write once SSO is implemented and validated end-to-end, not before.

---

## Sequencing

```
Document library (N)
    → Personnel connector pull (Liongard / M365 → contacts)
```

---

## Deferred

- **PDF rendering** — Jinja2 + WeasyPrint over `ssp.json`. Bundle export (J) has shipped and is stable, so this dependency is satisfied; not scheduled yet regardless.
- **Document-library template content** — paid add-on seed script; depends on document library (N) mechanism being live.
- **Personnel connector** — Liongard / M365 → auto-populate contacts; depends on M.
- **AI implementation statements** — generation worker behind BYO-AI provider abstraction; scaffolding exists.
- **CRM (Customer Responsibility Matrix)** — render from `raci_assignment` + `contact`; depends on M.
- **Scope connector** — Liongard / Datto RMM → `scope_entity`; supplements manual CSV import.
- **Role-differentiated RBAC guards** — authentication shipped (see Done, migration 0015); the `require_role` guard mechanism exists and is applied to user-management endpoints (`users.py`) but not yet to the core CMMC data-surface routers (assessments, evidence, contacts, orgs, bundle, frameworks) — any authenticated user of any role currently has equal access there. The three roles named in root `ROADMAP.md` item I (MSP User / Org User / Assessor) don't map 1:1 to the four that shipped; in particular there's no enforced read-only guard for `c3pao_assessor`.
- **Evidence download hardening** — replace presigned direct-to-MinIO download URLs with the backend streaming evidence bytes itself. Presigned URLs are bearer-token style: anyone with the link can download until it expires, with no per-request re-check of session/auth state. Worth revisiting given the investment already made in session/MFA/lockout hardening (item I) — that hardening doesn't currently extend to the download path. Surfaced while proxying MinIO behind nginx for item O.
- **Frontend build determinism** — generate and commit `frontend/package-lock.json` (none exists yet), then switch `deploy/nginx/Dockerfile` from `npm install` to `npm ci` for reproducible builds. Low priority, not blocking anything currently in flight.
