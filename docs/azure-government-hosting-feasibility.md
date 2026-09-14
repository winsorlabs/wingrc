# Azure Government hosting feasibility for WinGRC production

Research only — no Azure resources, credentials, code, or deployment
changes were made to produce this document. It's written to let Jarrod
decide whether and how to run WinGRC's production instance in Azure
Government; it does not decide anything itself.

**Companion, not replacement, to
[`azure-container-apps-deployment-plan.md`](azure-container-apps-deployment-plan.md).**
That doc lays out the general Container Apps architecture (resource
shape, `az` command sequence) for either commercial Azure or Azure
Government. This doc is the Gov-specific feasibility check that plan's
own "Open items" section asked for — read that doc first for the
architecture, this one for whether it actually holds up in Azure
Government today, and for everything that doc doesn't cover (Key Vault,
SAS URLs, egress, cost, the operating-model question, dev/prod
separation).

## Say the tension out loud, because it's easy to forget later

**Azure Government is a business choice here, not a technical or
compliance requirement.** [ADR 0005](adr/0005-deployment-topology-per-msp-not-shared-saas.md)
concludes WinGRC's own data (SSPs, evidence, scope) is not formally CUI
while it stays under the contractor's own control, and
[`cloud-hosting-options.md`](cloud-hosting-options.md) concludes commercial
Azure is almost certainly sufficient, cheaper, and has a fuller,
more-mature service catalog. Nothing in this document changes that
conclusion. Jarrod wants Azure Government because defense-sector clients
expect it, and that's a legitimate reason — but a future reader (including
future Jarrod) should not infer from "we run in Azure Gov" that WinGRC's
data classification demanded it. It didn't. Say so in any client-facing
material that mentions this hosting choice, so it doesn't get
misrepresented as a compliance claim WinGRC isn't making.

## The two questions that could have been blockers

### 1. PostgreSQL version — not a blocker

`docker-compose.yml` pins **`postgres:18`**.

**Azure Database for PostgreSQL – Flexible Server is fully authorized in
Azure Government**, confirmed directly from Microsoft's own current
sources, not inferred:

- The [Azure Government product GA roadmap](https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-product-roadmap)
  (Microsoft Learn, **page dated 2026-09-03** — six days before this
  research) lists **"Azure Database for PostgreSQL – Flexible Server"**
  as GA and authorized for FedRAMP High, DoD IL4, DoD IL5, *and* DoD IL6
  (Azure Government Secret) — the broadest authorization tier on that
  entire page.
