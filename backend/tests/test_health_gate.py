"""deploy/health-gate.sh: the only thing deploy.sh's wait loop asks before
it says "deployed".

The gate existed so that a split or incomplete deploy could not read as
done. Until 2026-11-06 it grepped the /health body for '"status":"ok"',
and item S's snapshots block put a nested "status":"ok" in the same body,
so every body passed. The guard was not changed; something landed beside
it. These bodies are the test that fails when its meaning changes
(docs/roadmap.md, "The deploy gate passed what it existed to refuse").
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "deploy" / "health-gate.sh"
DEPLOY = REPO / "deploy" / "deploy.sh"
SHA = "5db1201a5f9a0afe2fc1f6afb680ac8a517e1e50"
OLD = "75fb484e75018fa40db1d3b434a0a87fe7c8eb69"
SNAPSHOTS_OK = {
    "max_age_hours": 50, "newest": "20261106T140057Z", "age_hours": 0.0, "count": 7,
    "status": "ok", "attention": False,
}


def _body(status, backend=SHA, nginx=SHA, worker=SHA, **extra):
    return {
        "status": status, "app": "WinGRC", "version": "0.1.0", "snapshots": SNAPSHOTS_OK,
        "components": {"backend": backend, "nginx": nginx, "worker": worker},
        "migration": "0073_list_reference_columns", "database": "ok", **extra,
    }


def _run(stdin: str, sha: str = SHA) -> subprocess.CompletedProcess:
    bash = shutil.which("bash")
    if bash is None or not GATE.exists():
        pytest.skip("needs bash and deploy/ (an image build has no deploy/)")
    env = {**os.environ, "PYTHON": sys.executable}
    return subprocess.run(
        [bash, str(GATE), sha], input=stdin, text=True, capture_output=True, env=env
    )


def _deploy_sh() -> str:
    if not DEPLOY.exists():
        pytest.skip("needs deploy/ (an image build has no deploy/)")
    return DEPLOY.read_text(encoding="utf-8")


def _gate(stdin: str, sha: str = SHA) -> int:
    return _run(stdin, sha).returncode


# The body live's /health returned during the 2026-11-06 deploy, as
# deploy.sh printed it before saying "deployed": the worker had not
# reported yet, and the snapshots block said ok.
LIVE_2026_11_06 = _body(
    "incomplete", worker="unknown",
    detail=f"commit not known for worker: backend {SHA}; nginx {SHA}; worker unknown",
)


@pytest.mark.parametrize(
    "body",
    [
        LIVE_2026_11_06,
        _body("split", nginx=OLD, detail="components differ"),
        _body("incomplete", nginx="unknown"),
    ],
    ids=["live-2026-11-06-incomplete", "split", "incomplete-nginx"],
)
def test_refuses_a_split_or_incomplete_deploy_whose_snapshots_say_ok(body):
    assert '"status": "ok"' in json.dumps(body), (
        "fixture must carry a nested status ok, or this test proves nothing"
    )
    assert _gate(json.dumps(body)) == 1
    assert _gate(json.dumps(body, separators=(",", ":"))) == 1


def test_accepts_only_ok_with_every_component_on_the_commit():
    assert _gate(json.dumps(_body("ok"))) == 0
    assert _gate(json.dumps(_body("ok", backend=OLD))) == 1
    assert _gate(json.dumps(_body("ok", worker=OLD))) == 1
    assert _gate(json.dumps(_body("ok")), sha=OLD) == 1


@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "<html>502 Bad Gateway</html>",
        json.dumps({"snapshots": {"status": "ok"}, "components": {"backend": SHA}}),
        json.dumps([{"status": "ok"}]),
    ],
    ids=["empty", "not-json", "no-top-level-status", "not-an-object"],
)
def test_refuses_a_body_it_cannot_read_as_a_finished_deploy(stdin):
    assert _gate(stdin) == 1


def test_deploy_sh_asks_the_gate_and_matches_no_status_text():
    """The gate is only a gate if the loop uses it. Reverting the loop to
    grep the body turns this red."""
    text = _deploy_sh()
    loop = text[text.index("waiting for /health") : text.index('echo "deployed $sha"')]
    assert 'bash deploy/health-gate.sh "$sha"' in loop
    assert not re.search(r"grep[^\n]*status", loop)


@pytest.mark.parametrize(
    ("stdin", "reason"),
    [
        ("", "no JSON body (empty)"),
        ("<html>502 Bad Gateway</html>", "no JSON body (28 bytes)"),
        (json.dumps([{"status": "ok"}]), "body is not a JSON object"),
        (
            json.dumps(LIVE_2026_11_06),
            "status incomplete: backend 5db1201a5f9a; nginx 5db1201a5f9a; worker unknown",
        ),
        (
            json.dumps(_body("ok", worker=OLD)),
            "status ok but not all on 5db1201a5f9a: "
            "backend 5db1201a5f9a; nginx 5db1201a5f9a; worker 75fb484e7501",
        ),
        (
            json.dumps({"status": "starting"}),
            "status starting: backend missing; nginx missing; worker missing",
        ),
    ],
    ids=["empty", "html", "array", "live-2026-11-06", "ok-old-worker", "no-components"],
)
def test_a_refusal_says_why_in_one_line(stdin, reason):
    """deploy.sh logs this line for every refused try, so the record shows
    what the wait saw (2026-11-06's worker was about 31 s behind)."""
    run = _run(stdin)
    assert run.returncode == 1
    assert run.stderr == reason + "\n"
    assert run.stdout == ""


def test_an_accepted_body_prints_nothing():
    run = _run(json.dumps(_body("ok")))
    assert (run.returncode, run.stdout, run.stderr) == (0, "", "")


def test_deploy_sh_logs_each_refused_try_with_the_reason_and_records_it():
    """Reverting the loop to drop the reason, or the record's wait lines,
    turns this red."""
    text = _deploy_sh()
    loop = text[text.index("waiting for /health") : text.index('echo "deployed $sha"')]
    assert "reason=\"$(printf '%s' \"$body\" | bash deploy/health-gate.sh \"$sha\" 2>&1)\"" in loop
    assert 'refused: ${reason' in text
    assert 'waited+="$line"' in text
    record = text[text.index("record_deploy() {") : text.index('} > "$dir/deploy.txt"')]
    assert '"$waited"' in record
