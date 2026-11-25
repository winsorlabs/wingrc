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
- Nothing written outside the bench's own folder (2026-11-19). The one
  host path compose binds, the snapshot directory, is pointed into the
  clone in step 2. Without that, every `up` creates
  `/var/lib/wingrc-snapshots/<project>` as root.
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
2. **Give the clone its own `.env`** (since 2026-11-17 compose refuses to
   start without every required value, and names the missing one):

   ```bash
   cp .env.example .env && chmod 600 .env
   sed -i 's/^WINGRC_ENVIRONMENT=$/WINGRC_ENVIRONMENT=development/' .env
   deploy/generate-env.sh
   ```

   Then point the snapshot directory into the clone, before the first
   `up`:

   ```bash
   mkdir -p .bench/snapshots
   echo "WINGRC_SNAPSHOT_DIR=$PWD/.bench/snapshots" >> .env
   ```

   Compose binds `${WINGRC_SNAPSHOT_DIR:-/var/lib/wingrc-snapshots/<project>}`
   into the backend. Docker creates a missing bind source as root, so
   without this line each bench's `up` left an empty root-owned
   directory in `/var/lib`, seven of them by 2026-11-19. Making the
   directory first, as `claude`, keeps it `claude`'s. A bench that takes
   a snapshot there still gets files owned by the image's user, which
   `snapshot.sh` sets; see step 7.

   Fresh random values, printed by name only. Never copy live's `.env`:
   its values are live's credentials. A bench restored from a live
   snapshot needs live's Fernet key to read stored credentials; that is
   `snapshot-verify`'s concern, by key label, not this file's.
3. `docker compose -p wingrc_verify_<date> up -d --build` — own project
   name, no port publishing overrides needed since nothing outside the
   compose network needs to reach it; use `docker compose exec` for
   shell access into `backend`/`db`/etc.
   **Bring up `backend minio worker`, not the whole file:** `nginx`
   publishes host ports 80/443 and cannot start beside the live stack, and
   `minio` is not a dependency of `backend`, so naming only `backend` leaves
   the storage-backed tests failing on name resolution (2026-10-07).
   **The containers run the image's baked code (since 2026-10-08), not the
   clone.** After changing a file in the bench clone, `up -d --build
   backend worker` before re-running tests -- an edit is invisible until
   then. A negative control (old file swapped in) needs the same rebuild,
   then `git checkout --` and a rebuild to restore.
