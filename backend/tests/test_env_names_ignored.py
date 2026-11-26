"""Every copy of .env that a tracked file names, or tells someone to make,
is ignored by git, checked by git itself rather than by reading the
patterns.

2026-11-25: the move off MinIO's root said `cp -p .env
.env.pre-storage-account`. .gitignore had `.env.*.bak` and `.env.bak`,
which do not match it, so live's root credentials, database password and
Fernet key would have sat untracked in a public repo's working tree,
one `git add -A` from a commit. Jarrod caught it with `git check-ignore`.
.gitignore now ignores `.env.*` except the template.

Reads the repo's tracked files, so it needs git and a checkout (CI's
backend job; not the image).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO / ".git").exists(),
    reason="needs git and the repository's checkout",
)

# `.env.<suffix>`: a file name. `.env-grid` and the like are CSS classes.
NAME = re.compile(r"(?<![\w./-])\.env\.[A-Za-z0-9_][A-Za-z0-9_.-]*")
# `cp`/`mv` with options, then a source and a target.
COPY = re.compile(r"\b(?:cp|mv)((?:\s+-[A-Za-z]+)*)\s+(\S+)\s+(\S+)")
TEMPLATE = ".env.example"
GUIDE = "docs/deployment.md"


def _tracked_text() -> dict[str, str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                         check=True).stdout.decode().split("\0")
    texts = {}
    for rel in filter(None, out):
        try:
            texts[rel] = (REPO / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
    return texts


def _ignored(name: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", "--no-index", name],
                          cwd=REPO).returncode == 0


def _clean(token: str) -> str:
    return token.lstrip("`'\"(").rstrip("`'\".,;:)")


def _names(texts: dict[str, str]) -> dict[str, str]:
    found = {}
    for rel, text in texts.items():
        for m in NAME.finditer(text):
            found.setdefault(m.group(0).rstrip("."), rel)
    return found


def _copy_targets(texts: dict[str, str]) -> dict[str, str]:
    found = {}
    for rel, text in texts.items():
        for m in COPY.finditer(text):
            # "`cp .env` target" is prose: the code span closes at the source.
            if m.group(2).endswith("`"):
                continue
            src, dst = _clean(m.group(2)), _clean(m.group(3))
            if src == ".env":
                found.setdefault(dst, rel)
    return found


def test_every_env_name_in_a_tracked_file_is_ignored():
    texts = _tracked_text()
    names = _names(texts)
    assert ".env.pre-storage-account.bak" in _names({GUIDE: texts[GUIDE]}), (
        "fixture must find the move's backup name in the guide, or this test proves nothing"
    )
    not_ignored = {n: f for n, f in names.items() if n != TEMPLATE and not _ignored(n)}
    assert not not_ignored, f"named in a tracked file and not ignored by git: {not_ignored}"


def test_every_cp_of_env_lands_on_an_ignored_name():
    texts = _tracked_text()
    targets = _copy_targets(texts)
    assert ".env.pre-storage-account.bak" in _copy_targets({GUIDE: texts[GUIDE]}), (
        "fixture must find the move's `cp -p .env` step, or this test proves nothing"
    )
    inside = {t: f for t, f in targets.items() if not t.startswith(("/", "~", "$"))}
    not_ignored = {t: f for t, f in inside.items() if not _ignored(t)}
    assert not not_ignored, f"`cp .env` into the tree, not ignored by git: {not_ignored}"


def test_env_is_ignored_and_the_template_is_tracked():
    assert _ignored(".env")
    assert not _ignored(TEMPLATE)
    tracked = subprocess.run(["git", "ls-files", TEMPLATE], cwd=REPO, capture_output=True,
                             text=True, check=True).stdout.strip()
    assert tracked == TEMPLATE
