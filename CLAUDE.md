# WinGRC — context for Claude Code

Read this first, every session. It captures *intent*, not just file structure.

## What WinGRC is

An open, AGPL-licensed, multitenant CMMC (NIST 800-171 Rev 2 / CMMC L2) GRC
platform for MSPs. It does what high-priced commercial GRC tools do —
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

## The five layers

1. **Reference (shared across tenants):** the control catalog (800-171A
   assessment objectives + SPRS point weights) and the **product baseline
   library** (per-product: which objectives it covers when configured, the
   assumed config, the evidence spec, the responsibility split).
2. **Tenant setup:** select tools in place; define scope (spreadsheet upload OR
   Liongard/RMM API/MCP).
3. **Assessment core:** per-objective control state (met / not met / partial /
   N/A / inherited), responsibility (RACI → the MSP-vs-customer CRM), evidence.
4. **Generation:** AI-drafted implementation statements, grounded in baseline +
   scope + evidence, human-reviewed.
5. **Deliverables (the bundle):** point-in-time Lists, SSP, baseline docs,
   POA&M, CRM, and the SPRS score.

## The magic loop (the first real vertical slice to build)

Mark a product in-use → its covered objectives flip to `pending_evidence` →
evidence tasks queue → AI drafts implementation statements → SPRS recomputes.
Prove it thin first: one product (Heimdal), one control family (AC), end to end.

## Hard rules (these define correctness)

- **Candidates, never auto-met.** Imports and document ingestion *propose*; an
  engineer confirms. Nothing is "met" without confirmed config + attached
  evidence. An automated feed must never silently move the audit boundary.
- **Never auto-credit a vendor CRM.** A vendor CRM lists controls the product
  *touches*, most of which the customer still owns. Read the responsibility
  text: if the product says "the customer's IdP does this" (e.g., the entire IA
  family for RocketCyber), classify it `customer_owns` and route it to the
  product that actually owns it — do NOT credit the vendor. See
  `baselines/rocketcyber.yaml` for the worked example.
- **Minimize evidence — this is a first-class requirement.** Evidence sprawl is
  why MSPs hate GRC tools. One artifact can satisfy many objectives: capture
  once, reference many. Prefer one authoritative export over many screenshots.
  Only `provider_satisfies`/`shared` controls generate provider evidence tasks;
  `customer_owns`/inherited never do. Capture product-level config once and
  reuse across tenants; re-capture only tenant-specific state. Batch tasks by
  collection session.
- **BYO-AI / pluggable provider.** Every AI call routes through a provider
  abstraction the tenant configures: Anthropic API, Azure OpenAI (GCC High), or
  a local model (Ollama/vLLM). "Bring your own AI" means bring your own **API
  key** (or local model) — consumer chat subscriptions (Pro/Plus) can't be used
  programmatically. CUI-sensitive tenants must be able to keep generation local;
  never assume CUI may go to a commercial cloud LLM.
- **Scope = denominator.** Control objectives are evaluated against scoped
  assets ("AV on all CUI assets" = devices where category = CUI Asset).

## Data model direction

Built: `scope_entity` (the scope graph; lists are views over it). To add:
control catalog + assessment objectives (+ SPRS weights), product baseline,
tenant↔product link, control_state (status + responsibility per objective per
tenant), evidence + evidence_task, implementation_statement. Build on the
existing single-table-+-JSONB + RLS pattern.

## Stack & conventions

React 19 + Vite (SPA) · FastAPI (Python 3.13) · PostgreSQL 18 + pgvector ·
SQLAlchemy 2.0 + Alembic · S3-compatible storage. One container image; deploy
to Docker / Azure Container Apps / GCC High / air-gapped. Keep the domain core
DB-agnostic and unit-testable (see `backend/app/domain.py`). Tests must pass
and `ruff check` clean before merge. Work on branches, small commits.

## Current status

Scope module (AC.L2-3.1.1 Authorized Entities) is built and tested: parse →
reconcile → render, validated on real data. Next: the assessment engine + the
magic loop, starting from the data-model additions above.
