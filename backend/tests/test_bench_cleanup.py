"""scripts/bench-cleanup.sh: the one command Jarrod runs with sudo after a
bench cycle. It acts as root, so what it must never touch is the test:
live's snapshot directory, another project's, a symlink's target, and
anything a running container mounts. And under the bench home it hands
entries back to claude; it never removes them.

A non-owner is simulated (BENCH_OWNER names a user who owns nothing here,
BENCH_CHOWN_TO the test's own user), so the test runs unprivileged. Linux
only: the script is GNU find/stat/realpath/chown.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "bench-cleanup.sh"

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not SCRIPT.exists() or shutil.which("bash") is None,
    reason="the script runs on wl-util-1 (Linux); not in the image",
)


def _tree(tmp: Path) -> dict[str, Path]:
    snap, home, outside = tmp / "snap", tmp / "bench", tmp / "outside"
    for d in (snap / "wingrc", snap / "wingrc_verify_s3a", snap / "wingrc_other",
              home / "s3a" / ".bench" / "snapshots", outside):
        d.mkdir(parents=True)
    (snap / "wingrc" / "live-set").write_text("live")
    (snap / "wingrc_verify_s3a" / "bench-set").write_text("bench")
    (home / "s3a" / ".bench" / "snapshots" / "set").write_text("bench")
    (outside / "keep").write_text("not the bench's")
    (home / "link-out").symlink_to(outside, target_is_directory=True)
    return {"snap": snap, "home": home, "outside": outside}


def _run(t: dict[str, Path], *args: str, mounted: str = "") -> subprocess.CompletedProcess:
    env = {
        **os.environ, "BENCH_HOME": str(t["home"]), "SNAP_ROOT": str(t["snap"]),
        "BENCH_OWNER": "root" if os.geteuid() != 0 else "nobody",
        "BENCH_CHOWN_TO": str(os.geteuid()), "BENCH_CHOWN_GROUP": str(os.getegid()),
        "BENCH_CLEANUP_TEST": "1", "BENCH_CLEANUP_MOUNTED": mounted,
    }
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True,
                          text=True, timeout=30)


def test_it_removes_a_bench_projects_snapshot_dir_and_never_lives_or_another(tmp_path):
    t = _tree(tmp_path)
    r = _run(t)
    assert r.returncode == 0, r.stderr
    assert not (t["snap"] / "wingrc_verify_s3a").exists()
    assert (t["snap"] / "wingrc" / "live-set").read_text() == "live"
    assert (t["snap"] / "wingrc_other").exists()


def test_under_the_bench_home_it_hands_back_and_removes_nothing(tmp_path):
    t = _tree(tmp_path)
    r = _run(t)
    assert r.returncode == 0, r.stderr
    assert (t["home"] / "s3a" / ".bench" / "snapshots" / "set").read_text() == "bench"
    assert f"handed back to {os.geteuid()}" in r.stdout
    assert str(t["home"] / "s3a") in r.stdout


def test_a_symlink_is_neither_acted_on_nor_followed(tmp_path):
    t = _tree(tmp_path)
    r = _run(t)
    assert r.returncode == 0, r.stderr
    assert (t["outside"] / "keep").read_text() == "not the bench's"
    assert (t["home"] / "link-out").is_symlink()
    assert "link-out" not in r.stdout


def test_a_path_a_running_container_mounts_is_skipped(tmp_path):
    t = _tree(tmp_path)
    mounted = str(t["snap"] / "wingrc_verify_s3a")
    r = _run(t, mounted=mounted)
    assert r.returncode == 0, r.stderr
    assert (t["snap"] / "wingrc_verify_s3a" / "bench-set").exists()
    assert "a running container mounts it" in r.stdout


def test_dry_run_changes_nothing(tmp_path):
    t = _tree(tmp_path)
    r = _run(t, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert (t["snap"] / "wingrc_verify_s3a" / "bench-set").exists()
    assert "would remove" in r.stdout and "would hand back" in r.stdout


def test_without_sudo_it_refuses(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("running as root")
    t = _tree(tmp_path)
    env = {**os.environ, "BENCH_HOME": str(t["home"]), "SNAP_ROOT": str(t["snap"])}
    env.pop("BENCH_CLEANUP_TEST", None)
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 2, "fixture must not run as root, or this test proves nothing"
    assert (t["snap"] / "wingrc_verify_s3a").exists()
