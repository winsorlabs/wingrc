# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read this first, every session. It captures *intent*, not just file structure.

---

## What WinGRC is

An open, AGPL-licensed CMMC (NIST 800-171 Rev 2 / CMMC L2) GRC platform
MSPs deploy to collaborate with their client organizations on compliance. It does
what high-priced commercial GRC tools do —
control-by-control met/not-met, SPRS scoring, AI-drafted implementation
statements, RACI/CRM, evidence storage, full assessment-bundle export — but
free, MSP-first, and deployable anywhere (commercial Azure, GCC High, on-prem,
air-gapped).

The **magic** is tool-driven control pre-population: select the security tools a
tenant runs, and the platform pre-populates the controls those products satisfy
from a curated **baseline library**, then queues only the evidence collection
still needed. The lists are one *output*, not the product.

> Do NOT over-index on the scope/lists module. That is one input pillar plus one
> output. The product is the assessment engine described below.

---

## The five layers

1. **Reference (shared across orgs):** the control catalog (800-171A
   assessment objectives + SPRS point weights) and the **product baseline
   library** (per-product: which objectives it covers when configured, the
   assumed config, the evidence spec, the responsibility split).
2. **Org setup:** select tools in place; define scope (spreadsheet upload OR
   Liongard/RMM API/MCP).
3. **Assessment core:** per-objective control state (met / not met / partial /
   N/A / inherited), responsibility (RACI → the MSP-vs-customer CRM), evidence.
4. **Generation:** AI-drafted implementation statements, grounded in baseline +
   scope + evidence, human-reviewed.
5. **Deliverables (the bundle):** point-in-time Lists, SSP, baseline docs,
   POA&M, CRM, and the SPRS score.

---

## Hard rules (these define correctness)

Violating these produces incorrect assessments. Enforce them in every review.

- **Candidates, never auto-met.** Imports and document ingestion *propose*; an
  engineer confirms. Nothing is "met" without confirmed config + attached
  evidence. An automated feed must never silently move the audit boundary.
  Status transitions: magic loop → `pending_evidence`; human confirms → `met`.
  Never skip `pending_evidence`.

- **Never auto-credit a vendor CRM.** A vendor CRM lists controls the product
  *touches*, most of which the customer still owns. Read the responsibility
  text: if the product says "the customer's IdP does this" (e.g., the entire IA
  family for RocketCyber), classify it `customer_owns` and route it to the
  product that actually owns it — do NOT credit the vendor. See
  `backend/baselines/rocketcyber.yaml` for the worked example.

- **Never credit `coverage_basis = platform_only`.** The magic loop
  (`engine.py:_run_loop`) explicitly excludes baseline controls where
  `coverage_basis == 'platform_only'`. These are controls the vendor satisfies
  for its *own* infrastructure, not the customer's CUI systems. Do not remove
  this filter.

- **Minimize evidence — this is a first-class requirement.** Evidence sprawl is
  why MSPs hate GRC tools. One artifact can satisfy many objectives: capture
  once, reference many. Prefer one authoritative export over many screenshots.
  Only `provider_satisfies`/`shared` controls generate provider evidence tasks;
  `customer_owns`/inherited never do. Capture product-level config once and
  reuse across client orgs; re-capture only org-specific state. Batch tasks by
  collection session.

- **BYO-AI / pluggable provider.** Every AI call routes through a provider
  abstraction each deployment configures: Anthropic API, Azure OpenAI (GCC High),
  or a local model (Ollama/vLLM). CUI-sensitive deployments must be able to keep
  generation local; never assume CUI may go to a commercial cloud LLM.

- **Scope = denominator.** Control objectives are evaluated against scoped
  assets ("AV on all CUI assets" = devices where category = CUI Asset).

- **Contacts are separable from future users.** The `contact` table and all
  RACI/task-assignment FKs use `contact.id`. When auth lands, a `user` table
  gets a nullable `contact_id` FK so a login account maps to an existing
  contact — it does NOT replace it. This means RACI and task assignment work
  today without auth, and auth lands cleanly without a migration that renames or
  collapses the contact table.

- **Verify reference data against authoritative sources.** SPRS weights,
  control families, assessment objective text, and satisfaction types must be
  verified against NIST SP 800-171A Rev 2 and the DoD CMMC scoring methodology
  — not derived from vendor CRMs, blog posts, or third-party summaries. The
  verified weight distribution is: **44 controls × 5 pts + 14 controls × 3 pts
  + 52 controls × 1 pt** (max deduction 314 pts; score range −204 to 110). Any
  migration that touches control weights must be diffed against this.

## Product invariants

These rules have each decided a design more than once. Each used to be
restated slice by slice; it is stated here once. Cite this section, not
the slice that last applied a rule.

- **Candidates are offered and a person attaches them; nothing is
  auto-attached.** Why: evidence nobody chose changes what an assessor
  reads without anyone having looked.
- **Today's state never rewrites yesterday's record.** Why: a record is
  read as what was true when it was made, so a change writes a new
  version and says the drift.
- **WinGRC does not author content into a customer's documents or lists,
  and a customer's content never becomes template source.** Why: content
  moves from template to customer only when a person adopts it, so one
  tenant's records can never become another's examples.
