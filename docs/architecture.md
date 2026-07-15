# WinGRC Architecture

This is the authoritative description of what WinGRC is and how it fits
together. For the terse, session-loaded version see `CLAUDE.md`; for individual
decisions see `docs/adr/`.

## Goal

A free, open, MSP-first CMMC (NIST 800-171 Rev 2 / CMMC L2) GRC platform MSPs
deploy to collaborate with their client organizations — feature-matching the
high-priced commercial tools, deployable anywhere the data must live: commercial
Azure, GCC High, on-prem, or fully air-gapped.

## The five layers

### 1. Reference layer (shared across orgs)

- **Control catalog.** The 110 CMMC L2 practices decomposed into their 800-171A
  assessment objectives, each carrying its SPRS point weight (1 / 3 / 5).
  Authoritative, versioned (r2 now; r3 transition later), encoded from NIST's
  published material (public domain) rather than hand-typed.
- **Product baseline library.** One entry per security product (Heimdal,
  Senteon, RoboShadow, RocketCyber, DUO, FenixPyre…). Each entry declares, per
  control: the objectives the product covers when configured correctly, the
  assumed configuration, the evidence spec, and the responsibility split. This
  is the moat — curated, shared across all orgs in a deployment, and vendor-sponsorable. See
  `baselines/` for the format and the worked RocketCyber example.

### 2. Org setup

Select the tools in place, and define scope (users, devices, boundaries) by
spreadsheet upload or by API/MCP pull from Liongard / Datto RMM / Entra. The
moment a tool is marked in-use-and-configured, the baseline library
pre-populates the controls it covers and queues the remaining evidence tasks.

### 3. Assessment core

Per org, per objective: control state (met / not met / partial / N/A /
inherited), responsibility assignment (RACI, which renders as the
customer-responsibility matrix), and linked evidence. This is the
control-by-control view and the source of the SPRS score (start at 110, deduct
the 1/3/5 weight per unmet control).

### 4. Generation

AI-drafted implementation statements, grounded in the relevant product
baseline + the org's scope + the collected evidence, then human-reviewed.
Runs in a background worker, not the request path.

### 5. Deliverables — the assessment bundle

A point-in-time, assessor-ready package: the Lists, the SSP, the baseline docs,
the POA&M, the CRM, and the SPRS score.

## Two ingestion paths

1. **Structured import** (scope): spreadsheet/CSV or Liongard/RMM API → the
   scope graph. Built.
2. **Document ingestion** (baselines/CRMs/evidence): an AI extraction step that
   reads unstructured vendor documents — a CRM, a baseline doc, a SOC 2 report —
   and proposes baseline-library entries, responsibility mappings, and evidence
   attachments.

Both follow the same rule: **propose, don't apply.** Output is candidate records
an engineer confirms.

### Document ingestion: the judgment that matters

A vendor CRM lists every control the product *touches* — but most of those the
customer still owns. The ingestion step must read the responsibility text and
classify each control:

- `provider_satisfies` — the product materially meets this for in-scope assets.
- `shared` — the product enables it; the customer owns a configurable half.
- `customer_owns` — the product explicitly does NOT do this (e.g., RocketCyber
  punts the entire IA family to the customer's IdP). Do NOT credit the product;
  route to whatever actually owns it.

A naive "select tool → mark its controls met" importer credits the vendor for
controls it disclaims — an assessment-failing error. The worked example in
`baselines/rocketcyber.yaml` shows the IA family correctly held back.

When two docs are available (an MSP baseline that asserts authoritative coverage
plus a vendor CRM with the responsibility split), cross-reference them: the
baseline resolves which tool is authoritative for a family, the CRM resolves the
per-objective split.

## Evidence minimization (first-class requirement)

Evidence sprawl is the main reason MSPs abandon GRC tools, so the engine
minimizes deliberately:

- One artifact can satisfy many objectives — capture once, reference many.
- Prefer a single authoritative export (policy/config dump) over many ad-hoc
  screenshots.
- Only `provider_satisfies`/`shared` controls generate provider evidence tasks;
  `customer_owns` and inherited controls generate none.
- Capture product-level config once and reuse across client orgs; re-capture only
  org-specific state.
- Batch tasks by collection session ("while you're in the portal, grab these").
- Track last-captured + cadence so evidence is refreshed, not re-collected.

## AI provider abstraction (bring-your-own)

Every AI call routes through a provider interface each deployment configures:
Anthropic API, Azure OpenAI (GCC High path), or a local model (Ollama/vLLM) for
air-gapped/CUI-sensitive work. "Bring your own AI" means bring your own **API
key** or local model — consumer chat subscriptions can't be used by a
third-party app programmatically. Credentials live in the deployment's secrets
vault. This is both an economic necessity (a free tool can't pay everyone's
inference) and a compliance one (sending CUI to a commercial cloud LLM is a
per-org data-handling decision WinGRC must not make for the user).

## Platform & deployment

React 19 (Vite SPA) · FastAPI (Python 3.13) · PostgreSQL 18 + pgvector ·
SQLAlchemy 2.0 + Alembic · S3-compatible object storage. One container image;
Azure Container Apps for deploying into your own Azure environment, Docker/compose
for self-host, the same image for GCC High and air-gapped. Per-org isolation
within a deployment via `org_id` + Postgres Row-Level Security.

## Data model direction

Built: `scope_entity` (scope graph; lists are views). To add: control catalog +
assessment objectives (+ SPRS weights), product baseline, org↔product link,
control_state (status + responsibility per objective per org), evidence +
evidence_task, implementation_statement. Extends the single-table-+-JSONB + RLS
pattern already in `backend/app/models.py`.

## Build sequence

1. Scope module (AC.L2-3.1.1). **Done.**
2. Control catalog + product baseline + control_state for one product (Heimdal)
   and one family (AC) — the magic loop end to end.
3. Document-ingestion importer (baseline/CRM → candidate library entry).
4. AI implementation statements (grounded, behind the provider abstraction).
5. SPRS, POA&M, CRM, SSP renderers → the bundle generator.
