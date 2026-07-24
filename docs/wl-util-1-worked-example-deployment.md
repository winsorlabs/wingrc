# Worked example: hardening and deploying WinGRC on wl-util-1

A concrete, real walkthrough of taking an existing WinGRC install — Ubuntu
Server, Docker, git, on a private LAN behind NAT — from an over-exposed dev
setup to a hardened, HTTPS-served reference deployment. Written from the
actual session, including the mistakes, not just the idealized happy path.
Intended as the companion "full worked example" to the generic
`docs/deployment.md`, using this exact machine as the example deployment.

## Starting point

wl-util-1: Ubuntu Server, Docker + Docker Compose, git checkout of the
WinGRC repo, no reverse proxy, `npm run dev` frontend container, local auth
already working (session cookies, MFA, lockout — verified separately before
this work started).

## Step 0: audit before touching anything

Before making any changes, we checked what was actually exposed:

```
sudo ufw status verbose
sudo firewall-cmd --state 2>/dev/null && sudo firewall-cmd --list-all
sudo iptables -L -n -v
sudo ss -tulnp
```

Findings: `ufw` was inactive (no host firewall at all), and Docker had
published Postgres (5432), MinIO's API and console (9000/9001), the FastAPI
backend (8000), and the Vite dev server (5173) to *all* host interfaces —
not just the internal Docker network. The box sat behind NAT with no inbound
port forwards, so this wasn't internet-reachable, but that's the network
edge doing the work, not the app — anything else on the same LAN had a
direct, unauthenticated-at-the-network-layer path to the database and
object store.

**Lesson:** run this audit on any existing deployment before assuming
"it's fine, it's internal" — Docker Compose's `ports:` mappings publish to
`0.0.0.0` by default, not to the Docker-internal network only.

## Step 1: choose a TLS approach that fits a firewalled, non-public box

Standard Certbot (HTTP-01 challenge) needs port 80 reachable *from the
internet* for domain validation — not compatible with a box that has no
inbound port forwards. DNS-01 challenge instead proves domain control via a
DNS TXT record, requiring zero inbound exposure — only outbound HTTPS from
the box to Let's Encrypt's ACME endpoint.

Automating DNS-01 normally means giving Certbot an API token for your DNS
provider. Our provider (DNSimple) doesn't offer scoped tokens on personal
plans — only full-account tokens. Rather than store an unscoped,
all-zones-plus-registrar-plus-billing credential on the server just for
renewal convenience, we went with Certbot's **manual** DNS-01 mode instead:
zero credentials stored anywhere, at the cost of renewal being a manual,
recurring (~60 day) task instead of automatic.

**Lesson:** don't assume DNS-01 automation is free from a security
standpoint — check what your provider's tokens can actually be scoped to
before wiring one into a server. An unscoped token you don't need is a
worse trade than a manual renewal chore, if your provider doesn't support
scoping.

## Step 2: nginx as the sole host-facing entry point

Added an `nginx` service to `docker-compose.yml` as the only container
publishing ports to the host (80/443). Removed host port publishing for
`db`, `minio`, and `backend` entirely — only the internal Docker network
needs to reach them now.

## Step 3: the MinIO routing problem (verified before implementing)

Evidence and logo downloads use S3 presigned URLs — the browser hits MinIO
*directly*, not through the backend. That meant MinIO's host port couldn't
just be removed without breaking those links.

The obvious fix — proxy `/storage/` on the main hostname to MinIO — doesn't
work: a presigned URL's signature covers the exact path and Host header it
was signed against. Strip the `/storage` prefix before forwarding and
MinIO's signature check fails (different path than was signed). Keep the
prefix and MinIO's own bucket router reads "storage" as a literal
(nonexistent) bucket name. This was confirmed with an offline boto3 test
before writing any nginx config, not assumed.

**Fix:** a dedicated subdomain (`storage.dev.wingrc.us`) with a full
pass-through proxy to MinIO — no path rewriting, Host header preserved.
MinIO's own `MINIO_SERVER_URL` was set to match, so it signs against the
same host nginx proxies under. Cost: two DNS names on one certificate
instead of one, meaning two manual TXT records per issuance/renewal instead
of one.

**Lesson:** don't reverse-proxy S3-compatible presigned-URL storage with a
path prefix. Use a subdomain with an unmodified pass-through.

## Step 4: frontend — production build, not a proxied dev server

The frontend container ran `npm run dev --host` with **no source
bind-mount** — meaning hot-reload was never actually wired to live edits in
the first place, even before this work. Rather than proxy a dev server
through nginx (which would also need WebSocket-upgrade handling for Vite's
HMR), we switched the deployment to build the frontend as a real production
bundle (`npm run build`) served as static files directly by nginx, via a
multi-stage Dockerfile. No dev server runs in the deployed stack at all.

This is also what surfaced a real, previously-invisible bug: `npm run
build` runs a full TypeScript check (`tsc -b`) that the dev server never
enforced. It turned out this project had never had a real production build
run against it before. Two errors came up:

- A missing `vite-env.d.ts` (standard Vite/TS gap, mechanical fix).
- `ControlDrawer.tsx` had its own local, narrower type plus a mapper
  function that silently dropped a real backend field
  (`control_discussion`) when converting the API response. Net effect: the
  "About this control" discussion panel had rendered nothing since it was
  written. A genuine functional bug, not a type-only nitpick — found purely
  as a side effect of finally running a real build.

**Lesson:** if your dev workflow never runs the actual production build
command, you don't know if the production build works. Moving to a real
build as part of hardening is itself a useful test, independent of the
deployment goal.

## Step 5: restore contributor dev workflow separately

