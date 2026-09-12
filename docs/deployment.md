# Deployment guide

A baseline walkthrough for standing up a new WinGRC instance: clone, configure,
bring the stack up behind nginx, and get HTTPS working. Written to be generic —
follow it for any deployment, not a specific server. Replace every
`YOUR_HOSTNAME` / `YOUR_EMAIL` placeholder with real values as you go.

Per [ADR 0005](adr/0005-deployment-topology-per-msp-not-shared-saas.md), each
WinGRC deployment is a dedicated instance for one MSP (and the client
organizations they serve) — not shared multi-tenant infrastructure. Each
deployment gets its own hostname and TLS configuration, which is what this
guide sets up.

## Prerequisites

- A Ubuntu server (local or cloud — your choice) with **Docker**, **Docker
  Compose**, and **git** installed. Nothing else is assumed.
- Two DNS names pointed at this server: `YOUR_HOSTNAME` (the app) and
  `storage.YOUR_HOSTNAME` (evidence/logo downloads). Both are required — see
  the comment block at the top of `deploy/nginx/nginx.conf` for why a
  separate storage subdomain exists rather than a path on the main hostname.
- Access to your DNS provider to create TXT records (for certificate
  issuance) and A/AAAA records for the two hostnames above.

## 1. Clone and configure

```bash
git clone <this-repo-url> wingrc
cd wingrc
cp .env.example .env
```

Edit `.env`:

- `WINGRC_ENVIRONMENT=production` — required for any HTTPS deployment; this
  is what puts the `Secure` flag on session cookies (see
  `backend/app/auth.py`). Do not skip this.
