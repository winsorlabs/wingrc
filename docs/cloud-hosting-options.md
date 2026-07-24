# Cheaper cloud hosting options for WinGRC deployments

Research notes for docs.wingrc.us. Covers whether GovCloud-style environments are
actually needed, and whether serverless/PaaS options can meaningfully undercut the
cost of a persistently-running VM like wl-util-1.

## Start here: do you actually need GovCloud / Azure Government at all?

Before picking a cloud path, confirm whether GovCloud is even required — it's a
gated environment, not just a pricier region toggle.

**Azure Government** requires proof of eligibility: a government contract number,
a sponsorship letter from a US government entity, or proof of handling regulated
CUI/ITAR/DoD Impact Level data. Everyone who accesses it must be a **US person**
(citizen or green card holder). ([Microsoft Learn][azgov-welcome], [Innovia][azgov-qualify])

**AWS GovCloud (US)** has parallel requirements: the account holder must be a US
entity incorporated to do business in the US, the account holder must be a US
person, and the entity must be able to handle ITAR-controlled data. ([Hyperglance][awsgc-req])

Per [ADR 0005](adr/0005-deployment-topology-per-msp-not-shared-saas.md), WinGRC's
own SSP and evidence data is **not** formally CUI while it stays under the
contractor's own control — CUI status attaches when data is collected/maintained
*by or on behalf of* a government agency, which a contractor's internal working
documents generally aren't. That means most WinGRC customers likely do **not**
need Azure Government or AWS GovCloud *specifically for hosting WinGRC* — commercial
Azure or AWS regions are almost certainly sufficient, have the full service catalog
(no eligibility gate, more mature managed offerings), and are typically cheaper.

GovCloud only makes sense as a *default* choice for a customer that already has an
independent obligation to run there for other parts of their business (an existing
DoD contract requiring IL4/IL5, ITAR data elsewhere in their environment, etc.) —
not because WinGRC's own data classification demands it. Worth stating this
explicitly in the docs so customers don't over-gate themselves into a more
restrictive, more expensive environment they don't actually need.

## Is "true serverless" (Azure Functions / AWS Lambda) actually viable?

Short answer: not without a real rewrite, and it's probably not worth it.

WinGRC's backend is a standard FastAPI application maintaining a connection pool
against PostgreSQL — not written as discrete function handlers. Adapting it to
Azure Functions or Lambda (via an ASGI adapter) is technically possible but comes
with real friction: cold starts, per-invocation execution limits, and — the bigger
issue — a bursty, concurrent-invocation model that can exhaust Postgres's max
connections without adding a connection pooler (e.g. PgBouncer) in front of the
database. That's a genuine re-architecture project, not a hosting swap.

The practical middle ground that gets you the *economic* benefit of serverless
(pay only when used, no idle VM cost) without rewriting the application is
**serverless containers** — Azure Container Apps or AWS Fargate. Same Docker
images we already build for wl-util-1, no code changes, but billed per-second
of actual use instead of a 24/7 VM.

## Azure path: Container Apps

- **Scale-to-zero is real and free.** With `min-replicas: 0`, an idle app incurs
  no usage charge at all — you only pay for the cold-start latency on the next
  request. The first 180,000 vCPU-seconds, 360,000 GiB-seconds, and 2 million
  requests per month are free before any billing kicks in. ([Azure pricing][aca-pricing])
- **Built-in managed HTTPS and custom domains.** This is worth calling out
  directly against what we just did by hand on wl-util-1 — Container Apps
  handles certificate issuance/renewal itself. The entire manual DNS-01 /
  Certbot dance this session required would not be necessary on this path.
- **Azure Government availability is unclear as of this research** — search
  results show an official "Container Apps in Azure Government Cloud" update
  entry and a Microsoft devblog on new container/compute options in Azure
  Government, but also an older (April 2024) community thread asking whether
  it was available yet. This needs a direct, current confirmation from
  Microsoft's Azure Government service list before relying on it for a
  Gov-eligible deployment — don't assume either way. ([Azure updates][aca-gov-update], [Azure Gov devblog][azgov-devblog], [MS Q&A][azgov-qa])
- **Database:** Azure Database for PostgreSQL Flexible Server, Burstable tier —
  cheapest managed Postgres option, genuine PostgreSQL (not just compatible).
