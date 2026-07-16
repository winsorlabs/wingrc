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

## Follow-ups not covered by this baseline

- **HSTS** — commented out in `deploy/nginx/nginx.conf`. Enable once this
  deployment's HTTPS has run reliably for a while; enabling it before that
  risks locking out access on a cert hiccup, since browsers cache it and
  there's no server-side way to un-cache early.
- **PDF rendering, connectors, auth/RBAC polish, etc.** — see
  [docs/roadmap.md](roadmap.md) for the application feature roadmap; this
  guide only covers infrastructure.