4. Backend, with the test database passed for this run only:

   ```bash
   docker compose -p wingrc_verify_<date> exec -T backend sh -c \
     'base="${WINGRC_MIGRATION_DATABASE_URL:-$WINGRC_DATABASE_URL}"
      WINGRC_TEST_DATABASE_URL="${base%/wingrc}/wingrc_test" WINGRC_MIN_TESTS_RUN=1500 \
      WINGRC_AUDIT_COVERAGE=1 pytest -q'
   ```

   `WINGRC_AUDIT_COVERAGE=1` is what CI's `integration` job sets. Without it
   the audit check's companion is off, and a route a new test drives,
   still listed in `NOT_REACHING_A_WRITE`, passes here and fails in CI
   (#137's first run, 2026-11-20).

   Compose no longer puts `WINGRC_TEST_DATABASE_URL` in the container
   (2026-11-18): it is the owner's, a superuser, and it sat in every
   deployment's environment, live's included, for nothing. Without it every
   integration test skips, so the count floor is part of the command: a
   run that skipped them fails instead of passing. The floor sits between
   the two counts: about 840 tests run with no database and about 2150
   with one (2026-11-17). CI's 500 would not tell them apart here. Raise it
   as the suite grows; never lower it to make a run pass. The URL is built from
   the owner URL already in the container, so nothing is typed.
   **That makes the tests connect as the owner, a superuser.** That is
   right for tests: they need DDL to build the test database, and the
   fixture switches to `wingrc_app` per request. But it works only
   because the bench's backend holds the owner, the condition behind
   `docs/roadmap.md`'s "The bench runs as the owner, so it cannot see an
   RLS defect". This line changes when that is fixed: with the owner URL
   out of the backend, the fallback names `wingrc_app`, which cannot
   create the test database. That entry's cost list says how.
   (full suite, not just `-m "not integration"` — that's the local-only
   subset). **The full suite in one process, never a per-file run.**
   Running a file in isolation is a different experiment, not a cheaper
   version of this one: order- and state-dependent defects are invisible to
   it by construction. A bundle-export nondeterminism turned `main` red
   (run #307, 2026-09-28) after passing per-file verification — reproduced
   afterwards both ways, failing in the suite and passing alone. If a slice
   touches a query, an ordering, a fixture, or anything a later test reads,
   a green targeted run says nothing. Also run `ruff check .`, confirm any
   new Alembic migration applies
   cleanly (`alembic upgrade head` is what `docker compose up` already
   runs on backend start — check its logs, and check migration source for
   whether it's purely additive or touches existing rows).
5. Frontend: a throwaway `node:24-alpine` (or matching) container running
   the frontend's own `npm install && npm test && npm run build`
   unmodified — don't hand-roll a different build path. **`npm install`,
   not `npm ci`**: there is no committed `package-lock.json`, and
   `deploy/nginx/Dockerfile` itself runs `npm install`, so `npm ci` both
   fails and would not be the real build path anyway. (Whether to commit a
   lockfile and move the image to `npm ci` is an open decision — it is what
   would actually pin transitive dependencies for a compliance product, and
   it changes the deploy path, so it needs its own call rather than being
   slipped in.)
   Mount the **repo root**, not just `frontend/`: the Markdown renderer's
   parity test reads `backend/tests/fixtures/markdown_corpus.json`, which is
   deliberately shared with the backend suite rather than duplicated.
   **Run it as `claude`, not as root** (2026-11-19):

   ```bash
   docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
     -v "$PWD:/repo" -w /repo/frontend node:24-alpine \
     sh -c 'npm install > install.log 2>&1 && npm test > vitest.log 2>&1; echo "vitest exit $?"; npm run build > build.log 2>&1; echo "build exit $?"'
   grep -E "FAIL|Test Files|Tests " frontend/vitest.log
   ```

   **The run goes to a file before anything reads it** (2026-11-24).
   Twice a vitest failure was seen only as a summary line, because the
   command was piped through a filter that kept the summary and cut the
   `FAIL` lines, and neither failure came back (`docs/roadmap.md`, "One
   unexplained frontend failure, kept as a data point"). A `grep` of the
   log afterwards loses nothing; a `grep` of the stream loses the rest.

   **If `vitest.log` shows a failure, run the tests once more before
   anything else**, to a second file, and keep both:
   ```bash
   docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
     -v "$PWD:/repo" -w /repo/frontend node:24-alpine \
     sh -c 'npm test > vitest-2.log 2>&1; echo "vitest exit $?"'
   ```
   That run is the test of the roadmap's hypothesis: the first run after
   a fresh install fails a test that the second run passes. Record both
   logs' `FAIL` lines in that entry, whichever way it comes out.

   The image runs as root by default, so `node_modules` and `dist` came out
   root-owned. `claude` cannot remove them, so each bench left Jarrod a
   `sudo` cleanup, twice by 2026-11-19, `/home/claude/bench/t1fe` the
   second. `HOME=/tmp` gives npm a writable cache, because the image's home
   is root's. Measured on wl-util-1: install, vitest and the build pass,
   no file in the tree is left owned by anyone but `claude`, and `claude`
   removes it unaided.
6. Any slice-specific load measurement or manual walkthrough the slice's
   own task calls for.
7. Tear down: `docker compose -p wingrc_verify_<date> down -v` — confirm
   the live `wingrc` project's containers are still running unaffected
   before and after. **Compare the host paths compose binds, too**, read
   from the compose config rather than from a list kept by hand:

   ```bash
   binds() { docker compose -p wingrc_verify_<date> config --format json \
     | python3 -c 'import json,sys
   for s in json.load(sys.stdin)["services"].values():
       for v in s.get("volumes", []):
           if v.get("type") == "bind": print(v["source"])' | sort -u; }
   binds | while read -r p; do [ -e "$p" ] && echo "present $p" || echo "absent $p"; done
   ```

   Run it before the first `up` and after `down -v`, and diff the two. A
   path that was absent before and is present after is something the
   bench left on the host. The config is printed only through the filter,
   because the whole config holds the `.env`'s secrets.

   **Why this check exists:** until 2026-11-19 the comparison covered
   containers, volumes and networks. It reported "host lists unchanged"
   while seven root-owned directories sat in `/var/lib/wingrc-snapshots/`,
   one for each bench since 2026-11-18 (`docs/roadmap.md`, "The bench's
   teardown check verified less than it reported").

   **The snapshot directory, with step 2's line,** is the clone's
   `.bench/snapshots`. Empty, `rm -rf` of the clone takes it. If the
   bench took a snapshot, `snapshot.sh` made the directory the image
   user's, mode 0700, and `claude` cannot remove what is inside it.
   **Ask Jarrod; do not remove it through a container.** This step used
   to say to do exactly that, with
   `docker run --rm -v /var/lib/wingrc-snapshots:/s postgres:18 rm -rf …`.
   That works because the `docker` group is root-equivalent on this host.
   The capability exists and the permission does not, and the difference
   is the whole point: a file permission that gives way whenever docker
   is to hand is not one. `docs/roadmap.md`'s "What claude can do on
   wl-util-1" has the rule. A snapshot holds the bench's data, so leaving
   one for Jarrod costs nothing but the ask.
   **What claude cannot clear is one command for Jarrod:** see "After a
   bench cycle" below.

   **A helper container from an image that declares a `VOLUME`
   (`postgres:18` does) leaves an anonymous volume unless it is run with
   `--rm`.** `docker run -d` followed by `docker rm -f` without `-v` leaves
   one. On 2026-11-02, item S's disk-full test left three this way, empty
   and unused; the before-and-after volume list is what caught them.
   Compare the whole `docker volume ls` before and after, not only the
   bench project's names.

## After a bench cycle: the one cleanup command

Some of what a bench leaves on wl-util-1 is not `claude`'s to remove.
Rather than a list after each cycle, Jarrod runs one command on
wl-util-1, from live's checkout once it carries the script:

```bash
scripts/bench-cleanup.sh --dry-run     # lists what it would do; no sudo needed
sudo scripts/bench-cleanup.sh          # does it
```

It covers two places, and nothing else:

| Path | Who owns it, and why | What the command does |
|---|---|---|
| `/var/lib/wingrc-snapshots/wingrc_verify_*` | Root made the directory: compose's bind source and `snapshot.sh`'s default when a bench did not export `WINGRC_SNAPSHOT_DIR` into its clone. If the bench took a snapshot, `snapshot.sh` then gave it to uid 10001, the image's `wingrc` user (no name on the host), at 0700, because a dump holds every tenant's records. `claude` cannot write `/var/lib/wingrc-snapshots`. | **Removes it.** It is bench data. Never `wingrc` (live's) or any name not starting `wingrc_verify_`. |
| Under `/home/claude/bench`, every topmost entry `claude` does not own | Uid 10001: a clone's `.bench/snapshots` after a snapshot, for the same reason as above. Root: `node_modules`, `dist`, logs and screenshots that a container run as root wrote into a clone, before step 5 ran the frontend as `claude` (2026-11-19). | **Hands it back to `claude`** (`chown -R`), and removes nothing: some are a bench's only copy of its output. `claude` then removes what it should, in its own directory, unaided. |

The first dry run, 2026-11-24, listed 274 entries: 1 under `/var/lib`,
and 273 under the bench home, 4 of them uid 10001's (two snapshot
directories, and two output folders of 2026-11-12's bench) and 269
root's, from benches dated 2026-09-16 to 2026-11-19.

It skips anything a running container mounts, so a bench still running
is left alone, and it never follows a symlink. It is not a boundary
against `claude`, whose `docker` group is root-equivalent here already:
it is the permission, used by the person who holds it, in place of
`claude` using the capability. `tests/test_bench_cleanup.py` holds live's
directory, another project's, a symlink and a mounted path.
