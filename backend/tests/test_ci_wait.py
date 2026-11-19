"""scripts/ci_wait.py answers about the commit it was given, by its jobs.

A wait that re-read HEAD each poll reported on whatever was checked out
(docs/roadmap.md, "The CI wait read the working tree"). These bodies are
the ones that would fool a check that did not compare what it read with
what it was asked.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ci_wait.py"
ASKED = "a" * 40
OTHER = "b" * 40


@pytest.fixture(scope="module")
def ci_wait():
    if not SCRIPT.exists():
        pytest.skip("scripts/ is outside this tree (the backend image); CI runs this")
    spec = importlib.util.spec_from_file_location("ci_wait", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run(sha, id_, path=".github/workflows/ci.yml", attempt=1):
    return {"id": id_, "head_sha": sha, "path": path, "run_attempt": attempt}


def _jobs(**states):
    out = []
    for name, s in states.items():
        if s in ("queued", "in_progress"):
            out.append({"name": name, "status": s, "conclusion": None})
        else:
            out.append({"name": name, "status": "completed", "conclusion": s})
    return {"jobs": out}


def test_a_run_for_another_commit_is_not_an_answer(ci_wait):
    body = {"workflow_runs": [_run(OTHER, 9)]}
    assert ci_wait.pick_run(body, ASKED) is None


def test_the_newest_run_for_the_asked_commit(ci_wait):
    body = {"workflow_runs": [_run(ASKED, 5), _run(OTHER, 9), _run(ASKED, 7),
                              _run(ASKED, 8, path=".github/workflows/other.yml")]}
    assert ci_wait.pick_run(body, ASKED)["id"] == 7


@pytest.mark.parametrize(
    "jobs,expected",
    [
        (dict(backend="success", integration="success", image="success"), "passed"),
        (dict(backend="success", integration="failure", image="skipped"), "failed"),
        (dict(backend="success", integration="success", image="skipped"), "failed"),
        (dict(backend="success", integration="in_progress"), "pending"),
        (dict(backend="success", integration="success"), "pending"),
        (dict(backend="failure", integration="in_progress"), "failed"),
        (dict(backend="success", integration="success", image="success", other="failure"),
         "passed"),
        ({}, "pending"),
    ],
)
def test_verdict_reads_the_three_required_jobs(ci_wait, jobs, expected):
    assert ci_wait.verdict(_jobs(**jobs))[0] == expected