- `WINGRC_STORAGE_PUBLIC_ENDPOINT=https://storage.YOUR_HOSTNAME`
- `MINIO_SERVER_URL=https://storage.YOUR_HOSTNAME`
- Set real values for `WINGRC_STORAGE_ACCESS_KEY` / `WINGRC_STORAGE_SECRET_KEY`
  (don't ship with the `wingrc` / `wingrc-dev-secret` defaults) and any other
  secrets called out in `.env.example`.

## 2. Obtain a TLS certificate (DNS-01)

Let's Encrypt's DNS-01 challenge is used so no port needs to be open before
the stack is even running. Certs land in a named Docker volume,
`wingrc_certs`, which nginx later mounts read-only. Pick whichever path
matches your DNS provider:

### 2a. Automated, if your DNS provider supports a scoped API token

Most providers have a certbot DNS plugin (`certbot-dns-cloudflare`,
`certbot-dns-route53`, `certbot-dns-digitalocean`, etc.). General shape:

```bash
docker volume create wingrc_certs
docker run --rm \
  -v wingrc_certs:/etc/letsencrypt \
  -v "$(pwd)/dns-credentials.ini:/dns-credentials.ini:ro" \
  certbot/dns-<your-provider> certonly \
  --dns-<your-provider> --dns-<your-provider>-credentials /dns-credentials.ini \
  -d YOUR_HOSTNAME -d storage.YOUR_HOSTNAME \
  --agree-tos -m YOUR_EMAIL --no-eff-email
```

Keep `dns-credentials.ini` out of git (it holds the scoped API token) and
scope the token to DNS-edit only, nothing broader.

### 2b. Manual, if your provider doesn't support a scoped token

Some providers (e.g. DNSimple on personal-tier plans) don't offer scoped API
tokens, so an unscoped token isn't worth issuing just for this. Run certbot
interactively instead and add the TXT record it gives you by hand:

```bash
docker volume create wingrc_certs
docker run -it --rm \
  -v wingrc_certs:/etc/letsencrypt \
  certbot/certbot certonly \
  --manual --preferred-challenges dns \
  -d YOUR_HOSTNAME -d storage.YOUR_HOSTNAME \
  --agree-tos -m YOUR_EMAIL --no-eff-email
```

Certbot pauses and prints a TXT record value for `_acme-challenge.YOUR_HOSTNAME`
(and again for the storage subdomain — one certificate, two names, two TXT
records). Add each in your DNS provider's dashboard, wait for propagation
(check with `dig TXT _acme-challenge.YOUR_HOSTNAME`), then continue.

Either way, this issues one certificate covering both hostnames, which is
what `deploy/nginx/nginx.conf` expects (`/etc/letsencrypt/live/YOUR_HOSTNAME/`).

## 3. Point the nginx config at your hostname

Edit `deploy/nginx/nginx.conf` and replace every `YOUR_HOSTNAME` with your
real hostname.

## 4. Bring up the stack

```bash
docker compose up --build -d
```

The `wingrc_certs` volume must already exist (step 2) — nginx's compose
service declares it `external: true` and won't create it for you.

## 5. Verify HTTPS end-to-end

- Visit `https://YOUR_HOSTNAME` — the app loads with a valid certificate (no
  browser warning).
- Log in, then attach or open a piece of evidence and confirm the download
  actually completes. This exercises the presigned-URL signature path
  through `storage.YOUR_HOSTNAME`, not just "nginx returns 200" — a broken
  Host-header or path mismatch here fails as a signature error on download,
  not as a proxy error. Don't skip this check.

## 6. Renewal (manual, ~every 60 days)

Let's Encrypt certs are valid 90 days. No renewal cron or systemd timer is
set up by this repo — re-run the same certbot command from step 2 roughly
every 60 days, then:

```bash
docker compose restart nginx
```

so nginx picks up the refreshed files from the volume. Set yourself a
recurring reminder; there's no automated fallback if it's missed.

## 7. Updating an existing deployment

Written 2026-09-12 after the first real deploy that carried a
data-modifying migration against a live tenant (the `Product.is_published`
backfill, migration `0039`) — three slices had accumulated on `main`
without a documented update procedure, which is exactly the kind of drift
that turns a routine deploy into an improvised one. Follow this for every
update from here on rather than re-deriving it.

### 7a. Back up first — non-negotiable for any deploy carrying a migration

Never skip this because "it's just a schema change" — `0039` looked like a
trivial `UPDATE ... SET is_published = true`, and it was exactly the
migration that could have silently emptied a live tenant's Tools panel if
the backfill's `WHERE` clause were wrong. Back up before every migrating
deploy, not just ones that look risky in advance.

```bash
docker exec <backend-container> sh -c \
  'pg_dump --format=custom --file /backups/pre-deploy-<label>-$(date -u +%Y%m%dT%H%M%SZ).dump "postgresql://<user>:<password>@db:5432/<dbname>"'
```

Notes:
- Use a **plain** `postgresql://` URL, not the app's `postgresql+psycopg://`
  SQLAlchemy driver string — `pg_dump` doesn't understand the `+psycopg`
  suffix and fails closed with a confusing "socket not found" error if you
  paste `$WINGRC_DATABASE_URL` in directly.
- `--format=custom` (not plain SQL), matching `cli.py`'s own
  `_preflight_backup` convention for `reset-dev`: `pg_restore`-loadable, so
  a bad migration can be recovered by loading into a scratch database and
  selectively restoring rows, not just a destructive wholesale restore
  over the live database.
- The destination is `/backups` inside the backend container, which maps
  to the `backend_backups` named volume — it survives the container
  recreate this same deploy is about to do.
- **Verify the dump, don't just trust exit code 0.** A failed connection
  can still leave a zero-byte file on disk before erroring. Confirm both:
  ```bash
  docker exec <backend-container> ls -la /backups/
  docker exec <backend-container> pg_restore --list /backups/<file>.dump | head -20
  ```
  A real dump lists real TOC entries (tables, functions, schemas) — an
  empty or truncated file, or a `pg_restore` error, means the backup
  didn't work and you do not have a safety net yet.

### 7b. Record before-state for anything a migration will touch

If the migration modifies data (not just schema), capture a query result
you can diff against after, not just "run it and see." For `0039` this
was `product.is_published` and every `org_product` row's `(org, product,
status)` — the exact two things the migration could get wrong, and the
exact two things a tenant would notice first.

### 7c. Deploy

```bash
cd ~/dev/wingrc
git pull --ff-only
docker compose build backend nginx   # nginx bundles the frontend build — rebuild it whenever frontend/ changed, not just backend/
docker compose up -d backend nginx   # migrations run automatically via backend's `alembic upgrade head && exec uvicorn ...` startup command
```

`db` and `minio` are untouched by an application-code deploy — only
`backend` and `nginx` need to be recreated. Confirm exactly which
migrations ran from the logs, by revision id, rather than trusting
"migrations applied" as a summary:

```bash
docker logs <backend-container> 2>&1 | grep -A1 'Running upgrade'
```

Then confirm the container reports healthy (`docker ps`) before verifying
anything else — an unhealthy backend makes every subsequent check
meaningless.

### 7d. Verify

- Re-run the §7b before-queries and diff the output — don't eyeball it.
- Exercise the specific tenant-facing path the migration was written to
  protect, not just "the app loads." For `0039` that meant confirming a
  product's read paths (library list, tenant activation list) still
  resolve correctly against real data.
- If a real user login is needed for the check (not just a read-only
  DB/endpoint verification) and you don't hold that credential, ask rather
  than creating or resetting an account on a live deployment to get past
  it — that's an account-modifying action on production, not a deploy
  step.

### 7e. If a data-modifying migration did the wrong thing

Stop. Do not patch forward with another migration written under pressure.
Restore the `pre-deploy-*.dump` from 7a, report exactly what the migration
did versus what was expected, and wait for a decision before touching it
again. A half-corrected table is worse than a restored one.

## Follow-ups not covered by this baseline

- **HSTS** — commented out in `deploy/nginx/nginx.conf`. Enable once this
  deployment's HTTPS has run reliably for a while; enabling it before that
  risks locking out access on a cert hiccup, since browsers cache it and
  there's no server-side way to un-cache early.
- **PDF rendering, connectors, auth/RBAC polish, etc.** — see
  [docs/roadmap.md](roadmap.md) for the application feature roadmap; this
  guide only covers infrastructure.