- **Storage:** Azure Blob Storage instead of self-hosted MinIO — WinGRC's
  storage abstraction already targets this natively (see `backend/app/storage.py`
  comments), so this is a config change, not a code change, and it removes a
  whole container from the deployment.

## AWS path: Fargate

- **Confirmed available in AWS GovCloud (US)**, running under ECS — note the
  specific limitation that **EKS-on-Fargate is not available in GovCloud**,
  only ECS-on-Fargate. Plain ECS is the right target, not EKS. ([AWS GovCloud docs][fargate-govcloud])
- **Pricing:** $0.04048/vCPU-hour + $0.00444/GB-hour (Fargate Spot offers up to
  70% off for interruption-tolerant workloads, not appropriate for the
  always-needs-to-respond backend/nginx tiers but could suit batch/background
  work if WinGRC ever adds any). ([AWS Fargate pricing][fargate-pricing])
- Not true scale-to-zero the way Container Apps is — ECS/Fargate task count can
  be scaled down to 0 when idle, but there's no built-in wake-on-request the
  way Container Apps or Cloud Run provide. For a low-traffic single-org
  deployment this still beats a 24/7 VM, but it's a coarser lever, not
  automatic.
- **Database:** Aurora Serverless v2 — **confirmed available in both AWS
  GovCloud (US-East) and (US-West)** as of the search results, genuinely
  scales down and is billed accordingly. Note it's Aurora's
  PostgreSQL-*compatible* engine, not vanilla PostgreSQL — very close in
  practice, should work fine with WinGRC's existing SQLAlchemy/Alembic setup,
  but worth a quick compatibility check before committing to it for a real
  deployment. ([AWS Aurora Serverless GovCloud][aurora-govcloud])
- **Storage:** S3 directly, same zero-code-change swap as Blob Storage on the
  Azure side.
- **App Runner** was also considered — roughly 58% more expensive than
  ECS+Fargate for equivalent usage, and its AWS GovCloud availability wasn't
  confirmed in this research. Given Fargate is both cheaper and confirmed
  available, it's the better default; App Runner isn't worth pursuing further
  here. ([Fargate vs App Runner pricing][fargate-vs-apprunner])

## Bottom line

- Confirm GovCloud/Azure Government is actually *required* by the specific
  customer's other obligations before defaulting to it — most WinGRC
  deployments likely don't need it given WinGRC's own data isn't formally CUI.
- Don't pursue true serverless functions (Azure Functions / Lambda) without
  planning a real backend rewrite — not worth it for the cost savings alone.
- Serverless *containers* (Container Apps / Fargate) get the same economic
  win without a rewrite, using the exact images already built for wl-util-1.
- Swapping MinIO for native Blob Storage/S3 is free — no code change, one
  less container to run, applies on both clouds.
- Azure Container Apps likely eliminates the manual TLS/Certbot process this
  session required, if commercial Azure (not confirmed yet for Azure Gov).
- A concrete monthly cost estimate needs real sizing inputs (expected vCPU/
  memory for the backend, request volume, DB storage size, evidence storage
  volume) — worth building once there's a rough usage profile to work from.

[azgov-welcome]: https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-welcome
[azgov-qualify]: https://www.innovia.com/questions/what-is-the-azure-government-cloud
[awsgc-req]: https://www.hyperglance.com/blog/aws-govcloud/
[aca-pricing]: https://azure.microsoft.com/en-us/pricing/details/container-apps/
[aca-gov-update]: https://azure.microsoft.com/en-us/updates?id=container-apps-in-azure-government-cloud
[azgov-devblog]: https://devblogs.microsoft.com/azuregov/new-container-and-compute-options-in-azure-government/
[azgov-qa]: https://learn.microsoft.com/en-us/answers/questions/1655402/will-azure-container-apps-be-available-for-azure-f
[fargate-govcloud]: https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-fargate.html
[fargate-pricing]: https://aws.amazon.com/fargate/pricing/
[aurora-govcloud]: https://aws.amazon.com/about-aws/whats-new/2023/05/amazon-aurora-serverless-v2-govcloud-regions/
[fargate-vs-apprunner]: https://cloudonaut.io/fargate-vs-apprunner/
