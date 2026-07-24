# Azure Container Apps deployment plan (draft — not yet executed)

Second deployment path for WinGRC, alongside the Ubuntu/on-prem path in
`docs/deployment.md` and `wl-util-1-worked-example-deployment.md`. Same
Docker images, no application code changes required *except* one item
flagged below that needs verification. Confirmed directly in a real Azure
Government subscription: Container Apps is available there. Two other
pieces (PostgreSQL Flexible Server, Container Apps managed certificates)
were not independently confirmed for Azure Government — verify both
directly in the portal before relying on this plan for a real deployment.

**Status: this is an architecture plan, not a validated walkthrough.**
Unlike the Ubuntu doc, none of this has been run end-to-end yet. Exact
`az` CLI flags can drift between CLI versions — treat commands below as
the intended shape, confirm against `az <command> --help` before running
against a real subscription.

## Why this path is architecturally simpler than the Ubuntu one, not just cheaper

Several of the hardest problems from the Ubuntu deployment don't apply
here at all:

- **No manual Certbot/DNS-01 dance.** Container Apps issues and renews a
  free managed certificate for a bound custom domain automatically — this
  replaces the entire Step 1/Step 6 process from the worked example.
- **No MinIO subdomain-routing workaround.** That whole problem existed
  because self-hosted MinIO's presigned URLs needed a reverse proxy in
  front of them. Azure Blob Storage's own endpoint (e.g.
  `https://<account>.blob.core.usgovcloudapi.net/...`) is already a
  public HTTPS endpoint with a valid Microsoft-issued certificate — SAS
  URLs point directly at it, no proxy or subdomain trick needed.
- **No `.env`-drift risk the same way.** Container Apps secrets/env vars
  are set via the Azure control plane (CLI/portal), not a local gitignored
  file that's easy to forget to update on a redeploy.

**Needs verification before trusting this plan:** WinGRC's storage
abstraction is described (see `backend/app/storage.py` comments) as
already supporting Azure Blob as an alternate backend to MinIO/S3. Confirm
this is actually implemented (not just planned) before committing to the
"zero code change" claim — Blob Storage's native API isn't S3-compatible,
so it needs the `azure-storage-blob` SDK, not boto3/the MinIO client
already in use. If it's not implemented yet, that's real backend work to
scope separately before this deployment path is usable.

## Prerequisites

- An Azure Government (or commercial Azure) subscription.
- `az` CLI installed and logged in (`az login`), Government cloud selected
  if applicable: `az cloud set --name AzureUSGovernment`.
- No local Docker needed — `az acr build` submits the Dockerfile and build
  context to Azure Container Registry and builds remotely. Relevant given
  neither Docker nor Node exists on the machine these docs are being
  written from.

## Planned resource shape (one full set per customer/MSP, matching the
## per-deployment isolation principle from ADR 0005)

1. **Resource group** — one per deployment, e.g. `rg-wingrc-<customer>`.
2. **Azure Container Registry (ACR)** — hosts the built backend and
   frontend/nginx images.
3. **Azure Database for PostgreSQL Flexible Server**, Burstable tier —
   replaces the `db` container. Needs the `pgvector` extension enabled
   explicitly (it's an allow-listed extension on Flexible Server, not on
   by default) — confirm current WinGRC Postgres version compatibility
   before provisioning.
4. **Azure Storage Account + Blob container** — replaces MinIO, pending
   the code-path verification above.
5. **Container Apps Environment** — the shared environment hosting the
   individual container apps.
6. **Backend container app** — internal ingress only (reachable from
   within the environment, not the public internet directly).
7. **Frontend/nginx container app** — external ingress, custom domain
   bound with a managed certificate. Given Container Apps' own ingress
   already terminates TLS, nginx's role here shrinks to just serving the
   static production bundle and proxying `/api/` to the backend's internal
   address — worth a follow-up look at whether Azure Static Web Apps
   (purpose-built, likely even cheaper) is a better fit for the frontend
   specifically, separate from this initial plan.

## Command shape (draft — verify flags before running)

```bash
az login
az cloud set --name AzureUSGovernment   # skip for commercial Azure
az account set --subscription "<subscription name>"

az group create --name rg-wingrc-<customer> --location usgovvirginia

az acr create --resource-group rg-wingrc-<customer> \
  --name acrwingrc<customer> --sku Basic

az acr build --registry acrwingrc<customer> \
  --image wingrc-backend:latest ./backend
az acr build --registry acrwingrc<customer> \
  --image wingrc-nginx:latest -f deploy/nginx/Dockerfile .

az postgres flexible-server create \
  --resource-group rg-wingrc-<customer> --name pg-wingrc-<customer> \
  --location usgovvirginia --admin-user wingrc \
  --admin-password "<generate a strong password, don't reuse the dev default>" \
  --sku-name Standard_B1ms --tier Burstable --storage-size 32

az postgres flexible-server parameter set \
  --resource-group rg-wingrc-<customer> --server-name pg-wingrc-<customer> \
  --name azure.extensions --value vector

az storage account create \
  --resource-group rg-wingrc-<customer> --name stwingrc<customer> \
  --sku Standard_LRS --kind StorageV2
az storage container create \
  --account-name stwingrc<customer> --name evidence --auth-mode login

az containerapp env create \
  --resource-group rg-wingrc-<customer> --name env-wingrc-<customer> \
  --location usgovvirginia

az containerapp create \
  --resource-group rg-wingrc-<customer> --name ca-wingrc-backend \
  --environment env-wingrc-<customer> \
  --image acrwingrc<customer>.azurecr.io/wingrc-backend:latest \
  --registry-server acrwingrc<customer>.azurecr.io \
  --target-port 8000 --ingress internal \
  --secrets db-url="<postgres connection string>" \
  --env-vars WINGRC_DATABASE_URL=secretref:db-url WINGRC_ENVIRONMENT=production \
  --min-replicas 0 --max-replicas 3

az containerapp create \
  --resource-group rg-wingrc-<customer> --name ca-wingrc-nginx \
  --environment env-wingrc-<customer> \
  --image acrwingrc<customer>.azurecr.io/wingrc-nginx:latest \
  --registry-server acrwingrc<customer>.azurecr.io \
  --target-port 443 --ingress external \
  --min-replicas 0 --max-replicas 3

az containerapp hostname bind --hostname <customer-domain> \
  --resource-group rg-wingrc-<customer> --name ca-wingrc-nginx \
  --environment env-wingrc-<customer> --validation-method CNAME
```

## Open items before this plan is trustworthy enough to execute

1. Confirm the Blob Storage code path is actually implemented in the
   backend (see above) — highest-priority unknown, blocks the whole
   storage piece if not.
2. Confirm PostgreSQL Flexible Server and Container Apps managed
   certificates are actually available in your specific Azure Government
   subscription/region — quickest way is trying to create each directly
   in the portal, same as how Container Apps availability itself got
   confirmed.
3. Confirm current WinGRC Postgres version against Flexible Server's
   supported versions, and pgvector extension compatibility.
4. Decide whether backend/nginx secrets should route through Azure Key
   Vault instead of Container Apps' built-in secrets store, for parity
   with how seriously secrets handling was treated on the Ubuntu path.
5. VNet integration for the database (this plan uses the simpler "allow
   Azure services" firewall rule as a first pass) — worth tightening
   before a real production customer deployment, matching the
   defense-in-depth posture applied to wl-util-1.
