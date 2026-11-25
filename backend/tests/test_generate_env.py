"""deploy/generate-env.sh fills only what it is told to, and says what it
will fill before it writes.

2026-11-25: run on live to fill the two storage keys, it also filled
WINGRC_APP_DB_PASSWORD, which live's .env did not have. The names are now
required; `--fresh` fills them all and only on a new .env
(docs/roadmap.md, "The app reaches MinIO as root", the move).

Needs bash and python3 (Linux; CI's backend job, or wl-util-1).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "generate-env.sh"

SECRETS = [
    "POSTGRES_PASSWORD", "WINGRC_APP_DB_PASSWORD", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD",
    "WINGRC_AUTH_FLOW_SECRET", "WINGRC_CREDENTIAL_ENCRYPTION_KEYS",
    "WINGRC_STORAGE_ACCESS_KEY", "WINGRC_STORAGE_SECRET_KEY",
]

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not SCRIPT.exists()
    or shutil.which("bash") is None or shutil.which("python3") is None,
    reason="needs deploy/ (not in the image), bash and python3",
)


def _run(env: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SCRIPT), "--env-file", str(env), *args],
                          capture_output=True, text=True, timeout=30)


def _values(env: Path) -> dict[str, str]:
    out = {}
    for line in env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


# Live's .env at the move, in shape: every secret set except the two storage
# keys, emptied for the move, and WINGRC_APP_DB_PASSWORD absent, because
# WINGRC_DATABASE_URL is set outright.
LIVE_AT_THE_MOVE = "\n".join([
    "WINGRC_ENVIRONMENT=production",
    "POSTGRES_PASSWORD=set", "MINIO_ROOT_USER=wingrc", "MINIO_ROOT_PASSWORD=set",
    "WINGRC_AUTH_FLOW_SECRET=set", "WINGRC_CREDENTIAL_ENCRYPTION_KEYS=local:set",
    "WINGRC_DATABASE_URL=postgresql+psycopg://wingrc_app:set@db:5432/wingrc",
    "WINGRC_STORAGE_ACCESS_KEY=", "WINGRC_STORAGE_SECRET_KEY=",
]) + "\n"


def test_the_live_move_fills_the_two_named_keys_and_nothing_else(tmp_path):
    env = tmp_path / ".env"
    env.write_text(LIVE_AT_THE_MOVE)
    r = _run(env, "WINGRC_STORAGE_ACCESS_KEY", "WINGRC_STORAGE_SECRET_KEY")
    assert r.returncode == 0, r.stderr
    v = _values(env)
    assert "WINGRC_APP_DB_PASSWORD" not in v
    assert v["WINGRC_STORAGE_ACCESS_KEY"] == "wingrc-app"
    assert v["WINGRC_STORAGE_SECRET_KEY"]
    assert r.stdout.splitlines() == [
        "would fill: WINGRC_STORAGE_ACCESS_KEY (empty), WINGRC_STORAGE_SECRET_KEY (empty)",
        "filled: WINGRC_STORAGE_ACCESS_KEY WINGRC_STORAGE_SECRET_KEY",
    ]
    assert v["WINGRC_STORAGE_SECRET_KEY"] not in r.stdout + r.stderr


def test_with_no_names_it_refuses_and_writes_nothing(tmp_path):
    env = tmp_path / ".env"
    env.write_text(LIVE_AT_THE_MOVE)
    r = _run(env)
    assert r.returncode == 2
    assert "name the secrets to fill" in r.stderr
    assert env.read_text() == LIVE_AT_THE_MOVE


def test_dry_run_says_what_it_would_fill_and_writes_nothing(tmp_path):
    env = tmp_path / ".env"
    env.write_text(LIVE_AT_THE_MOVE)
    r = _run(env, "--dry-run", "WINGRC_STORAGE_SECRET_KEY", "WINGRC_APP_DB_PASSWORD")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[0] == (
        "would fill: WINGRC_STORAGE_SECRET_KEY (empty), WINGRC_APP_DB_PASSWORD (absent)"
    )
    assert env.read_text() == LIVE_AT_THE_MOVE


def test_a_name_already_set_is_refused_not_replaced(tmp_path):
    env = tmp_path / ".env"
    env.write_text(LIVE_AT_THE_MOVE)
    r = _run(env, "WINGRC_STORAGE_ACCESS_KEY", "POSTGRES_PASSWORD")
    assert r.returncode == 2
    assert "already set, never replaced: POSTGRES_PASSWORD" in r.stderr
    assert env.read_text() == LIVE_AT_THE_MOVE


def test_a_name_it_does_not_generate_is_refused(tmp_path):
    env = tmp_path / ".env"
    env.write_text(LIVE_AT_THE_MOVE)
    r = _run(env, "WINGRC_ENVIRONMENT")
    assert r.returncode == 2
    assert "not a secret this script generates: WINGRC_ENVIRONMENT" in r.stderr
    assert env.read_text() == LIVE_AT_THE_MOVE


def test_fresh_fills_every_secret_in_a_new_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text((REPO / ".env.example").read_text())
    r = _run(env, "--fresh")
    assert r.returncode == 0, r.stderr
    v = _values(env)
    assert all(v.get(n) for n in SECRETS), [n for n in SECRETS if not v.get(n)]
    assert r.stdout.splitlines()[-1] == "filled: " + " ".join(SECRETS)


def test_fresh_on_a_new_path_creates_it_0600(tmp_path):
    env = tmp_path / ".env"
    r = _run(env, "--fresh")
    assert r.returncode == 0, r.stderr
    assert env.stat().st_mode & 0o777 == 0o600


def test_fresh_refuses_an_env_that_is_not_new(tmp_path):
    """On live this is the run that would have filled more than asked."""
    env = tmp_path / ".env"
    env.write_text(LIVE_AT_THE_MOVE)
    r = _run(env, "--fresh")
    assert r.returncode == 2
    assert "this .env is not new" in r.stderr
    assert env.read_text() == LIVE_AT_THE_MOVE
