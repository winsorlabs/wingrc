# 5. Deployment topology: per-MSP instance, not shared multi-tenant SaaS

Date: 2026-07-15
Status: Accepted

## Context

WinGRC's assessment data, evidence, network/data-flow diagrams, and personnel
lists are not formally CUI while under the contractor's own control — SSPs
specifically are "sensitive but unclassified" until submitted to or returned by
the government; they do not inherit CUI marking in the contractor's own copy.
However, evidence artifacts are an uncontrolled upload surface: a customer could
attach a file that genuinely contains CUI, entirely outside the tool's ability to
detect.

On shared multi-tenant SaaS infrastructure, that risk lands on the platform
operator, not just the uploader — exactly the scenario DFARS 252.204-7012's
FedRAMP-Moderate-equivalent requirement for cloud service providers (CSPs) exists
to prevent. Market precedent supports the conservative call: FutureFeed, a
prominent player in this exact space, holds FedRAMP Moderate equivalency,
indicating that serious competitors have already committed to this boundary.

The prior documentation described WinGRC as a "multitenant GRC platform,"
implying a single shared hosting instance across unrelated MSPs. That framing was
never accurate to the intended deployment model and carries real compliance weight
if taken literally.

## Decision

WinGRC is architected and positioned as a shared platform deployed **per MSP**
(or per organization) — not as a single multi-tenant SaaS instance hosting many
unrelated MSPs on shared infrastructure.

Each deployment continues to support multiple internal organizations, frameworks,
and active assessments: an MSP and the client organizations they serve
collaborating within one shared, self-hosted platform. The existing `org_id` +
Postgres Row-Level Security multi-org model is exactly what this requires and is
unchanged. This is a reframing of the deployment topology, not a schema change.

Two distinct isolation boundaries exist and must not be conflated:

- **Per-org isolation within a deployment** — enforced by `org_id` + Postgres
  RLS; separates one client organization's rows from another's inside the same
  running instance.
- **Per-deployment isolation across unrelated MSPs** — enforced by separate
  infrastructure (separate databases, object storage, and network boundaries);
  this is what prevents cross-MSP data exposure.

## Consequences

- No FedRAMP-equivalent hosting obligation is triggered. WinsorLabs is not
  operating shared CUI-adjacent infrastructure across unrelated customers.
- Each deployment gets its own domain and TLS configuration rather than routing
  through a shared WinsorLabs SaaS domain.
- This strengthens rather than weakens the AGPL-3.0 self-hosted distribution
  story: "software you deploy for your own MSP and its clients" is a natural fit
  for open-source distribution and the CMMC market's appetite for on-prem and
  air-gapped deployments.
- WinsorLabs may still offer managed hosting as a revenue path, but only as
  **dedicated, single-customer instances** — not shared infrastructure. This is
  meaningfully different from multi-tenant SaaS and does not reintroduce the
  CUI/FedRAMP concern.
- Positioning language should describe WinGRC as "a shared platform MSPs deploy
  to collaborate with their client organizations," not as "a multi-tenant SaaS
  platform." The word "tenant" where it correctly refers to an Azure/Entra AD
  tenant (standard Microsoft terminology for an Azure subscription context) is
  unaffected by this decision.
