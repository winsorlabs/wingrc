"""Which code is running, per component, and whether that is one commit.

A deployment is three images built separately -- `backend`, `worker` and
`nginx` -- so it can run three commits at once. `/health` used to report a
single `build`, the backend's own `WINGRC_BUILD_SHA`, and during a split
(2026-10-26: nginx at V.1, backend and worker on #52) it said `ok` with
one commit in it. It was not wrong about the backend; one field could not
be right about the other two. So each component reports its own commit,
and the verdict is computed over all of them.

Where each commit comes from -- each is the component's own value, never
another's:

- **backend**: `WINGRC_BUILD_SHA` in this process's environment, baked into
  the image as a build arg (backend/Dockerfile).
- **nginx**: the same build arg, baked into the nginx image as `ENV` and
  substituted into its config at container start, which sends it on every
  `/api/` request as `X-WinGRC-Nginx-Build`. It is present only when the
  request came through nginx; a direct call to `backend:8000` (the
  container's own healthcheck) sees no nginx and says so. nginx's
  `proxy_set_header` replaces any value a client sends.
- **worker**: the worker process names its database connections
  `wingrc-worker@<its WINGRC_BUILD_SHA>` (`cli.worker`), and `/health` reads
  the names of the connections open now from `pg_stat_activity`. That
  observes a worker that is connected at this moment, not a record of one
  that once ran. `/health`'s own connection carries no such name, so the
  check cannot find itself. Its absence means no connection is *named* as
  a worker: a worker built before this naming is running and connected,
  and reads `unknown` too (seen on the bench, 2026-10-26), so the reason
  given says only what was observed.

A commit nobody reported reads `unknown`, never the backend's value.
"""

from __future__ import annotations

COMPONENTS = ("backend", "nginx", "worker")
UNKNOWN = "unknown"
NGINX_BUILD_HEADER = "X-WinGRC-Nginx-Build"
WORKER_APP_NAME_PREFIX = "wingrc-worker@"

# pg_stat_activity is read once per transaction and then held: a second read
# in the same transaction sees the first one's connections (measured on the
# bench, 2026-10-26). Cleared first, so /health reports the connections open
# now even if its session is already inside a transaction.
CLEAR_SNAPSHOT_SQL = "SELECT pg_stat_clear_snapshot()"
WORKER_BUILDS_SQL = (
    "SELECT DISTINCT application_name FROM pg_stat_activity "
    "WHERE datname = current_database() AND application_name LIKE 'wingrc-worker@%'"
)


def worker_application_name(build: str) -> str:
    return f"{WORKER_APP_NAME_PREFIX}{build}"


def worker_builds(application_names: list[str]) -> list[str]:
    return sorted(
        n[len(WORKER_APP_NAME_PREFIX):]
        for n in application_names
        if n.startswith(WORKER_APP_NAME_PREFIX)
    )


def render_component(observed: list[str]) -> str:
    """One string per component for the response: the commit, `unknown`
    when nothing reported one, or every commit when several did (two
    workers on different images)."""
    if not observed:
        return UNKNOWN
    return ", ".join(observed)


def deployment_verdict(
    observed: dict[str, list[str]], why_unknown: dict[str, str]
) -> tuple[str, str | None]:
    """`ok` only when every component in COMPONENTS reported exactly one
    known commit and they are all the same commit.

    Iterates COMPONENTS, not `observed`, so a component missing from the
    input counts as unknown rather than being left out of the comparison:
    a verdict over fewer components than the deployment has is the defect
    this module exists to remove, one layer up. Tested by
    `test_health.py`'s disagreement tests, mutation-checked.

    Returns (status, detail). `detail` names each component's commit, so
    whoever reads it during an incident has the values, not a summary.
    """
    parts = []
    unknown = []
    for c in COMPONENTS:
        commits = observed.get(c) or []
        if not commits or UNKNOWN in commits:
            unknown.append(c)
            parts.append(f"{c} unknown ({why_unknown.get(c, 'nothing reported a commit')})")
        else:
            parts.append(f"{c} {render_component(commits)}")
    summary = "; ".join(parts)
    if unknown:
        return "incomplete", f"commit not known for {', '.join(unknown)}: {summary}"
    distinct = {sha for c in COMPONENTS for sha in observed[c]}
    if len(distinct) > 1:
        return "split", f"components are on different commits: {summary}"
    return "ok", None