- **A check must actually check: it must not match its own invocation, and
  a screen must not state a result it never computed.** Why: a check that
  cannot tell itself apart from its subject, or a sentence that says the
  rule beside an enabled button, reports "fine" by construction (the
  process-grep form in full under Stack & conventions; the screen form is
  L.10's tag dialog).

---

## Stack & conventions

React 19 + Vite (SPA) · FastAPI (Python 3.13) · PostgreSQL 18 + pgvector ·
SQLAlchemy 2.0 + Alembic · S3-compatible storage (MinIO in dev, Azure Blob /
AWS S3 in cloud). Two application images — `backend` (FastAPI) and `nginx`
(reverse proxy, bundling the built frontend as static files), per
`docker-compose.yml` — plus unmodified `postgres`/`minio` base images;
deploy to Docker / Azure Container Apps / GCC High / air-gapped.

- Keep the domain core DB-agnostic and unit-testable (`backend/app/domain.py`,
  `backend/app/assessment.py`).
- All DB-touching tests use `@pytest.mark.integration` and require
  `WINGRC_TEST_DATABASE_URL`. Unit tests run without a DB. **Mark the
  test, not the module**, unless every test in the file needs a database.
  **A marker never causes a skip — the `db_session` fixture does**, by
  calling `pytest.skip()` when `WINGRC_TEST_DATABASE_URL` is unset. CI's
  `backend` job runs plain `pytest -q` with no `-m` filter, so it runs
  every test and the DB-needing ones skip themselves. What a misplaced
  marker breaks is therefore **selection, not coverage**: `-m integration`
  runs tests that need no database, and `-m "not integration"` drops tests
  that would have run fine. An earlier version of this bullet claimed the
  `backend` job uses `-m "not integration"` and that a module marker cost
  it coverage; both were wrong, and measuring it is what showed so — the
  no-DB total was 458 before and after the 2026-09-30 retag of 28
  over-marked tests. Seven files have been corrected in both directions
  (three under-marked 2026-09-29, four over-marked 2026-09-30); see
  `docs/roadmap.md`'s CI-red and N.3 entries.
- **A test's precondition carries a message saying it is one**, placed
  where the fixture sets it up: `assert a != b, "fixture must … or this
  test proves nothing"`. A bare assert on the setup fails as if the
  product were wrong (519d894's red run; `docs/roadmap.md`'s list
  version cause entry).
- **A status check must not identify its subject by a pattern its own
  invocation can match.** A process-list grep for a script name
  (`pgrep -f renew.sh`, `ps … | grep "[r]enew.sh"` inside an
  `ssh '…renew.sh…'`) matches the shell running the grep, so it reports
  "running" by construction and can never report "stopped" -- written
  twice in one session (2026-10-17). Check something structurally
  distinct from the checker: a container count, a pidfile the subject
  wrote, an exit status. A check that cannot tell itself apart from what
  it is checking is not a check (`docs/roadmap.md`'s item W keeps the
  TLS-specific record).
- **Every change a request makes is audited, and
  `tests/audit_check.py` enforces it.** Each table a request commits a
  write to needs an audit row in that request naming it, or a rule
  there. A rule is keyed by table, operation and columns, and carries
  its reason. It names the parent audit type that accounts for a
  derived write, or marks bookkeeping. A failure names the table and how
  it was written. Add the audit row; add a rule only for a write that is
  derived or bookkeeping, and say why. A new `auth.` SECURITY DEFINER
  function the app calls is classified there as a writer or a reader.
  CI's `integration` job also sets `WINGRC_AUDIT_COVERAGE`, so a
  mutating route no test drives to a write fails the run unless it is
  listed with its reason. The check matches an audit row's entity, not
  its action, so a test still asserts the action where it matters
  (`docs/roadmap.md`'s "Every change a request makes is audited" entry).
- **A write to a live database goes through an audited CLI command, or
  it does not run.** `audit_check.py` only sees HTTP requests. A script
  that opens its own `SessionLocal()` is outside its reach, so nothing
  but this rule covers it. A one-off fix is written as a command in
  `app/cli.py`: dry-run by default, `--apply` to write, an audit row per
  change saying why, archiving rather than deleting
  (`review-cycles-detach-unattested` is the pattern). Reads may be ad
  hoc. A credential is minted only through the route that records it,
  never by a script. On 2026-09-07 a verification script on live made a
  user, membership and API token, then raw-`DELETE`d them with no audit
  row, so live's log holds no trace any of them existed
  (`docs/roadmap.md`'s "A concrete case from before the rules").
- **A docstring or comment that makes a universal claim about what the
  code does carries a test that fails when the claim stops being true.**
  Universal means "none", "never", "always", "only", "every", or a
  closed list of callers or cases. The docstring names its test, so the
  next reader can see the claim is held. A claim nobody can test is
  worded as intent ("is meant to"), not as fact. Why: such a claim is
  read as settled, and when the code moves the prose stays. Each of
  these asserted the opposite of the code:
  - `publish_document` said control_state was "NEVER touched" while it
    attached evidence (item X);
  - `close_cycle` said its record was linked on every assessment for
    every status;
  - the audit check's first draft said no SECURITY DEFINER function
    writes on a request path, and `grant_org_membership` does;
  - `clear_current_org` said "No production path calls this", until org
    creation did.

  These are two kinds. `publish_document`'s claim was false when
  written: an author's error, which review can catch. `close_cycle`'s
  and `clear_current_org`'s were true when written and made false by a
  later change, weeks later for one and the same slice for the other.
  The second kind is the larger problem, because nobody re-reads a
  docstring in a file they are not editing. That is why the test has to
  keep running rather than the claim being checked once at review.

  Existing claims are brought under this when their code is touched
  (`tests/test_audit_check.py`'s definer classification and
  `test_rls_context.py`'s caller list are the pattern).
- **A slice that puts text on a new background measures it in that
  slice.** New means a background, tint or opacity not measured before. The
  slice measures it with WCAG 2.x and states the ratio as a number:
  4.5:1 for anything read, and 3:1 only for an element faint on purpose.
  It does not wait for a later sweep. The evidence section's archived rows
  shipped at 3.52:1 one day and were found the next by V's sweep
  (`docs/roadmap.md`'s visual batch entry).
- **A test's time margin is taken from the clock the code under test
  reads.** A check made in SQL reads `now()`, fixed when the test's outer
  transaction began; a margin off `datetime.now()` is against the wrong
  clock. Artifacts compared across two moments must not carry a stamp
  where they are compared -- not one finer than the gap, and not a
  coarse one either, because a minute or a day can roll over between
  them (a bundle's folder is dated; look its pages up per zip). Three
  red runs and three latent failures had this shape (`docs/roadmap.md`'s
  clock-granularity sweep entry).
- **When removing or changing a side effect, find what reads it as a
  record.** A document version's "what was this approved for" was only
  ever the links publish made, so removing auto-linking (item X) would
  have silently emptied it. Store the fact where it is decided
  (migration 0069; `docs/roadmap.md`'s side-effect lesson).
- CI (`.github/workflows/ci.yml`) runs `backend` with **no database at
  all** and `integration` against `postgres:18`. Both run
  unconditionally — `integration` deliberately has no `needs: backend`,
  because a skipped required status check counts as satisfied, and
  because gating it once hid a real failure for five runs. The runner is
  pinned to `ubuntu-24.04`; revisit after 2026-10-19.
  It triggers on **branch pushes only** (since 2026-10-10): every pushed
  branch gets one run per commit before any PR exists, and that run's
  checks are what the PR shows. There is no `pull_request` trigger; it
  only duplicated runs (see `docs/roadmap.md`'s CI-triggers entry).
- **Python versions come from `backend/requirements.lock`** (since
  2026-10-28), hashed and installed with `--no-deps` by the image and
  both CI jobs. Before it, `pyproject.toml`'s floors let every build take
  the newest release of every package. A version changes only through
  `make lock` and a committed diff. CI's `Lock is current` step fails when
  `pyproject.toml` needs a re-lock. The base image, the apt line and npm
  still float (`docs/deployment.md`, "Python dependencies"; item R).
- `ruff check` must be clean before merge. B008 is suppressed per-file for
  FastAPI router files (see `backend/pyproject.toml`).
- Work on branches, small commits. Push after every commit — dev server is a
  separate Linux box, deployed from what is pushed.
- **Live is deployed by `deploy/deploy.sh`, never by `git pull`.** What
  goes wrong, and how it looks when it does (the full mechanism is in
  `docs/deployment.md`, "What a `git pull` on the live host does"):
  - **Since 2026-10-08 the live checkout is a detached HEAD** at the
    deployed commit; `deploy.sh` leaves it there. `git pull` fetches
    first, so `origin/main` really does move, and then refuses to merge.
    It exits 1, but its message ends with an indented
    `git pull <remote> <branch>` usage line. Seen through `| tail`,
    that line is all that shows, and the pipeline's status is `tail`'s
    0. The working tree has not moved. A `docker compose build` after it
    succeeds, on the old commit, stamped `unknown`. That was 2026-10-26:
    nginx rebuilt at #52, then nginx alone at V.1 with the backend on #52,
    and no deploy record.
  - **Until 2026-10-08 the checkout was on `main`**, bind-mounted under
    `uvicorn --reload`. The pull succeeded and printed an ordinary
    fast-forward, and that *was* a deploy, without migrations. That was
    2026-10-07's three-hour outage.

  Opposite mechanisms, one cause: notes that said "`git pull` first",
  followed at the terminal. The tell in both cases was that nothing
  after the pull said which commit was running; `/health` now does, per
  component. A bench clone is a branch checkout, where a pull is fine
  (`docs/bench-stack-verification.md`).
  - **A deploy that changes `deploy.sh` runs the target's copy.** Bash
    keeps reading the file it opened, and the checkout writes a new one.
    Since 2026-10-28 `deploy.sh` compares its own blob with the
    target's and, when they differ, checks out and `exec`s the target's,
    once. That works only from a running copy that has the check, so
    the hand checkout first (`git checkout --detach origin/main`, then
    `deploy.sh`) is still needed when the copy on the host predates it
    (`docs/deployment.md`, 7c).
- **A pipe whose status is read must not report the last command's
  status in place of a failure.** On 2026-10-26 a failed `git pull`
  read as success through `| tail`. A new bash script starts
  `set -euo pipefail`, as `deploy.sh` and `tls/renew.sh` do. Two
  exceptions are deliberate:
  - `tls/check-expiry.sh` drops `-e`, so that every name is reported.
  - `tls/auth-hook.sh` is POSIX `sh` with no pipefail, because its wait
    loop reads `grep -q`'s status by intent.

  A pipe ending in `grep -q` can SIGPIPE its writer, which pipefail
  reads as a failure. It is safe in `deploy.sh`'s wait loop only
  because `/health`'s body is far under the 64 KiB pipe buffer. A CI
  `run:` step that pipes needs `shell: bash`: GitHub's default runs
  `bash -e` without pipefail, and naming `bash` adds it. At a terminal,
  check the result itself (`git rev-parse HEAD`, `git ls-remote`,
  `/health`), never the exit status a `| tail` shows. The full sweep
  of every pipe is `docs/roadmap.md`'s "Pipes whose status could hide a
  failure".

  **This convention reaches committed scripts and CI steps, not commands
  typed while investigating.** That is where every instance has been:
  - 2026-10-26's `git pull`;
  - two more by 2026-10-28, one the editable install hidden behind
    `| tail -1` the same day this bullet was written;
  - on 2026-10-29, a chain of commands joined with `;`, whose first step
    failed. The rest committed and pushed conflict markers.

  For a person at a terminal it stays a recognition problem: read the
  result, not the status.

  **For Claude's Bash tool on wl-dt26, a hook catches the pipe half and
  not the chain half (2026-10-30).** It rewrites every command to start
  with `set -o pipefail`.
  - **The script is tracked; what turns it on is not.** The script is
    `.claude/hooks/strict_bash.py`, reviewed like any code. The
    registration lives in the checkout's `.claude/settings.local.json`,
    which is machine-specific (`py -3`, an absolute path). Jarrod's global
    git ignore covers that file, so `git status` does not list it. A fresh
    clone keeps the script and loses the hook without a word. The
    registration block is in the script's docstring, and the clone
    checklist is in `docs/roadmap.md`'s item on the git repack failure.
  - **Claude: your first Bash call in every session is `false | cat`,
    and it must exit 1.** That includes a session resumed from a summary.
    Run it before any other command, because the failure it catches is
    silent and anything run first has already depended on the hook. This
    is Claude's job, not Jarrod's: Claude reads this file at the start of
    every session, and a check left to memory gets skipped. If it exits
    0, or the Bash tool refuses or errors, **stop and report it to
    Jarrod before any other work**, with the result as it came back. It
    is not a note to add to the report. Do not re-register the hook
    yourself: settings that change what runs on every command are
    Jarrod's to restore.
  - **The general rule: a check that repairs what it checks reports
    success either way.** If the check could quietly re-register the
    hook, a lost hook and a working one would look the same, and nobody
    would learn the clone dropped it. Report, then let the owner fix it.
    This is the product invariant "a check must actually check", seen
    from the other side.
  - **There is no tracked `.claude/settings.json`, and none will be
    added to automate this check (Jarrod, 2026-10-30).** A tracked
    session-start hook would survive a clone and run the check without
    anyone remembering. But the repo is public. A settings file that runs
    commands when a session starts executes repo-controlled code in
    every cloner's environment. The check itself is harmless; the
    precedent is what is declined. Do not re-propose it as an obvious
    improvement. Machine-specific registration stays in
    `settings.local.json`, and the check stays Claude's first call.
  - **Until this is on `main`, the registration points at an identical
    copy in `~/.claude/hooks/`.** On a branch without the file, `py` exits
    2 (measured), and the hooks documentation says a PreToolUse exit of 2
    blocks the call, so every Bash command would be refused. Once every
    branch has it, point the registration at the tracked file. A branch
    from before then fails loudly, not silently.
  - **Pipes: caught.** `false | cat` now ends with exit 1, which the tool
    reports as a failure.
  - **`;` chains: not caught, and no prefix can catch them.** The tool
    runs each command where bash ignores `set -e` and the ERR trap. With
    both set and showing as on, a bare `false` still did not stop the
    chain. A fresh `bash -c` process would escape that, but permission
    rules would then match the outer `bash -c '…'` text, so it was not
    built. The chain that pushed conflict markers stays a recognition
    problem.
  - **Permission rules: deny, the case that matters, was measured.**
    Temporary deny rules (a prefix rule, an exact rule, and one inside a
    `;` compound) denied the same commands before and after the rewrite.
    The denial message quotes the rewritten text, so rules see what runs,
    and the added line does not hide the denied step. The two unmeasured
    cases point the safe way, or do not arise:
    - **`allow`:** a rule that stopped matching sends the command to the
      auto-mode classifier, or to a prompt outside auto mode. That is
      more scrutiny, not less.
    - **`ask`:** this is the one direction that would mean less
      scrutiny. But no `ask` rule is configured. On 2026-10-30 there was
      none in `~/.claude/settings.json`, `~/.claude/settings.local.json`
      or this checkout's. `ask` goes through the same matching the deny
      probes measured. Measure one before adding the first `ask` rule.
  - **The hook never returns a permission decision.** A hook that
    returns `"allow"` with its rewrite skips every permission check.
    That is the shape the documentation's example shows.
  - **Escape hatches:**
    - `… | head || true` for a pipe whose first stage may fail;
    - `#strict-off` as a command's first line leaves it untouched;
    - a command already starting with `set -` is left as written.
  - **Out of reach:**
    - pipes inside a quoted remote command (`ssh host '… | tail'`);
    - the PowerShell tool;
    - a command that prints an error and exits 0.

  **CI was no gate against conflict markers in prose until #69.** On
  the marker commit (`6bd3d5a`), `backend` ran its lock check, ruff and
  the full no-database suite, and passed. Nothing in CI reads
  `docs/roadmap.md`. That run was later cancelled, superseded by the
  fix, so it left no failure behind to notice. #69 adds a `backend` step
  that greps every tracked file. It passes only on `git grep`'s no-match
  exit, so an erroring grep fails. A planted marker was shown to fail it.
  Every file, not only prose: markers in a `.py` file should fail ruff's
  parse, but "should" was never measured.
- **Landing a slice, in order: bench-verify → open a PR → *watch CI go
  green* → merge → deploy → report.** The CI step is not optional and not
  "check later". Six consecutive red runs on `main` went unnoticed
  (2026-09-28/29) purely because nobody looked; the signal existed the
  whole time. A slice is not done until `backend` and `integration` have
  both actually **run** and passed — a skipped job is not a passed job.
  **The live walk (clicks and the text each should show) goes in the PR
  body, not only in a message.** Messages are what a context summary
  drops. A walk kept only there gets reconstructed from memory later,
  and nobody can tell the reconstruction from the record. Until a walk is
  done, carry it forward in the next deploying PR (`docs/roadmap.md`'s
  "a walk list kept only in a message").
- **`main` is protected (since 2026-09-29): the merge path is a PR, not a
  fast-forward push.** The repository ruleset "Branch Protection" requires
  **`backend`, `integration` and `image`** (read from the API on
  2026-10-24), and blocks deletion and non-fast-forward pushes. So
  branches-not-direct-pushes is enforced rather than conventional, and
  "watch CI go green" is a precondition for merging rather than a
  courtesy. `image` stays required: a broken Dockerfile merged to `main`
  would otherwise first show as a deploy failure, at the worst moment. Do
  not loosen the ruleset to match older prose.
  *History:* until 2026-10-24 this bullet said `image` was "deliberately
  not" required, as did the 2026-09-29 entries in `docs/roadmap.md`. The
  reason given was that `image` has `needs: [backend, integration]`, so it
  skips when either fails, a skipped required check counts as satisfied,
  and so requiring it "would wave through exactly the runs protection
  exists to stop". That holds only if `image` replaced the other two.
  Required beside them, a skipped `image` passes nothing, because the
  failed job it skipped for is itself required and still blocks. The
  only effect of requiring it is to stop a merge where the tests pass
  and the image build fails. When `image` became required is not
  recorded; the ruleset was last edited 2026-10-13.
- **Reading CI status needs no credential.** The repo is public, so run
  conclusions and per-job/per-step results come back from
  `api.github.com/repos/winsorlabs/wingrc/actions/runs` unauthenticated
  (60 requests/hour per IP — poll sparingly, it is easy to exhaust). Raw
  log download is the one thing that 403s, which is why the count floor
  below exists: it fails the run in-process instead of relying on someone
  with `Actions: read` noticing a wall of skips. A token is needed only to
  *open or merge* a PR, never to watch one.
- **Claude opens PRs; Jarrod merges them.** `gh` is not installed on
  wl-dt26, and it does not need to be. Git Credential Manager holds an
  OAuth token for `winsorlabs` (scopes `repo`, `workflow`, `gist`; admin
  on the repo). Read it with `git credential fill` and send it to
  `POST /repos/winsorlabs/wingrc/pulls` without printing it. This first
  worked with #57 (2026-10-24); before that, every PR was opened by hand
  on the wrong assumption that no credential existed. The same token
  could merge, and the ruleset requires status checks but no approving
  review. So merging is Jarrod's by convention, not by enforcement:
  Claude never calls the merge endpoint. **It cannot be enforced as
  things stand, so do not spend an afternoon trying.** The token is the
  `winsorlabs` account, the same one Jarrod merges with, so branch
  protection cannot tell the two apart. Requiring an approving review
  would leave nobody able to approve, because GitHub blocks approving
  your own PR. The real fix is a separate machine account with push but
  not merge rights. That is the roadmap's item "A machine account for
  Claude", to do if a second person ever touches this repo, not before.
  An authenticated poll also lifts the CI-status rate limit to 5000 an
  hour.
- The no-database suite is the cheap local gate: `pytest -q` in `backend/`
  with no `WINGRC_*DATABASE_URL` set runs ~458 tests in **~10s**. Use it
  before every push. It is exactly the configuration CI's `backend` job
  uses, so it catches that whole class before a run is spent.
- **A count floor makes a silently-skipping job fail.** Both CI jobs set
  `WINGRC_MIN_TESTS_RUN` (200 for `backend`, 500 for `integration`) and
  `backend/tests/conftest.py` fails the run when fewer tests than that
  actually executed. It is off unless that variable is set, so narrow local
  runs are unaffected by construction. Do not "fix" a floor failure by
  lowering the number — it means the run verified nothing, and the cause is
  almost always a missing `WINGRC_TEST_DATABASE_URL` or an unreachable
  database.
- When marking any slice done, grep `CLAUDE.md`, `ROADMAP.md`,
  `docs/roadmap.md`, and the relevant `PLAN-*.md` for that slice's own
  name/number and update every hit, not just the file you're actively
  editing.

### Key file locations

| Path | Purpose |
|---|---|
| `backend/app/models.py` | All SQLAlchemy models (single file, ~1660 lines) |
| `backend/app/assessment.py` | Pure domain functions: `compute_sprs`, `magic_loop_updates` |
| `backend/app/engine.py` | DB adapter: `start_assessment`, `activate_org_product`, `deactivate_org_product`, `recompute_sprs` |
| `backend/app/bundle_service.py` | Bundle snapshot + ZIP render (pure function over frozen dataclasses). **Every query here must be totally ordered** — ordered or `DETERMINISM-EXEMPT`, ending in a primary key or `DETERMINISM-TOTAL`; `tests/test_bundle_determinism.py` enforces both, because an unordered collection reaching a rendered artifact makes it nondeterministic and the byte comparison does not reliably catch it |
| `backend/app/markdown_doc.py` | The document-body format decision (GFM-subset Markdown) and the only renderer for it — read its docstring before touching document rendering anywhere |
| `backend/app/document_diff.py` | Pure version-to-version diff (word spans, collapsed runs); no DB |
| `frontend/src/lib/markdown.tsx` | Browser counterpart to `markdown_doc.py` — Markdown → React elements, never `dangerouslySetInnerHTML` |
| `frontend/src/lib/tiptapMarkdown.ts` | Editor bridge: stored Markdown ↔ TipTap document, owned in-repo so the subset stays the subset |
| `backend/app/routers/` | FastAPI routers: `assessments`, `bundle`, `contacts`, `evidence`, `frameworks`, `orgs` |
| `backend/app/list_projection.py` | The one function that decides a CMMC list cell (shared by the `.xlsx` export and the scope-drawn lists in Library › Lists); `catalog.py`'s `ListView.sources` says where each column's value may come from |
| `backend/app/list_templates.py` / `list_library.py` | The list library (roadmap L): the template importer, whose contract is the template header convention, and the append-only versioned list model. A list is a definition plus its own rows -- read `docs/PLAN-list-library.md` before touching either. `ListVersionSnap` is what an export (L.3) renders: `render.render_list_version_bytes` makes a workbook that is a function of the version alone -- no wall-clock time, so the standalone export and a bundle's copy are the same bytes. Every version says why it exists (`CAUSES`: a person's edit, a changed source, or an importer change with the source unchanged); an importer change writes a version of every list it touches, so read the dry-run's `new_version` count before shipping one. An import never replaces a person's edit: such a list is a `conflict`, skipped unless that exact edit is named (`--overwrite-edit list_key@vN`, per edit, never all). A list can be built by hand (`create_by_hand`, the header block's fields, v1 `created_by_hand`). Tags record who added them (`added_via`) and every change is a `list_tag_event`, shown in the list's History beside its versions; an import manages only its own tags. **Removing a tag is refused while evidence rests on it** (`tag_removal_blockers`, which reads L.4's live links); the confirmation asks the server first (`GET …/tags/{key}/removal`, L.10) and says what it found, never the rule in the abstract. The check and the DELETE's 409 answer in fields (`reason`, `links` of `RestingLink`), and the client writes the one sentence, in the title, never the key (`TagRemovalDialog.tsx`, shared with documents, X.1). An org adopts a template from the org holding it (`list_adoption`, L.7): templates stay org-scoped, the copy records `template_list_id`/`template_list_version_id`, and a newer template version is taken deliberately, never over a person's edit unless it is named. "Populated" is `list_library.is_populated`, the one rule for what may be offered as evidence |
| `backend/app/list_evidence.py` | A populated list linked as evidence (L.4): the link names a `list_version` (`evidence.source_list_version_id`, `kind='reference'`, `artifact_type='list'`), never the list, and stays there when the list moves on -- the drift is said, and moving forward writes a new link and archives the old. Offered, never attached; confined to the list's tags; no control_state or SPRS change. A product's deactivation does not archive these links (`engine.deactivate_org_product`), or the tag they rest on would become removable. Every link, move forward and unlink is a `list_link_event` (L.10), shown in the list's History beside versions and tags, because the audit log is not where a person looks and not every role can read it |
| `backend/app/document_evidence.py` | A published document attached as evidence (item X), L.4's shape for documents: publish writes the version's Evidence row and links nothing; the document is offered on its tagged objectives and a person attaches it. A link stays on its version, and a republish never archives it. A superseded version says *superseded* and "may no longer be in force" (not a list's "version N is available"), in the UI and the bundle. The bundle carries the text of every superseded version a live link in the assessment names (`ssp/06_documents.html`'s second section), as `lists/` does for list versions. Each attach, move and removal is an audit row on the version, in the document's History. A version records the tags it was published under (`published_objective_ids`, migration 0069); no link is read as coverage. Its module docstring has the two-question rule for any path that links evidence. Tags (X.1) are per objective; a typed practice key expands to its objectives, each its own removable tag (`resolve_tag_key`), and removing one is refused while a live link from any version rests on it (`tag_removal_links`), by the same people who define lists |
| `frontend/src/components/ListLibraryPanel.tsx` / `lib/listLibrary.ts` | Library › Lists (L.2), the one Lists screen: library lists and the four scope-drawn ones together, in separate sections by kind (live from scope, the org's own, templates -- L.2.1; `kindOf`). A list's page puts its rows first: what to act on, then the table, then reference, then History collapsed (L.8). A list's rows, library or scope-drawn, are drawn by `components/ListRows.tsx` (`ListRowsTable`: the sticky header in its own scroll box and the side-scroll cue from `useSideScroll`; L.9), styled by the one `.list-rows` block, never the shared `.contacts-table` header alone. `rebase()` is how a stale save is re-applied for the second editor -- rows are tracked by origin, never guessed |
| `backend/app/evidence_links.py` | The one path that archives and restores an evidence link (2026-10-19). **Nothing deletes a link, an Evidence row or its bytes**: the objective's Detach (it was a ✕ that deleted the artifact, unaudited), the list and document unlinks, a deactivation and the `review-cycles-detach-unattested` CLI all archive, and every archive says why (`archive_cause`, migration 0071). Only a deactivation's archive names a product, so reactivation restores what it archived and never what a person detached. A reader of `evidence_state_link` that means "evidence present" filters `is_archived` -- four did not, and counted archived links as live |
| `backend/app/storage.py` | `StorageClient` ABC + `MinIOClient` + `NullStorageClient` |
| `backend/app/audit.py` | `log_event()` — writes `AuditLog` rows; also the actor-resolution helpers both audit views share |
| `backend/app/rls.py` | The only place `app.current_org` is written, plus the `after_begin` hook that makes it survive a commit — read before touching anything org-scoped |
| `backend/app/migrations/` | Alembic migrations (currently 0001–0071) |
| `backend/baselines/` | YAML product baselines (`rocketcyber.yaml`, …) — not repo-root `baselines/`; `seeds/baselines.py:_BASELINES_DIR` resolves here, and a stale repo-root duplicate that drifted out of sync with a real coverage_basis reclassification was removed 2026-09-19 (see `docs/roadmap.md`'s tenant lifecycle consolidation pass entry) |
| `docs/fips.md` | FIPS 140-2/140-3 crypto boundary documentation |
| `docs/architecture.md` | Authoritative architecture description (the five layers) — companion to this file's terse session version |
| `docs/deployment.md` | Generic deployment walkthrough: clone, configure, `docker compose up`, HTTPS |
| `docs/roadmap.md` | Full roadmap detail behind the "Roadmap — priority order" section below |
| `docs/PLAN-auth-rbac-completion.md` | Auth/RBAC completion plan (slices I.1–I.9) — read before touching auth |
| `docs/wl-util-1-worked-example-deployment.md` | Worked-example hardening + HTTPS (Certbot/DNSimple DNS-01) walkthrough on a real box |
| `docs/bench-stack-verification.md` | How to verify a slice on an isolated wl-util-1 Docker Compose project before merging to `main` — the `claude` account/key, isolation rules, run steps. Read before claiming a slice is verified |
| `docs/azure-container-apps-deployment-plan.md` | Draft Azure Container Apps deployment plan — architecture only, not yet executed |
| `docs/pdf_ssp_template_spec.md` | Design spec for the consolidated PDF SSP export — not built yet |
| `docs/cloud-hosting-options.md` | Cloud hosting cost / GovCloud-necessity research for docs.wingrc.us |

---

## What is built and verified

Everything listed here has passing tests and is deployed on the dev server.

### Scope module (migrations 0001)
`scope_entity` table with JSONB attributes. Spreadsheet import (openpyxl),
dry-run reconcile diff, apply. Catalog views (CUI Assets, CUI Users, etc.) as
enumerated view definitions. Lists are views over the scope graph, not separate
documents. Scope is the denominator for control evaluation.

**Lists (2026-10-07; one screen since L.2, 2026-10-13):** Library › Lists shows the four CMMC lists drawn from scope beside the list library, and
`GET …/exports/{view_id}` is the `.xlsx` export (a GET, rendered in memory —
it once shared one temp path across orgs). **An import or sync may not clear
or overwrite an operator-entered field it has no source for:**
`repo.upsert()` carries `domain.OPERATOR_OVERLAY_ATTRIBUTES` and
`scope_category` forward. No import or sync changes an existing row's
`in_boundary` or moves it out of `decommissioned` (a workbook may still
decommission a row; Liongard keeps status outright), unless the caller passes
`operator_edit=True` (only `POST`/`PATCH /scope` do). Lists exclude
out-of-boundary entities and state how many were excluded. Asset Type *is* `scope_category`. `[PLACEHOLDER - reason]` cells are
values, stored verbatim. **No workbook evaluates a cell:** every one leaves
`render.py` through `_save()`, which writes a cell starting with `=`, `+`,
`-`, `@`, tab or carriage return as quote-prefixed text; a new renderer
saves through it (`tests/test_workbook_formulas.py` fails otherwise). See `docs/roadmap.md`'s Lists entry.

### Assessment engine (migrations 0002–0006)
Full model set: `Framework → Control → AssessmentObjective → ControlState`;
`Product → BaselineControl → BaselineEvidenceSpec`; `OrgProduct`.

**SPRS scoring** (`assessment.py:compute_sprs`): groups objectives by control,
applies worst-objective-wins rollup per control, deducts weight for any
non-passing control (statuses: `not_met`, `partial`, `pending_evidence`,
`needs_review`). `met`, `inherited`, `not_applicable` do not deduct.
`engine.py:recompute_sprs` is the single write path for
`assessment.sprs_score` — every mutation that can change a control's status
routes through it: assessment creation (`start_assessment`), every product
activation/deactivation, every control-state edit
(`routers/assessments.py:patch_control_state` — e.g. the assessment board's
"mark met" action), and always fresh immediately before bundle export
(`bundle_service.snapshot_bundle`). Do not describe this list as just
"activation/deactivation/bundle export" elsewhere — that enumeration has
been copied stale into other docs before (docs/PLAN-gui-restructure.md's
G.2 section, 2026-08-19) precisely because it read as complete when it
wasn't. `recompute_sprs` acquires `SELECT ... FOR UPDATE` on the assessment
row before reading `control_state`, serializing concurrent recomputes for
the same assessment — added 2026-08-19 after G.3's dashboard surfaced a
lost-update race that predated it. `recompute_sprs` also flushes
unconditionally as its first statement, before that lock — production's
`SessionLocal` (`db.py`) sets `autoflush=False`, and `patch_control_state`
used to set `cs.status` and call `recompute_sprs` with no flush between
them, so the score computed there could miss the very edit that
triggered it. Both fixes landed the same day; see
`docs/PLAN-gui-restructure.md`'s G.2 section for the full incident
writeup.

**Magic loop** (`engine.py:_run_loop`, pure function in `assessment.py`):
- Activating a product → objectives it covers flip `not_met → pending_evidence`
- `coverage_basis == 'platform_only'` is excluded — vendor self-coverage never
  credits the customer's CUI environment
- SPRS recomputed as part of this too — see the complete call-site list above
- Re-activation after deactivation restores archived tasks/links; restored states
  go to `needs_review` (not `pending_evidence`) — MSP must re-confirm prior
  artifacts are still current

**Baseline versioning** (migration 0054, `models.py:ProductBaselineVersion`):
`BaselineControl` rows are immutable once created — an import that actually
changes the mapping creates an entirely new version (fresh rows) rather than
mutating the current one; a no-op reimport writes nothing and leaves
`is_published` untouched. `OrgProduct.baseline_version_id` pins a tenant to
the version active when they activated (`_run_loop` resolves `BaselineControl`
by that pin, never by bare `product_id`); a later reimport never touches an
already-activated tenant's claims. Moving a tenant onto a newer version is a
separate, explicit action (`engine.py:move_org_product_version`) — only the
objectives whose claim actually changed land in `needs_review`; evidence
always survives a version move (contrast deactivation, which archives it).
See `docs/roadmap.md`'s item P for the full design writeup, including the §1
bug this fixed: `seed_baselines` used to silently keep honoring a control
removed from the source YAML — now dry-run shows it as `change_type="removed"`
and a new version excludes it.

**Deactivation/archive lifecycle** (`engine.py:deactivate_org_product`):
- All control states sourced from the deactivated product → `needs_review`
- Evidence-state links attributed to the product → archived (`is_archived=True`,
  `archive_cause='deactivation'`, `archived_by_product` FK set for provenance-based
  reversal on re-activation; `evidence_links.py`). Reactivation restores only
  those: a link a person detached names no product
- The person may name what replaces it (`org_product.replaced_by_product_id`,
  `deactivated_by`, migration 0071). Optional, never inferred, need not cover the
  same objectives; the objective's archived evidence then reads "Replaced by X",
  with who said so, and so does the bundle
- Evidence tasks seeded by this product → archived + closed (`na`)
- `OrgProduct.status → decommissioned`, timestamp set
- SPRS recomputed — `needs_review` does not satisfy, score drops

**Audit logging**: every state change writes an `AuditLog` row via
`audit.log_event()`. Context dict captures `via`, `product_name`,
`assessment_id`, and any before/after values. The bundle export writes
`action="bundle.export"`.

**Control state history**: `ControlStateHistory` table captures every
`(previous_status, new_status, previous_responsibility, new_responsibility,
change_reason)` transition with FK to `control_state`.

### Evidence collection (migrations 0007–0010)
`Evidence` (file / reference), `EvidenceStateLink` (many-to-many, with
`is_archived` + `archived_by_product` for deactivation reversal, and
`archive_cause` saying why; never deleted -- `evidence_links.py`),
`EvidenceTask`, `EvidenceTaskStateLink`.

**Upload**: magic-byte validation + extension allowlist + 50 MB cap. Storage key:
`{org_id}/evidence/{evidence_id}/{evidence_id}{ext}`. Stored in MinIO
(`StorageClient.upload_file`). Presigned URL via `StorageClient.presigned_url`
using the public endpoint so browser-facing URLs resolve (see FIPS section).

**Evidence task fan-out**: when a product is activated, `BaselineEvidenceSpec`
rows are translated into `EvidenceTask` rows with dedup:
1. By `baseline_spec_id` — idempotent on re-activation
2. By `(title.lower(), artifact_type)` — same artifact across specs shares one
   task (evidence minimization across controls)
Each task links to multiple `ControlState` rows via `EvidenceTaskStateLink`.

**Task collect** (`POST …/evidence-tasks/{task_id}/collect`): marks task
`collected`, creates the evidence artifact, links it to all control states the
task covers, optionally advances states from `pending_evidence → needs_review`.
One collect action satisfies many objectives.

**Triage / filtering**: evidence task list supports filters by `status`
(open/collected/na/archived), `collection_session`, `artifact_type`, and the
family/control the task is linked to.

### Onboarding wizard (migrations 0011–0013)
- **Org profile** (`Organization`): CAGE code, UEI, address, industry, phone,
  website, logo upload (stored in MinIO, presigned URL for display).
- **System description** (`SystemDescription`): system name/type, operational
  status, CUI categories (JSONB array), CUI storage locations (JSONB array),
  authorization boundary description, external connections (JSONB array), CUI
  flow description. One row per org (UNIQUE constraint).
- **Contacts** (`Contact` + `ContactDocumentationRole`): name, email, phone,
  affiliation (msp/customer/other), role_title, contract_ref. Documentation
  roles: `it_admin`, `security_officer`, `system_owner`, `authorizing_official`,
  `president`, `cui_user`, `assessor`, `mssp`, `consultant`, `other`. RACI
  assignments (`RaciAssignment`) link contacts to control states with
  letters A/R/C/I.

### FIPS 140-2/140-3 audit (no migration required)
Full crypto boundary documented in `docs/fips.md`. Application code is clean:
no direct `hashlib` usage, no hand-rolled crypto. Two fixes shipped:

1. **botocore MD5 suppression** (`storage.py`): `boto3.Config(
   request_checksum_calculation='when_required',
   response_checksum_validation='when_required')` prevents botocore from calling
   `hashlib.md5()` on every `put_object` (hard-fails in FIPS mode).

2. **Public endpoint for presigned URLs** (`storage.py` + `config.py`):
   `WINGRC_STORAGE_PUBLIC_ENDPOINT` env var; a second boto3 client `_s3_pub`
   uses the public LAN address for `generate_presigned_url` so browser-facing
   URLs contain the resolvable host, not the internal Docker `minio` hostname.
   Set in `docker-compose.yml` via `${WINGRC_STORAGE_PUBLIC_ENDPOINT:-}`.

FIPS deployment (not yet built, see roadmap): requires UBI 9 base image,
MinIO FIPS build (BoringCrypto), TLS everywhere, PostgreSQL `scram-sha-256`.
Auth must use `PBKDF2-HMAC-SHA256`; bcrypt/argon2/scrypt are not
CMVP-certified. `docs/fips.md` contains the full deployment checklist and
the ready-to-use SC.L2-3.13.11 SSP implementation text for customers.

### Bundle export (current session)
`GET /orgs/{org_id}/assessments/{assessment_id}/bundle` → ZIP archive.

**Point-in-time integrity**: SPRS recomputed first, all data copied to frozen
`BundleSnapshot` dataclasses (no ORM objects), logo and evidence file bytes
fetched from MinIO and embedded, `generated_at` stamped last.
`render_bundle(snapshot)` is a pure function — no DB or storage calls.

**ZIP layout**:
```
{org_slug}_{date}/
  index.html                    — table of contents
  cover.html                    — org profile, SPRS score, logo (base64)
  ssp/
    01_system_description.html  — SSP Section 1 narrative
    02_implementation.html      — per-control [a]/[b]/[c] statements, RACI, evidence
    03_personnel.html           — contacts with documentation roles
    04_component_inventory.html — scoped assets/users, with approval records
    05_customer_responsibility_matrix.html — RACI flattened to a matrix
    06_documents.html           — approved policies, rendered from Markdown (N.2)
    system_security_plan.pdf    — the consolidated SSP (WeasyPrint)
  lists/
    index.html                  — which lists the operator chose, by list_version id (L.3)
    {list_key}_v{n}.xlsx        — one per chosen list; none by default
  evidence/
    manifest.html               — per-objective evidence index with zip paths
    files/                      — embedded evidence file bytes
  summary/
    scoring.html                — SPRS deduction table, family breakdown
    outstanding.html            — gaps, open tasks, open findings
```

Evidence files are embedded as bytes (not presigned URLs) so the bundle remains
valid after URL expiry and works in air-gapped delivery. HTML uses inline CSS
with `@media print` — no external dependencies.

`StorageClient.get_bytes(key)` is a non-abstract method (default `b""`);
`MinIOClient` overrides with `get_object`. Existing `InMemoryStorageClient`
stubs in other test files inherit the default and are unaffected.

---

## Data model

A hand-maintained schema copy drifts by construction — this file held a
table inventory frozen at migration 0017 for weeks while ~15 tables were
added, and `roles.ts`'s own header admits the same failure mode for its
mirrored role constants. Not repeating it here: **`backend/app/models.py`
is the schema**; `backend/app/migrations/versions/` is its history. The
relationships and enum values a reader needs are already covered where
they're used — "The five layers" above for how the pieces fit together,
"Hard rules" for the status/responsibility semantics that actually matter
(`pending_evidence`→`met`, `customer_owns` vs `provider_satisfies`,
`platform_only` exclusion), and the "Assessment engine" section above for
the SPRS rollup rule.

---

## Roadmap — priority order

Do not build a slice until its prerequisites ship and tests pass.

### 1. Bundle export ✅ DONE (this session)
`GET …/bundle` → ZIP. See above.

### 2. Auth / RBAC — substantially built, completion tracked separately

**Plan and live status: `docs/PLAN-auth-rbac-completion.md`** (slices I.1–I.9).
Read that file before touching auth. Do not re-derive the design from this entry,
and do not infer completion status from it — the plan header is the only source
of truth for what has landed.

Shipped (migrations 0015–0017): local login + Entra SSO; session cookies
(`wingrc_session`, HttpOnly/SameSite=Lax, Secure in prod) and
`Authorization: Bearer wingrc_<token>` API tokens — NOT JWT. PBKDF2-HMAC-SHA256
@ 600k iterations. TOTP MFA with backup codes. Exponential-backoff lockout.
HIBP k-anonymity check. RLS via `app.current_org` with SECURITY DEFINER
functions on a pinned search_path. `wingrc_app` role, NOBYPASSRLS.

**Always set the org through `rls.set_current_org(session, org_id)` — never
write `app.current_org` directly.** That GUC is transaction-scoped, so a
commit discards it and any read afterwards matches zero rows under RLS;
`rls.py`'s `after_begin` hook re-applies the value the chokepoint recorded,
which is what makes a post-commit `refresh()`/`select()` work at all. A
direct write leaves the recorded value stale and the hook would then
re-apply the *wrong* org — `tests/test_rls_context.py` fails the build on
one. **The app connects as `wingrc_app` — a non-owner role that cannot bypass
RLS (cutover 2026-09-29).** Tenant isolation is enforced by the database,
not by router filtering: a query that forgets its org scope returns nothing
rather than another tenant's rows. Every org-scoped table, directly or
transitively, is policy-gated (migrations 0060/0061).

Migrations still need the owner, via `WINGRC_MIGRATION_DATABASE_URL` —
which is why there are two connection strings, and why consolidating them
back would break the next deploy. The CLI also runs as the owner and
privileged commands need an explicit override; see `docs/deployment.md`'s
"Database roles" section for both, and `docs/roadmap.md`'s Done entries for
the reasoning. Router-level
guards on all routers. Multi-org access (`org_membership`, per-membership
role — see `docs/adr/0009-multi-org-user-access.md`) is what `require_org_access()`
actually enforces; a role is not a fixed property of a `User` row.

Roles: `msp_admin`, `consultant_admin`, `msp_engineer`, `customer_poc`,
`c3pao_assessor`. `consultant_admin` (migration 0034) is a restricted
platform role for an external consultant (e.g. a C3PAO hired to help, not
to assess): full access to compliance data, no access to identity/security
administration (user management, API tokens, audit log). Rank sits above
`msp_engineer`, below `msp_admin`. Not auto-provisioned into every org the
way `msp_admin`/`msp_engineer` are — membership is per-engagement, granted
explicitly. See `docs/roadmap.md`'s Done entry for the full can/cannot
classification of every `require_role`/`require_org_access("msp_admin")`
call site.

The completion plan covers, in order: audit coverage for privilege-affecting
mutations plus a deny-by-default route harness (I.1); `c3pao_assessor` write
enforcement, currently defined but unenforced (I.2); API token role-downgrade
tracking (I.3); session inactivity timeout for 3.1.11 (I.4); password reset,
admin unlock, and password history (I.5); a hardening sweep (I.6); and the
frontend admin surface, role-aware rendering, and account self-service
(I.7–I.9).

Design constraints that override anything written elsewhere in this file or in
older notes:
- **No SMTP dependency.** One-time tokens (invite, password reset) are returned
  in the response body for out-of-band delivery. GCC High and air-gapped
  deployments have no mail relay. Disregard any "email verification" reference
  in older roadmap text.
- **PBKDF2 only.** bcrypt/argon2/scrypt are not FIPS-140-validated.
- Never log, echo, or persist a raw token, invite token, reset token, TOTP
  secret, or backup code. Hash at rest, return once in the response body, never
  in the audit log.

### 3. FIPS deployment profile
UBI 9 base image, MinIO FIPS build (BoringCrypto), TLS for app↔Postgres and
app↔MinIO, PostgreSQL `scram-sha-256`, nginx FIPS cipher suites. Startup
self-test (`fips_check.py`; `WINGRC_REQUIRE_FIPS` env var). All documented in
`docs/fips.md`.

### 4. RACI assignment UI ✅ DONE (G.7)
Shipped 2026-09-08 (`7efdbe6e3`, `c81bcfa56`) — see
`docs/PLAN-gui-restructure.md`'s G.7 section for the full writeup. New
`backend/app/routers/raci.py` (there was no RACI API surface at all
before this) plus `frontend/src/components/RolesPanel.tsx`, under
Assessments (moved from Scope 2026-09-09 — RACI is assessment-scoped data,
not org-scoped, per that session's own writeup):
family-level bulk-assign cascades to every child control state without
clobbering objective-level overrides already in place (checked per
`(control_state_id, raci_letter)`, not per control state), with
individual-objective override/remove alongside it. The MSP-vs-customer
suggestion is derived client-side from `ControlState.responsibility`
(already returned by the existing control-states endpoint) rather than a
second server-side lookup through `BaselineControl` — surfaced as a
sorted/labeled default in the contact picker, never auto-written.
Verified live on wl-util-1 (isolated throwaway stack, not the shared
instance): 507/507 backend tests, `tsc -b` clean, and a real browser
walkthrough confirming the override-survives-a-second-bulk-assign case
both in the UI and via a direct `psql` query, plus the G.3 dashboard's
"Open Tasks by Owner" widget populating a named contact instead of its
prior permanent "unassigned" degradation.

### 5. AI-drafted implementation statements
`POST …/assessments/{id}/objectives/{obj_id}/draft-statement` → calls the
configured AI provider with the baseline spec + scope context + existing evidence
titles as grounding. Returns draft body, stores as `ImplementationStatement`
with `status='draft'`. Human reviews → `reviewed` → `approved`. Provider
abstraction already exists: `ai/__init__.py:get_ai_provider()` resolves the
configured provider from the encrypted "ai" connector (Administration → AI
Provider), added 2026-09-19 — see that section's roadmap entry below.

### 6. SPRS score display (dashboard widget) ✅ DONE (G.3)
**Q.0 (2026-10-27): two views.** The assessment view is G.3's widgets on
a named-area grid. The environment view (`EnvironmentDashboard.tsx`,
`GET /orgs/{org_id}/environment`) lists devices and people *in the
boundary*, meaning `status='active' AND in_boundary`, because "active"
alone includes rejected entities. Its totals are `COUNT(*)`, never
`len(rows)`. Which view a role lands on is `lib/dashboardView.ts`.
Drag-and-drop and persistence are still item Q (`docs/roadmap.md`).
Shipped as part of a broader org dashboard, not a standalone widget as
originally scoped here — see `docs/PLAN-gui-restructure.md`'s G.3 section
for the full writeup (backend `routers/dashboard.py`, frontend
`OrgDashboard.tsx`). The SPRS widget itself reads `assessment.sprs_score`
plus G.2's `sprs_snapshot` table for trend, alongside eight other widgets
(family completion heatmap, statement authoring progress, evidence
expiring, needs-review queue, blocked objectives, RACI-bucketed open
tasks, POA&M summary, recent activity). Pending live wl-util-1
verification per that plan doc's own status line — do not infer this is
verified from this entry alone.

### 7. Document library / SSP templates

**Plan and live status: `docs/PLAN-document-library.md`** (slices N.1–N.5).
Read that file before touching this item. **N.1 ✅ DONE** (2026-09-24) —
`document`/`document_version`/`document_objective_tag` (migration 0059),
versioned from day one. **N.2 ✅ DONE** (2026-09-28, no migration) —
browser editing, diffs, audit surfacing. **N.3 ✅ DONE** (2026-09-30,
migration 0062) — approval records and the review cadence. See
`docs/roadmap.md`'s Done entries for all three full writeups. N.4–N.5 not
started — do not infer otherwise from this entry; the plan doc's own status
line is the only source of truth.

**Item X (2026-10-18, migration 0069): publishing attaches nothing.** A
published document is offered on its tagged objectives and a person
attaches it (`document_evidence.py`). A republish leaves every link on the
version it names, and the link reads as superseded. Do not reintroduce a
link made by publish, or an archive made by republish. **X.1** gives the
document page its tag editor (`DocumentTags.tsx`; documents could not be
tagged from any screen before) and refuses removing a tag a live link
rests on.

N.2's decisions that constrain later slices: **document bodies are
GFM-subset Markdown** and `backend/app/markdown_doc.py` is the only thing
that renders one to HTML — matched by `frontend/src/lib/markdown.tsx`,
which renders to React elements so the SPA keeps its
no-`dangerouslySetInnerHTML` invariant, with a shared corpus test
(`backend/tests/fixtures/markdown_corpus.json`) pinning the two together.
Bodies are stored verbatim and neutralised on render, the same line
`svg_sanitize.py` draws. No merged table cells (GFM cannot express them,
and no HTML escape hatch was added on purpose) and no inline images until
N.4 adds an upload path for `storage_key`. Editing is append-only with
optimistic concurrency (`base_version_id` required); there is no
autosave.

N.3's decisions, which later slices must not undo: **`document_approval` is
the single authoritative approval record**, and
`DocumentVersion.approved_at`/`approved_by_contact_id` are a denormalized
snapshot of the *initial* approval only — never read either to answer a
cadence question, because they know nothing about reaffirmations.
**Re-approving an unchanged document creates no new version**; it appends
one approval row and changes nothing else, so N.2's diff history stays
meaningful. **Overdue is derived, never stored** (`document_reviews.py`) —
there is no `next_due_at` column, because a cadence change must move the
due date. **Overdue flags, never invalidates**: evidence stays attached,
control state is untouched, the SPRS score does not move; the scheduled
digest writes only notification rows. `Document.cadence_months` is a third,
distinct cadence — not `Organization.review_cadence_months` (per-org
users/devices cycle) and not `ReviewCycle.cadence_months`.

Reconciles two prior specs that had drifted apart (`ROADMAP.md` item F and
this section's own earlier text, flagged 2026-09-07 as likely-duplicated
and never reconciled until the plan doc landed); do not re-derive the
design from either of those, they're superseded.

Org-level document store for policies, procedures, and plans that flow into
implementation statements. Versioned from the first migration (append-only
`document_version`, never mutated in place), MSP/master-org templates with
variable substitution resolved at import (not render), per-document
approval and review cadence, and environment/tool-driven suggestions — see
the plan doc for the full five-slice breakdown and the cross-cutting rules
that apply to all of them.

### 8. Connectors (Liongard / RMM scope ingestion)
API/MCP-based scope ingestion to replace manual spreadsheet upload. Liongard
inspector data → `scope_entity` records (dry-run + apply same as workbook
importer). This feeds Layer 2 (tenant setup) without manual data entry.

**D.1 ✅ DONE (2026-09-09)** — Integrations screen: credential entry +
test-connection, Liongard first (`routers/integrations.py`,
`connectors/liongard.py`, `crypto.py`). **Credentials are MSP-wide, not
per-org** — Liongard's API key is scoped to the whole MSP instance (one
Liongard user account), with per-client Environments underneath it, so
`integration_connection` is a deployment-wide table (one row per
connector), not one row per org. Encrypted at rest (Fernet, deploy-time
key, fail-closed, never persisted) — this is the MSP holding its own
credential (self-hosted deployment), not WinGRC-the-vendor holding a
customer's; see root `ROADMAP.md` item D / D.1 / **D.4** for the full
reasoning and the open question D.4 tracks for a future *hosted* WinGRC.
**D.2 ✅ DONE (2026-09-11)** — the actual `scope_entity` pull
(`importers/liongard.py`), routed through the same dry-run/apply review
flow the workbook importer uses. See `docs/roadmap.md`'s Done entry for
the full writeup. **D.3, both halves, ✅ DONE.** First half — periodic
review & attestation — shipped 2026-09-13, `backend/app/review_cycles.py`
+ `routers/review_cycles.py`. **Only a completed cycle's record is
attached** to AC.L2-3.1.1[a]/[c] (2026-10-19, migration 0070): a deadline
or undeliverable close writes and keeps its record, names its cycle
(`evidence.source_review_cycle_id`), shows it on the cycle's page and in
the bundle's unlinked evidence, and attaches nothing -- and is never
offered, since it says nobody reviewed. **The bundle lists evidence whose
links were all archived** (a deactivation, or such a record detached),
as archived with where from, when and why, never as current evidence
(ADR 0007's amendment). Second half — daily Liongard sync +
asset/user onboarding approval — shipped 2026-09-18:
`backend/app/liongard_sync.py` + `routers/liongard_sync.py` +
`scheduler.py:_liongard_daily_sync`, migration 0056. A brand-new
Liongard-observed entity lands `pending_approval` (`domain.py:
EntityStatus`), never silently active — a human approves (→ `active`) or
rejects (→ `active`, `in_boundary=false`) through an authenticated
session; the checklist shown at approval is the org's activated Tools,
reviewer-confirmed by hand (no per-device Liongard-metrics pull exists in
`connectors/liongard.py` — that's a separate, explicitly deferred future
slice, not guessed at). See `docs/roadmap.md`'s Done entry and root
`ROADMAP.md`'s D.3 section (amended in place) for the full design
writeup.

### 9. Evidence task enhancements

**(a) Task assignment to contacts** — add nullable `assigned_to UUID FK → contact`
to `evidence_task`. Schema change is one column + one index; add after auth
lands so the assignee becomes a real login target for "my tasks" and email
notification.

**(b) Recurrence engine** — `AssessmentObjective.cadence` (annual/quarterly/
monthly) and `EvidenceTask.due_date` already exist. Auto-regenerate a new task
when the current one is marked collected and the next due date is within the
collection window. Tracks `last_completed_at`, `next_due_at`; flags overdue/
at-risk. Pushes recurring tickets to PSA (Autotask) via outbound webhook.
Prerequisites: task assignment, auth.

### 10. Continuous-compliance dashboard
Live posture view: SPRS trend, overdue tasks, controls regressing from met →
needs_review, coverage gap heatmap by family. Powered by `ControlStateHistory`
and `AuditLog`. Alerts via webhook (Teams/Slack) when SPRS drops or a control
regresses.