Removing the frontend dev-server container broke `CONTRIBUTING.md`'s
documented dev loop. Fix: a `docker-compose.override.yml.example` (copied
locally to `docker-compose.override.yml`, gitignored, auto-loaded by
Compose on top of the base file) that restores a dev-server frontend — this
time with an actual source bind-mount, fixing the original gap, plus an
anonymous volume on `/app/node_modules` to stop a host bind-mount from
shadowing the container's own Linux-native `node_modules` (a real
cross-platform gotcha on a Windows host with a Linux container). This stays
local-only — never present on a real deployment like wl-util-1, which runs
the base compose file alone.

## Step 6: issuing the certificate

```
docker volume create wingrc_certs
docker run -it --rm \
  -v wingrc_certs:/etc/letsencrypt \
  certbot/certbot certonly \
  --manual --preferred-challenges dns \
  -d dev.wingrc.us -d storage.dev.wingrc.us \
  --agree-tos -m <email> --no-eff-email
```

Certbot pauses to request a TXT record per domain, created by hand in
DNSimple's dashboard. One run only prompted for one of the two domains —
alarming at first, but it turned out to be Let's Encrypt reusing a still-
valid authorization from an earlier attempt on the same ACME account, not a
bug. **Verified, not assumed:**

```
docker run --rm -v wingrc_certs:/etc/letsencrypt alpine/openssl \
  x509 -in /etc/letsencrypt/live/dev.wingrc.us/fullchain.pem -noout -text \
  | grep -A1 "Subject Alternative Name"
```

confirmed both `dev.wingrc.us` and `storage.dev.wingrc.us` were present on
the issued certificate before proceeding.

**Lesson:** if a multi-domain DNS-01 request only challenges some of the
domains, don't assume it's fine — check the actual certificate's SAN list
directly.

## Step 7: bringing the stack up — the orphan container trap

```
docker compose up -d --build
```

succeeded, but warned about an "orphan container" (`wingrc-frontend-1`) —
the *old* frontend service, no longer defined in `docker-compose.yml` after
Step 4. Compose doesn't stop containers for services it no longer knows
about unless told to. That orphan was still running with its *old* port
binding — meaning port 5173 was still exposed to the LAN, silently
undercutting the whole hardening effort. Fixed with:

```
docker compose up -d --remove-orphans
```

**Lesson:** after removing a service from `docker-compose.yml`, always use
`--remove-orphans` on the next `up` and re-run the exposure audit from
Step 0 — don't trust that removing a service definition removed the
running container.

## Step 8: the stale `.env` trap

Everything built and deployed cleanly, but evidence downloads failed
outright and uploads failed with a generic "Failed to fetch." The download
failure's browser URL bar gave it away: still pointing at the old
`http://10.10.24.35:9000` — the raw LAN IP, not the new
`https://storage.dev.wingrc.us`.

Root cause: `.env` is (correctly) gitignored, so it's never touched by
`git pull` — it's a server-local file. `docs/deployment.md` documents what
values it *should* have, but nobody had actually gone back and edited the
*existing*, pre-hardening `.env` file on the server with the new values.
`WINGRC_ENVIRONMENT` was still `development`, `WINGRC_STORAGE_PUBLIC_ENDPOINT`
still pointed at the old IP, and `MINIO_SERVER_URL` didn't exist in the file
at all. Fixed directly on the server, then:

```
docker compose up -d
```

to recreate the `backend` and `minio` containers with the corrected values
(no rebuild needed, just a config-driven recreate).

**Lesson:** `.env` changes documented in a generic deployment guide don't
apply themselves to an *existing* deployment's real `.env` file. This is a
manual step on every existing box being migrated to a new config, easy to
forget precisely because git makes everything else feel automatically
in sync.

## Step 9: host firewall as a second layer

Docker's own port-publishing was already fixed (Steps 2-7), but no
host-level firewall existed at all. Added one as defense-in-depth — not
because anything was still open, but because a *future* accidental
`ports:` addition to `docker-compose.yml` would otherwise bypass everything
done so far. `ufw` genuinely does gate ports that `docker-proxy` binds
directly on the host's interfaces, so this isn't just a redundant second
lock.

```
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw enable
```

**Critical safety step, always in this order:** allow SSH *before* enabling
a deny-by-default firewall, and open a **second**, separate SSH session to
confirm connectivity survives before closing the original one. Enabling a
deny-incoming firewall without SSH already allowed is the classic way to
lock yourself out of a remote box with no other access path.

## Final verification

Re-ran the Step 0 exposure audit (`ss -tulnp`) — only 22, 80, 443, and
loopback-only internals listening, both before and after firewall
enablement. Then the actual functional check, not just "nginx returns 200":
loaded `https://dev.wingrc.us`, confirmed a clean trusted certificate,
logged in, opened a control to confirm the (now-fixed) discussion panel
rendered, and completed a real evidence download and upload — the one step
that actually exercises the presigned-URL signature path through
`storage.dev.wingrc.us`, which a simple connectivity check would have
missed entirely.

## Summary of gotchas for anyone repeating this

1. Docker Compose `ports:` publish to all interfaces by default — audit
   before assuming "internal only."
2. Check your DNS provider's token scoping before automating DNS-01;
   manual mode is a legitimate, more secure choice if scoping isn't
   available.
3. Never path-prefix-proxy S3-compatible presigned-URL storage — use a
   subdomain.
4. If you've never run the real production build, you don't know it works
   — running it for the first time during a deploy can surface real,
   previously-invisible bugs.
5. `--remove-orphans` after removing a compose service, every time.
6. `.env` is server-local and gitignored — deployment guide changes don't
   apply themselves to an existing box's real file.
7. Verify SSH survives a firewall change in a second session before
   closing the first.
