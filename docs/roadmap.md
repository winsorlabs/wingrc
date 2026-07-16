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

---

## Planned

### J. Assessor Bundle Export

Downloadable ZIP an MSP hands to a C3PAO, assembled from existing data.

**Zip layout:**
```
bundle/
├── index.json            ← metadata + TOC; lists any files missing from storage
├── README.txt            ← human-readable TOC; note on future PDF + templates
├── 01_ssp/
│   └── ssp.json          ← per-control blocks with labeled [a]/[b] objectives + statements
├── 02_evidence/
│   ├── manifest.json     ← full evidence manifest + zip_path on file-kind items
│   └── files/            ← file-kind Evidence, deduplicated by evidence_id
├── 03_scores/
│   └── sprs_score.json   ← SPRS total, by-weight and by-family breakdown
└── 04_status/
    ├── control_status.json ← per-objective status + control-level SPRS satisfaction
    └── poam.json           ← Finding + PoamItem rows for this assessment
```

**Implementation:**
- `backend/app/bundle.py` — pure assembly function `assemble_bundle(session, org_id, assessment_id, storage)` returning ZIP bytes. No HTTP concerns; sets up future async-generation path.
- `GET /orgs/{org_id}/assessments/{assessment_id}/bundle` streams the ZIP.
- File evidence fetched from MinIO; fetch failures logged in `index.json → files_missing_from_storage`, rest of bundle proceeds.

**Not building yet:** PDF rendering (Jinja2 + WeasyPrint over `ssp.json`); document-library section in bundle (depends on item M below).

---

### K. Organization Profile

Extend `organization` table (migration 0011). One row per org; all new columns nullable (incomplete profile is valid; bundle export warns, not errors).

**Fields added:** `cage_code`, `uei` (SAM.gov Unique Entity ID), `year_established`, `industry`, `address_line1`, `address_line2`, `city`, `state_or_province`, `postal_code`, `country` (default 'US'), `phone_primary`, `phone_secondary`, `website`, `logo_storage_key`.

CAGE code and UEI are mandatory SSP header fields for DoD contractor submissions — included now to avoid a schema change when bundle format is locked.

**API:** `GET/PATCH /orgs/{org_id}/profile`; `POST /orgs/{org_id}/logo` (multipart, same upload pipeline as evidence).

---

### L. System Description

New table `system_description` (migration 0012). One row per org (not per-assessment — the system is persistent; the bundle snapshots current state at export time). Complements the scope graph: the graph captures *what* (devices, users, nodes); system description captures the *narrative layer* assessors need.

**Fields:** `org_id` (FK, UNIQUE), `system_name`, `system_type` (major_application / general_support_system / minor_application), `operational_status`, `system_description` (narrative), `cui_categories` (JSONB list), `cui_storage_locations` (JSONB: `[{"type": "gcc_high", "description": "..."}]`), `authorization_boundary_description`, `external_connections` (JSONB: `[{"name": "Azure AD", "direction": "bidirectional", "purpose": "..."}]`), `cui_flow_description`.

**API:** `GET/PUT /orgs/{org_id}/system-description` — full upsert (creates on first PUT). The onboarding wizard is a 4-step frontend flow (system type → CUI categories → storage locations → boundary/flow narrative); the backend is one PUT.

---

### M. Personnel Repository

Extends the existing `contact` + `raci_assignment` foundation (migration 0013).

**Changes:**
- `ALTER TABLE contact ADD COLUMN notes Text nullable` — role context, tenure notes, SSP signature-page text.
- New table `contact_documentation_role`: `(contact_id, role)` UNIQUE. Role vocabulary: `it_admin`, `security_officer`, `system_owner`, `authorizing_official`, `president`, `cui_user`, `assessor`, `mssp`, `consultant`, `other`. Answers "who is the Security Officer?" without joining through control_states.

**Three uses of contacts:** (a) populate documentation (SSP/policies/CRM) — this slice; (b) RACI assignment per objective — already modeled; (c) link to authenticated user account — auth/RBAC slice.

**Auth-linkage seam:** `contact` gets no `user_id` FK now. When auth/RBAC lands, the `user` table carries a nullable `contact_id` FK. Dependency runs auth→contact, not contact→auth. A contact can exist without an account (most CUI users won't log into WinGRC).

**API:** `GET/POST /orgs/{org_id}/contacts`, `GET/PATCH/DELETE /orgs/{org_id}/contacts/{contact_id}`, `POST/DELETE /orgs/{org_id}/contacts/{contact_id}/roles`. RACI endpoints stay in the assessments router (assessment-scoped); documentation roles here are org-scoped.

---

### N. Document Library

Two new tables (migration 0014). Prerequisite: M (for `approved_by_contact_id` FK).

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
Onboarding wizard v1 (K + L + M manual entry)
    → Bundle export now has real org/system/people data (J)
    → Document library (N)
    → Personnel connector pull (Liongard / M365 → contacts)
    → Auth / RBAC (authenticated users link to contact rows)
```

---

## Deferred

- **PDF rendering** — Jinja2 + WeasyPrint over `ssp.json`; depends on bundle export (J) being stable.
- **Document-library template content** — paid add-on seed script; depends on document library (N) mechanism being live.
- **Personnel connector** — Liongard / M365 → auto-populate contacts; depends on M.
- **AI implementation statements** — generation worker behind BYO-AI provider abstraction; scaffolding exists.
- **CRM (Customer Responsibility Matrix)** — render from `raci_assignment` + `contact`; depends on M.
- **Scope connector** — Liongard / Datto RMM → `scope_entity`; supplements manual CSV import.
- **Auth / RBAC** — authenticated user accounts; `user` table with nullable `contact_id` FK; role-based API guards.
- **Evidence download hardening** — replace presigned direct-to-MinIO download URLs with the backend streaming evidence bytes itself. Presigned URLs are bearer-token style: anyone with the link can download until it expires, with no per-request re-check of session/auth state. Worth revisiting given the investment already made in session/MFA/lockout hardening (item I) — that hardening doesn't currently extend to the download path. Surfaced while proxying MinIO behind nginx for item O.
- **Frontend build determinism** — generate and commit `frontend/package-lock.json` (none exists yet), then switch `deploy/nginx/Dockerfile` from `npm install` to `npm ci` for reproducible builds. Low priority, not blocking anything currently in flight.
