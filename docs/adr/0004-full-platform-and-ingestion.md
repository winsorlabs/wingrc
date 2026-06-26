# 4. Full assessment platform, document ingestion, and bring-your-own AI

Date: 2026-06-26
Status: Accepted
Supersedes the framing (not the code) of the initial scope-first scaffold.

## Context

The initial build delivered a vertical slice of the scope/lists module and
framed it as the core of the product. That framing was wrong: lists are one
input pillar plus one output of a much larger assessment engine. The actual
target is a multitenant CMMC GRC platform whose differentiator is tool-driven
control pre-population.

## Decisions

1. **WinGRC is the full five-layer platform** (reference data → tenant setup →
   assessment core → AI generation → assessment bundle), per
   `docs/architecture.md`. The scope module is repositioned as one input pillar
   and the source of the Lists deliverable. No code is discarded.

2. **Two ingestion paths, both propose-don't-apply.** Structured import (scope)
   and AI document ingestion (baselines, CRMs, evidence). Document ingestion
   classifies each control `provider_satisfies` / `shared` / `customer_owns` and
   never auto-credits a vendor for controls its own CRM disclaims.

3. **Evidence minimization is a first-class requirement**, not a nicety —
   deduplicate artifacts across objectives, prefer authoritative exports, only
   provider/shared controls generate provider tasks, reuse product-level config
   across tenants, batch by collection session.

4. **AI is bring-your-own, via a provider abstraction.** Anthropic API / Azure
   OpenAI (GCC High) / local model, configured and credentialed per tenant.
   Required for both economic viability (free core) and CUI compliance.

## Consequences

The next vertical slice is the assessment engine's magic loop (one product, one
family, end to end), not more list features. The data model gains the catalog,
baseline, control-state, and evidence tables on top of the existing
`scope_entity`. The first product baseline library entry is
`baselines/rocketcyber.yaml`.
