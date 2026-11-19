"""Wait for CI on one named commit and report its required jobs.

    py -3 scripts/ci_wait.py <commit>      (a SHA, a branch, origin/x, ...)

The commit is resolved once, when the script starts, to a full SHA, and
every poll asks about that SHA. A wait written as a one-liner on
2026-11-19 set `sha=` and then queried `git rev-parse HEAD` on every
poll, so a branch switch mid-wait made it report on a different commit
(a34a7b4 had to be re-checked by its fixed ID). This takes no reading of
the working tree after the start.

What it reports is checked against what it was asked:
- a run counts only when its `head_sha` equals the resolved SHA;
- it passes only when each required job (`REQUIRED`, the ruleset's three)
  ran and concluded `success`. A skipped job is not a passed one, and a
  run's own conclusion is not read in place of its jobs'.

Exit 0 when all required jobs succeeded, 1 when any did not, 2 when no
answer came before --timeout. Unauthenticated: 60 requests an hour, so it
polls every 60 s by default (CLAUDE.md, "Reading CI status needs no
credential").
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request

REPO = "winsorlabs/wingrc"
WORKFLOW = ".github/workflows/ci.yml"
REQUIRED = ("backend", "integration", "image")


def resolve(commit: str) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 -- fixed https host
        return json.load(r)


def pick_run(runs_body: dict, sha: str) -> dict | None:
    """The newest CI run for exactly this SHA, or None."""
    runs = [
        r for r in runs_body.get("workflow_runs", [])
        if r.get("head_sha") == sha and r.get("path", "").startswith(WORKFLOW)
    ]
    return max(runs, key=lambda r: (r.get("run_attempt", 1), r["id"]), default=None)


def verdict(jobs_body: dict) -> tuple[str, dict[str, str]]:
    """('pending' | 'passed' | 'failed', {job: state}) from a run's jobs."""
    jobs = {j["name"]: j for j in jobs_body.get("jobs", [])}
    states: dict[str, str] = {}
    finished: dict[str, str] = {}
    for name in REQUIRED:
        j = jobs.get(name)
        if j is None:
            states[name] = "not created yet"
        elif j.get("status") != "completed":
            states[name] = j.get("status") or "unknown"
        else:
            states[name] = finished[name] = j.get("conclusion") or "unknown"
    if any(c != "success" for c in finished.values()):
        return "failed", states
    if len(finished) == len(REQUIRED):
        return "passed", states
    return "pending", states


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("commit")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args(argv)

    sha = resolve(a.commit)
    print(f"waiting on {sha} ({a.commit})", flush=True)
    deadline = time.monotonic() + a.timeout
    base = f"https://api.github.com/repos/{REPO}/actions/runs"
    while True:
        run = pick_run(_get(f"{base}?head_sha={sha}"), sha)
        if run is not None:
            state, jobs = verdict(_get(f"{base}/{run['id']}/jobs"))
            if state != "pending":
                print(f"run {run['id']} on {run['head_sha']}: {state}")
                for name, s in jobs.items():
                    print(f"  {name}: {s}")
                return 0 if state == "passed" else 1
        if time.monotonic() > deadline:
            print(f"no result for {sha} within {a.timeout}s")
            return 2
        time.sleep(a.interval)


if __name__ == "__main__":
    sys.exit(main())