- The [Azure Government compliance-scope table](https://learn.microsoft.com/en-us/azure/azure-government/compliance/azure-services-in-fedramp-auditscope)
  (Microsoft Learn, page dated February 2026, **last updated
  2026-08-18**) confirms the same: Azure Database for PostgreSQL is ✅
  across FedRAMP High, DoD IL2, DoD IL4, and DoD IL5WI in Azure
  Government (not IL6, which is Azure Government *Secret* specifically —
  irrelevant here, we don't need Secret).

**Which major version is orderable in Gov *right now* — genuinely
unconfirmed.** [Supported versions of PostgreSQL in Azure Database for
PostgreSQL flexible server](https://learn.microsoft.com/en-us/azure/postgresql/configure-maintain/concepts-supported-versions)
(Microsoft Learn, updated 2026-08-27) confirms PostgreSQL 18 (minor
18.6) is GA on Flexible Server, down through 17, 16, 15, 14, and 13/12/11
in extended support — but this page doesn't distinguish which regions
(commercial vs. Gov) can currently provision which major version, and I
could not find a Gov-specific version matrix from a primary source. New
major versions have historically rolled out to Gov somewhat after
commercial. **This needs a two-minute direct check** —
`az postgres flexible-server create --version` (or the portal's version
picker) against a real Gov subscription — before committing to a
timeline.

**Why this doesn't matter as much as it sounds like it should:** I
searched the actual schema and migrations
(`backend/app/migrations/versions/`) for anything that would require
PostgreSQL 18 specifically, per your instruction not to assume. **Found
nothing.** The only Postgres-version-sensitive function in use anywhere
in the codebase is `gen_random_uuid()`, which has been built into
Postgres core since version 13 (no `pgcrypto` extension needed). There's
no `MERGE`, no JSON_TABLE, no PG18-specific syntax, nothing. `pgvector`
is mentioned in `CLAUDE.md`'s stack summary and in the existing Container
Apps plan's provisioning steps, but a direct grep found **zero actual
uses** of it anywhere in the current schema or app code — it's
aspirational, not load-bearing.

**Conclusion: if Gov Flexible Server can't yet provision PostgreSQL 18
specifically, provisioning on 16 or 17 instead costs nothing** — no code
change, no migration rewrite, no compatibility risk found. This is why I
did not stop and ask before writing the rest of this document: the
version question turned out not to reshape the plan.

### 2. Container Apps in Azure Government — genuinely unresolved, and more fragile than the existing plan assumes

This is the one place I'd push back hardest on relying on the existing
`azure-container-apps-deployment-plan.md`'s framing ("Confirmed directly
in a real Azure Government subscription: Container Apps is available
there"). That may have been true at the moment someone clicked "create"
successfully in the portal, but "I was able to create one" and "this is a
supported, production-ready service in this cloud" are different claims,
and the current evidence points at the second one being false.

**What the two official Microsoft sources say, and they disagree with
each other:**

- The [Azure Government product GA roadmap](https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-product-roadmap)
  (dated **2026-09-03**) **does not list Azure Container Apps at all** —
  not GA, not "Planned," not "Forecasted." It's simply absent from the
  table. "Container Instances" and "Container Registry" are both there
  (and both fully GA through IL6); Container Apps is not. On a page this
  current and this comprehensive (hundreds of other services listed,
  including ones still in "Planned"/"Forecasted" status), an absence
  reads as "not yet tracked for Gov GA," not as an oversight.
- The [compliance-scope table](https://learn.microsoft.com/en-us/azure/azure-government/compliance/azure-services-in-fedramp-auditscope)
  (dated February 2026, updated 2026-08-18) **does** list it — but only
  at **FedRAMP High and DoD IL2**. No checkmark for IL4, IL5WI, or IL6.
  Compare that to PostgreSQL Flexible Server's row on the *same table*,
  which reaches IL5WI. Container Apps is authorized at the lowest rung
  of the four DoD levels tracked for Azure Government.

**A live discussion between Microsoft customers and engineers,
[GitHub issue microsoft/azure-container-apps#612](https://github.com/microsoft/azure-container-apps/issues/612)
("Government Cloud Support"), open since February 2023, still open,
last comment 2026-06-16** — three months before this research — fills in
the practical picture the two docs above don't:

- May 2024: a commenter reported Container Apps became available in
  **GCC High** (a different, Microsoft-365-adjacent government
  environment, not the same thing as Azure Government) and did
  preliminary testing.
- May 2024: a *separate* commenter reported deploying a Container App
  Environment to **US Gov Arizona**, only to have regional support
  apparently dropped weeks later — the environment became **undeletable**
  (`NoRegisteredProviderFound` for `usgovarizona`). At that point only
  **US Gov Virginia** accepted new Container App Environments.
- **2026-06-16 (the most recent comment, ~3 months old as of this
  writing):** a commenter who'd dug further reported that the [Azure
  Updates entry for "Container Apps in Azure Government Cloud"](https://azure.microsoft.com/en-us/updates?id=Container-Apps-in-Azure-Government-Cloud)
  **still shows Public Preview**, limited to US Gov Virginia — consistent
  with the compliance-scope table's IL2-only authorization, and directly
  contradicting a GA claim. That commenter had an **open Microsoft
  support case** asking three unanswered questions: is it
  production-supported despite the preview label, does it carry an SLA,
  and is there a GA date. No follow-up in the thread since. I could not
  reach the Azure Updates page's live content directly (it renders via
  client-side JS and returned "0 updates found" to this research's fetch
  tool) to independently confirm the preview label still holds today —
  reporting what the June 2026 comment quoted, not what I verified
  myself, and flagging that gap honestly.

**Net assessment: don't treat Container Apps as ready for a production
Azure Government workload right now.** The evidence is consistent
(absent from the current GA roadmap; IL2-only in the compliance table;
preview label as of three months ago; a documented history of a region
losing support out from under a running deployment) and none of it points
the other way. This is a genuine, current gap in the existing internal
plan, not a nitpick — that plan's Gov confidence needs to be re-derived
from a direct, dated portal check, not carried forward from whatever
prompted the "confirmed directly" note.

**If a defense-sector client's own policy requires IL4/IL5** for
anything client-facing (a real possibility even though WinGRC's own data
doesn't compliance-require it — see the tension noted at the top),
Container Apps' IL2-only authorization could be independently
disqualifying regardless of the preview-status question.

**What this means for TLS.** The existing plan's stated Container-Apps
benefit — free managed certificates eliminating the manual Certbot/DNS-01
process — is a *commercial-Azure-confirmed* benefit. I found no
Gov-specific confirmation that managed certificates work at all on
Container Apps in Gov, and given the base service itself is
preview/IL2-only there, I would not plan around it. **Practical
fallback that doesn't depend on Container Apps' Gov status at all:** run
the same containers on plain **Azure VMs** (or **App Service**, which
*is* fully GA through IL5/IL6 per the roadmap table above) and reuse the
exact manual Certbot/DNS-01 process already proven on wl-util-1
(`docs/wl-util-1-worked-example-deployment.md`). That's less
architecturally elegant than the Container Apps plan, but it's built on
services with unambiguous Gov authorization today, and "the TLS story
we already know works" is a reasonable trade for "the compute platform
we're not sure is production-ready yet."

## The rest

### 3. Blob Storage (MinIO replacement)

**Available and fully authorized in Azure Government** — Storage: Blobs
is ✅ across FedRAMP High through IL6 on the compliance-scope table, the
broadest tier on that page alongside PostgreSQL and Key Vault.

**SAS URLs differ from commercial only in hostname, not in kind.** Azure
Government storage uses the `*.blob.core.usgovcloudapi.net` endpoint
suffix instead of commercial's `*.blob.core.windows.net`; a Gov SAS URL
otherwise has the identical query-string structure (`sv=`, `sp=`, `sig=`,
etc.) and the identical security property — a bearer-token-style link
good until it expires, checked by nothing else. **This directly confirms
the security-dependency point below: moving from MinIO to Blob Storage
changes the domain name in the URL and nothing about its exposure.**

**Real implementation gap, not confirmed working today:** `backend/app/storage.py`
defines the `StorageClient` abstract base class with exactly two concrete
implementations — `NullStorageClient` and `MinIOClient` (boto3,
S3-compatible API). **There is no `AzureBlobClient`.** The module's own
comment ("swap endpoint for AWS S3 or Azure Blob in cloud deployments")
and the existing Container Apps plan's "zero code change" framing are
both aspirational, not accurate today — Azure Blob Storage has no native
S3-compatible endpoint, so pointing boto3 at it will not work. A new
`StorageClient` subclass using the `azure-storage-blob` SDK, implementing
the same four methods (`upload_file`, `presigned_url`, `delete_file`,
`get_bytes`), is real — but small and well-scoped — backend work that has
to land before this path is usable. Flagging it here since the existing
plan doc lists this as an "open item to verify," and the verification's
answer is "not implemented."

### 4. Container Registry (ACR)

**Fully authorized across the board** — Container Registry is ✅ through
IL6 on the compliance-scope table, matching Storage and Key Vault as the
most solidly-supported piece of this whole stack in Gov. Azure Government
ACR uses region names like `usgovvirginia` the same way commercial does;
I could not independently confirm the exact Gov domain suffix
(commercial is `*.azurecr.io`; Azure Government services generally move
to a `.us` suffix, e.g. storage's `usgovcloudapi.net`, but I did not find
a primary source stating the ACR Gov hostname directly — confirm at
provisioning time, it won't change the build process below). One
narrower gap found: **ACR's VNet *service endpoints* feature is
explicitly not supported in Azure US Government** (Private Link is the
alternative, and appears to be supported generally, though not
independently confirmed for Gov here) — a networking-hardening detail,
not a blocker to using ACR at all.

**Build pipeline mechanics are unchanged from the existing plan:**
`az acr build --registry <name> --image wingrc-backend:latest ./backend`
submits the Dockerfile and build context to ACR and builds remotely —
already documented as the reason no local Docker/Node is needed on the
machine this research was written from. This works identically in Gov,
just pointed at `az cloud set --name AzureUSGovernment` first and a Gov
resource group/registry.

### 5. Key Vault for the Fernet credential key

**Available and, per the compliance-scope table, the most broadly
authorized service checked in this whole research** — Key Vault is ✅
across FedRAMP High, DoD IL2, IL4, IL5WI, *and* IL6. No ambiguity found
anywhere in this research.

**Yes, it's the right home for this key, and yes, a new deployment needs
a new key** — this follows directly from the existing custody
documentation (`.env.example`'s own comment on
`WINGRC_CREDENTIAL_ENCRYPTION_KEYS`, and `docs/roadmap.md` item O's
planned credential-encryption-key-custody content): the key is
per-deployment, generated at setup before the first connector credential
is entered, never shared across instances. A separate production
instance is, by that same rule, a separate key — not something to
migrate from wl-util-1's key, and not something to generate as part of
this research (per the task's constraint — no key was generated or
printed here).

**How it would actually reach the app — this needs a decision, not just
a "yes, use Key Vault":** today, `WINGRC_CREDENTIAL_ENCRYPTION_KEYS` is a
plain environment variable read by `config.py` at process start. Key
Vault doesn't change that read — the app still wants an environment
variable (or a value it reads at startup) with the same
`label:fernetkey[,label2:fernetkey2,...]` shape; nothing in `crypto.py`
today calls the Key Vault SDK directly. The realistic integration point
is at the **infrastructure layer, not the app layer**: Container Apps
(or App Service, or a VM's systemd unit) can be configured to pull a
secret from Key Vault into the environment/secret store the platform
already injects into the container — e.g. Container Apps' native
[Key Vault reference for secrets](https://learn.microsoft.com/en-us/azure/container-apps/manage-secrets)
on commercial Azure (Gov availability of *that specific feature*
inherits Container Apps' own unresolved Gov status above and wasn't
separately confirmed). The app's own code stays unaware Key Vault
exists; it still just reads an env var. This is a smaller lift than it
sounds, but it's an infrastructure decision to make deliberately, not
something to assume falls out of "we're using Key Vault now."

### 6. Egress — Liongard, SMTP2GO, and a platform-wide change that affects this regardless of Gov

**The most consequential single finding in this whole egress section
has nothing to do with Government cloud specifically.** Microsoft is
retiring **default outbound internet access** for newly-created Azure
virtual networks: as of **March 31, 2026** (already past, as of this
writing), newly created subnets no longer get outbound internet access
for free — any workload that needs to reach the internet (Liongard's
API, SMTP2GO, anything) now requires **explicit** outbound connectivity:
a NAT Gateway, a Standard Load Balancer with outbound rules, a Public IP
directly on the resource, or a third-party network virtual appliance.
This applies to *any new Azure deployment created today*, commercial or
Government. **Any production plan — this one or the existing Container
Apps draft — needs to budget a NAT Gateway (or equivalent) as a required
component, not an optional hardening step.** The existing plan doc
predates this and doesn't mention it.

With that provisioned, **normal outbound HTTPS to Liongard's API and
SMTP2GO's SMTP ports is architecturally unrestricted by the Azure
Government platform itself** — Gov is not air-gapped the way Azure
Government *Secret*/*Top Secret* are; reaching arbitrary public internet
endpoints from a Gov VNet with proper egress configured is a normal,
supported pattern, not something requiring special platform permission.
I found no Gov-specific documentation contradicting this.

**What I could not resolve, because it isn't a platform fact — it's a
policy question:** Azure Government's own [Trusted Internet Connections
(TIC) guidance](https://learn.microsoft.com/en-us/azure/azure-government/compliance/compliance-tic)
exists because *federal agency* networks are often required to route
through TIC-compliant connection points rather than reaching the open
internet directly. Whether that requirement, or something like it,
applies to *this* deployment — a WinsorLabs-run Gov tenant serving an
MSP/contractor's own compliance tooling, not a literal federal agency
network — is a scoping question this research can't answer from Microsoft's
docs alone, because it depends on the specific client's contractual
obligations, not on anything Azure enforces automatically. If a
defense-sector client expects "everything in Gov never talks to a
commercial SaaS," reaching SMTP2GO from inside their compliance tool's
hosting environment could be a conversation worth having explicitly with
that client, even though nothing technical stops it and the content rule
(`email_service.py`) already guarantees zero compliance content ever
transits that SMTP connection. **This is a business/contractual question
to raise with clients as they come up, not something resolved by this
research.**

### 7. Cost — structure, not a number, because the inputs still don't exist

`cloud-hosting-options.md`'s own bottom line already says a real monthly
estimate needs sizing inputs (expected vCPU/memory, request volume, DB
storage size, evidence storage volume) that don't exist yet for a
production instance with an unknown number of client orgs and an unknown
evidence-file volume. That's still true, and this research doesn't change
it — **I'm not going to produce a number here that would just be a
guess wearing a decimal point.**

What I can say: Azure Government pricing for equivalent SKUs (Postgres
Flexible Server Burstable tier, Container Apps/App Service vCPU-seconds,
Blob Storage per-GB) has historically run **higher than commercial
Azure** for the same shape — this is a long-standing, widely-cited
pattern (higher operating/compliance overhead for the isolated Gov
infrastructure), but I could not pull a current, sourced percentage or
side-by-side dollar figure in this research pass: the Azure pricing
calculator pages render their actual rate tables client-side and
returned no usable numbers to this research's fetch tooling, and no
primary Microsoft source I found states a specific Gov premium
percentage as of today. **Once real sizing inputs exist, get the number
from the Azure Government pricing calculator directly** (it's a
portal/calculator tool, not a static page, so it needs to be run
interactively) rather than from a percentage rule of thumb — treat any
remembered "Gov costs X% more" figure as unverified until checked against
that calculator for the actual SKUs in play.

## Dev/prod separation: what actually changes, not just "a second box"

The stated reason for this whole research: the periodic-review
attestation feature means **real client contacts will soon create real
evidence records** — wl-util-1 stays fine as the box that iterates on
unreleased code and carries `reset-dev`, but it cannot also be where that
evidence lives. A second instance only solves that problem if it's
operated differently, not just hosted differently:

- **Release process.** wl-util-1 today runs whatever's on `main` after a
  `git pull --ff-only` (per `docs/deployment.md` §7) — appropriate for a
  dev box that's supposed to reflect current work. Production needs a
  real boundary between "merged to main" and "running in production" —
  at minimum, tagged releases and a deliberate promotion step, not an
  automatic pull-forward. This document doesn't design that process; it
  flags that today's deploy procedure, applied unchanged to production,
  would mean production runs unreleased code the same day it merges.
- **`reset-dev` must not exist as a reachable command in production.**
  Today it's guarded by an environment-variable allowlist
  (`WINGRC_ENVIRONMENT`) that fails closed — that guard needs to be the
  *only* thing standing between production data and a destructive reset,
  which means the production environment's own configuration (not just
  the guard's logic) has to be right, and probably verified as part of
  first standing the instance up, not assumed correct from the code
  existing.
- **Backups.** wl-util-1's `pre-deploy-*.dump` backups (per
  `docs/deployment.md` §7a) are taken manually, immediately before each
  deploy — fine for a dev box deployed by a human at a keyboard. A
  production instance holding real client evidence needs a real backup
  *schedule*, independent of whether anyone happens to be deploying that
  day, with a tested restore path. Not designed here — flagged as a gap
  between "what wl-util-1 does" and "what production needs."
- **Who deploys, and with what credentials.** This is the access-
  constraint question below, but concretely for day-to-day operations:
  today, an AI coding assistant with shell access performs deploys
  end-to-end (build, migrate, verify) against wl-util-1, advised by
  another AI session with a bridge to a developer workstation. Whether
  that continues unchanged for a Gov-hosted production instance holding
  real client evidence is exactly the decision flagged below — not
  assumed either way here.
- **Key custody**, covered above: production gets its own
  `WINGRC_CREDENTIAL_ENCRYPTION_KEYS`, generated at setup, never shared
  with wl-util-1's.

## The access-constraint question — a decision for Jarrod, not decided or designed around here

Azure Government requires everyone who accesses it to be a **US
person** (citizen or green card holder), and the environment carries
handling expectations commercial Azure does not — this was already
noted in `cloud-hosting-options.md` and is unchanged by anything in this
research.

That has a concrete, immediate implication for how this project is
actually run today, and it's worth stating plainly rather than glossing
over: **deployment and operations are currently performed by an AI
coding assistant with real shell access to wl-util-1** (the same pattern
that ran every deploy, migration, and verification step referenced
throughout this document and its companions), **advised by a separate AI
session with a bridge to a developer workstation.** Before any Azure
Government resource exists — before a subscription is created, before a
resource group is named, before a single credential is issued — Jarrod
needs to decide deliberately what that tooling is and isn't allowed to
touch in a Gov context. Concretely, at minimum:

- May an AI assistant hold or use Azure Government credentials at all?
- May it read data that lives in a Gov-hosted database or storage
  account, even transiently (e.g. during a verification query)?
- May it operate the production instance directly (deploys, migrations,
  restarts), or does production require a human running scripts an
  assistant only *writes*?
- Does the answer differ between "routine operations" (a deploy that
  matches an already-reviewed plan) and "investigation" (reading real
  data to debug a live issue)?

**This document does not answer any of those questions, and deliberately
does not design a workflow that assumes a particular answer.** It may
turn out that the current model is entirely fine for Gov, or that
production needs a different operating model than development while
development keeps working exactly as it does today, or something in
between. Whichever way it goes, it should be a choice made once,
explicitly, before credentials exist — not a default that gets
discovered to be wrong after the fact.

## Security dependency that should land with, not after, production

**Shipped and bench-verified 2026-09-14 — this prerequisite is met.**
Evidence downloads no longer use presigned object-storage URLs:
`routers/evidence.py:download_evidence` streams the bytes through the
backend itself, so every download re-checks session/MFA/lockout/org-
access per request — see `docs/roadmap.md`'s "Evidence download
hardening" Done entry for the full writeup. One thing to know before
treating this as fully closed for a real Gov production instance
specifically:

- The slice's own §4 load measurement ran 2026-09-14 against a real
  Postgres+MinIO bench stack: concurrency 1/10/50, a 10 MB file, zero
  errors and zero `/health` failures at every level (full numbers in
  the roadmap entry). That's this project's own dev-box hardware (4
  vCPU, ~5 GiB) and a 10 MB file specifically — re-confirm against
  production-representative hardware and evidence file sizes before
  relying on it unmodified for a real Gov production instance's actual
  load profile, rather than assuming these exact numbers transfer.
- `ProductDocument` downloads (vendor baseline-library documents,
  `routers/admin_products.py`) were deliberately left on
  `presigned_url()` — not customer CUI, msp_admin/consultant_admin only,
  a different model/router than Evidence. Flagged as a small, well-
  scoped follow-up in the roadmap entry, not a blocker for Evidence
  specifically.

Original framing, kept for context: this was a tolerable gap on
wl-util-1 — a LAN-reachable dev box with no real client evidence on it —
but not tolerable on an internet-reachable production instance holding
real client evidence, and switching MinIO for Blob Storage would not
have fixed it on its own: a Gov Blob Storage SAS URL has the exact same
bearer-token property a MinIO presigned URL does, just at a different
hostname. The fix had to be in the access path, and now is.

## Open decisions for Jarrod

1. **Container Apps in Gov: proceed anyway, wait for GA, or route
   around it (VMs/App Service) for production specifically?** The
   evidence leans toward "not ready" as of this research (absent from
   the current GA roadmap, IL2-only authorization, preview label per a
   three-month-old report, a documented history of a region losing
   support mid-deployment) — but Jarrod may have information this
   research doesn't (a direct, current portal check; a support-case
   answer newer than the June 2026 GitHub comment). This is the
   consequential unknown in this whole document.
2. **The AI-tooling access-constraint question above** — not a technical
   question, a policy one, and needs an answer before any Gov resource
   exists.
3. **Whether the TIC/commercial-SaaS-reachability question (§6) needs a
   conversation with defense-sector clients before this goes live for
   them**, even though nothing technical blocks it today.
4. **Release process and backup schedule for production** — flagged as
   gaps above, not designed here; needs its own decision (and probably
   its own short plan) separate from this feasibility research.
5. **Sizing inputs for a real cost estimate** — expected client-org
   count, evidence-storage volume, request volume — once those exist,
   running them through the Azure Government pricing calculator directly
   is a short follow-up, not more research.
6. **Confirm the two remaining unconfirmed facts directly in a real Gov
   subscription/portal before committing to a timeline**: which
   PostgreSQL major versions Flexible Server currently offers in
   `usgovvirginia` (or whichever region), and Container Apps' actual
   current status if Jarrod chooses to pursue it despite the caution
   above.

## Honest gaps in this research

- **Container Apps' current (not June-2026-secondhand) preview/GA status**
  — the Azure Updates page itself renders via client-side JavaScript and
  returned no usable content to this research's fetch tooling. Everything
  reported about its current label comes from a GitHub comment quoting
  that page as of 2026-06-16, not from this research reading the page
  directly.
- **Exact PostgreSQL major-version list currently orderable in Gov
  regions specifically** — confirmed the *service* is GA/authorized;
  did not find a Gov-region-specific version matrix from a primary
  source.
- **ACR's exact Government domain suffix** — confirmed the service is
  authorized and how remote builds work; did not independently verify
  the hostname pattern from a primary source.
- **A sourced, current Azure Government pricing premium percentage or
  dollar comparison** — not found; flagged as needing the interactive
  pricing calculator once sizing inputs exist, rather than guessed at.
- **Whether TIC or similar federal-network requirements apply to this
  specific deployment shape** (a vendor-run Gov tenant for a
  contractor's own tooling, not a literal agency network) — this is a
  contractual/policy scoping question outside what Azure's own
  documentation can answer, not a research gap that more searching would
  close.
