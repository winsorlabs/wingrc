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
- `WINGRC_PUBLIC_URL=https://YOUR_HOSTNAME` — the URL a browser uses to
  reach this deployment. Without it, invite/reset emails and review-cycle/
  SPRS-reminder notifications fall back to a link-less body instead of a
  working link (see `config.py`'s own docstring on this setting). Read by
  both `backend` and `worker` — recreate both after setting it.
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
docker compose build backend worker nginx   # nginx bundles the frontend build — rebuild it whenever frontend/ changed, not just backend/
docker compose up -d --no-deps backend worker nginx   # migrations run automatically via backend's `alembic upgrade head && exec uvicorn ...` startup command
```

**When these commands run inside a docker-CLI helper container (`claude`'s
setup on wl-util-1: no direct filesystem access to `~wladmin/dev/wingrc`,
only docker-group access), mount the real host path at the identical
path, not an alias.** Confirmed live 2026-09-15: running `docker compose
-p wingrc up -d --no-deps backend worker nginx` from inside a
`docker:27-cli` container started with `-v /home/wladmin/dev/wingrc:/repo
-w /repo` (and the docker socket mounted so compose talks to the *host's*
dockerd) recreated `backend` with a broken bind mount — `backend`'s own
`volumes: [./backend:/app]` line resolves relative to compose's own cwd
(`/repo`, only meaningful inside the helper container's mount namespace),
but the actual mount is created by the *host* daemon, which looked for
`/repo/backend` on the real host filesystem, auto-created it as an empty
directory, and bind-mounted that empty directory over `/app` — so
`alembic upgrade head` failed with "No 'script_location' key found in
configuration" (alembic.ini genuinely wasn't there) and `backend` crash-
looped. `docker compose build` is unaffected by this (the build context is
streamed to the daemon as a tar, not resolved as a host path), which is
why the build step can succeed while `up` then fails — don't take a clean
build as proof the deploy will work. The fix: mount the helper container
at the *same absolute path* as the real host checkout (`-v
/home/wladmin/dev/wingrc:/home/wladmin/dev/wingrc -w
/home/wladmin/dev/wingrc`), so compose's relative-path resolution and the
host daemon's literal path both land on the same real directory. If a
deploy ends up in this state, `docker ps -a` shows the affected service
`Restarting`, and the fix is just re-running `up` correctly — the bad
empty bind-mount directory it auto-created (check `docker inspect
<container> --format '{{range .Mounts}}{{.Source}}{{end}}'` for a path
that doesn't match the real checkout) is orphaned on the host and safe to
remove once nothing references it.

**`--no-deps` is not optional.** Without it, a 2026-09-16 deploy naming
only `backend worker nginx` also recreated `db` and `minio` — undesired
and unexplained for `db` specifically (part of the cause is known for
`minio`: it interpolates `MINIO_SERVER_URL` from the host `.env`, which
Compose may treat as a config change; `db` has no such env-interpolated
setting and recreated anyway). Harmless that time only because both
reattached to their existing named volumes (`wingrc_db_data`/
`wingrc_minio_data` — verify this with `docker volume ls` if it happens
again, and confirm real data survived with a direct query before treating
it as fine) — but recreating the database container on a box holding real
client data must never happen as a side effect of an application-code
deploy. `--no-deps` tells Compose to touch only the services actually
named, full stop, regardless of what it thinks changed.

`db` and `minio` are untouched by an application-code deploy — only
`backend`, `worker`, and `nginx` need to be recreated. **`worker` runs the
same image as `backend`, built from the same `backend/Dockerfile`, but
Compose does not restart it just because `backend` did** — it's a
separate service with its own container, so a deploy that rebuilds and
recreates only `backend` (forgetting `worker`) silently leaves the
scheduler running old code indefinitely; this file exists precisely
because this kind of drift bit us once already (see this section's own
opening note), so don't let it happen to `worker` too. If your deployment
doesn't run `worker` at all (host cron against `wingrc jobs-run-due`
instead — see `cli.py`), there's nothing to rebuild here and this note
doesn't apply.

Confirm exactly which migrations ran from the logs, by revision id,
rather than trusting "migrations applied" as a summary:

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
- **Check any deployment-level setting the new code reads for the first
  time, rather than assuming it's populated.** `deployment_settings.
  msp_org_id` (ADR 0009 M.1) existed for a full month before Slice B's
  Users screen became the first thing to ever read it — nothing had
  reason to check its value before, so its state on any given box was
  genuinely unknown until checked. Query it before deploying anything
  that starts depending on it, and report what's there rather than
  assuming the happy path.
- **A real end-to-end functional check (not just a read) can use a
  throwaway record instead of asking for credentials or touching a real
  one** — clearly named (e.g. `*-verification-throwaway@wingrc.invalid`,
  `is_active=False`), exercised through the real function/endpoint,
  confirmed via a real query (including audit log rows, when the action
  is audit-logged), then deleted outright once confirmed. A throwaway
  record created solely for this check has no history worth preserving,
  so a hard delete is correct here — this is not the ADR 0006
  anonymize-don't-delete case, which exists to protect *real* audit
  history tied to a real account. Prefer calling the underlying function
  directly (`db.SessionLocal()` + the router function, passing a real
  admin's identity as `current_user` for accurate audit attribution) for
  a quick check; for anything where the HTTP authorization layer itself
  is what's in question, go through `TestClient` with `dependency_overrides`
  instead — a bare function call skips `Depends(require_org_access(...))`
  entirely, which looks like a pass even when the caller shouldn't have
  access at all (see `docs/roadmap.md`'s M.7/M.8 Done entry for the exact
  mistake this correction is written from).
- **A cross-org (or otherwise multi-entity) code path may have nothing to
  actually cross on a given box.** If the live data only has one of
  whatever the new code was built to span — one org, one product, one
  anything — say so plainly rather than reporting the read as verified;
  it's the degenerate case, not a positive result. `org_product` was empty
  on this box during the `0039` deploy for the same reason.

- **If this deploy touches `worker` (scheduler.py) or adds/changes a
  registered job:** `docker compose ps worker` should show it running
  (there's no HEALTHCHECK on this one — it has no HTTP endpoint to probe,
  just confirm it hasn't exited/restart-looped), and Administration ->
  Scheduled Jobs (msp_admin) should show the affected job's `last_run`
  advancing on its own interval rather than staying stale. Don't just
  trust that a code change to a job's body took effect — wait for (or
  don't wait past) one real interval and re-check.

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
