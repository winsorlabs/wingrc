# Bench-stack verification

Every non-trivial slice in this project is verified on an isolated Docker
Compose project on wl-util-1 *before* merging its commits to `main` — not
after. `main` is what gets deployed; unverified code there is a live
exposure, not a bookkeeping detail. History backs this up: outbound email,
the scheduler, SPRS submissions, and the periodic-review/attestation slice
all caught a real bug at this stage that no local unit run surfaced (a
quoted-printable assertion, a stale TypeScript fixture, a 40-char alembic
revision id exceeding a column, a `notified_at` window collapse).

If bench access isn't available in a given session, **hold the work on a
branch and say so** — do not merge to `main` planning to verify later. (Two
slices did exactly that on 2026-09-14 — document ingestion and
evidence-download hardening — landing twelve commits, including a
migration, on `main` unverified. They were fixed forward rather than
reverted, but the exception doesn't apply going forward now that access is
resolved — see below.)

## What "isolated" means

- Its own Docker Compose project name (`docker compose -p wingrc_verify_<date>`
  or similar) — never the live project name.
- Its own volumes and network — never attached to the live stack's.
- No published host ports — reachable only from inside the compose network
  or via `docker compose exec`.
- A separate clone of the repo, not the live deployment's working tree.
- The live `dev.wingrc.us` stack (project `wingrc`) is left running and
  untouched throughout. Confirm this explicitly when reporting a
  verification run — check `docker ps` before and after, in addition to
  the bench project's own name being visibly distinct.

## Access: the `claude` account on wl-util-1

Set up 2026-09-14, deliberately, by Jarrod (not self-provisioned):

- A **dedicated Linux account, `claude`**, on wl-util-1 — not Jarrod's own
  login, so `docker ps` / audit logs / shell history make it obvious which
  actions were the assistant's versus a human's.
- **Key-only login** (`adduser --disabled-password`) — no password to leak
  or guess.
- Public key: `~/.ssh/wl-util-1_claude.pub` (comment
  `claude-session-wingrc-verify`) on the machine each Claude Code session
  runs from; installed in `/home/claude/.ssh/authorized_keys` on wl-util-1.
- **Member of the `docker` group.** This is root-equivalent on that host —
  anyone in `docker` can mount the host filesystem into a container and
  write anywhere, including `/etc` and other users' home directories.
  There is no partial version of "can run containers." Jarrod accepted
  this explicitly for wl-util-1 as a rebuildable dev box; **do not assume
  the same tradeoff carries to a production host** — that would need its
  own explicit decision.
- **The Docker socket is shared.** The `claude` account can see and
  control the live `dev.wingrc.us` containers, not just its own bench
  projects — `docker ps` from this account lists everything on the host.
  Isolation from the live stack is discipline (a distinct project name,
  own volumes, no published ports, working in a separate clone under
  `/home/claude`, never Jarrod's checkout), not something the account
  permissions enforce for you.

### Connecting

An `~/.ssh/config` entry makes this a one-liner instead of re-deriving the
username/key each session:

```
Host wl-util-1
    HostName 10.10.24.35
    User claude
    IdentityFile ~/.ssh/wl-util-1_claude
    IdentitiesOnly yes
```

Then: `ssh wl-util-1`. Note the config lives per-machine/per-session-
environment — a new session on a different host won't have it until
created there too; check `ssh -o BatchMode=yes wl-util-1 echo ok` early
rather than assuming.

Work in a fresh clone under `/home/claude` (e.g. `/home/claude/bench/<date>-<slice>`),
never Jarrod's own checkout — his working tree must not be disturbed by
bench work.

## Running a verification

1. `git clone` (or `git pull` an existing bench clone) into
   `/home/claude/bench/<name>`.
2. `docker compose -p wingrc_verify_<date> up -d --build` — own project
   name, no port publishing overrides needed since nothing outside the
   compose network needs to reach it; use `docker compose exec` for
   shell access into `backend`/`db`/etc.
3. Backend: `docker compose -p wingrc_verify_<date> exec backend pytest -q`
   (full suite, not just `-m "not integration"` — that's the local-only
   subset), `ruff check .`, confirm any new Alembic migration applies
   cleanly (`alembic upgrade head` is what `docker compose up` already
   runs on backend start — check its logs, and check migration source for
   whether it's purely additive or touches existing rows).
4. Frontend: a throwaway `node:24-alpine` (or matching) container running
   the frontend's own `npm ci && npm test && npm run build` unmodified —
   don't hand-roll a different build path.
5. Any slice-specific load measurement or manual walkthrough the slice's
   own task calls for.
6. Tear down: `docker compose -p wingrc_verify_<date> down -v` — confirm
   the live `wingrc` project's containers are still running unaffected
   before and after.
