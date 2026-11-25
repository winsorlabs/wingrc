#!/usr/bin/env bash
# Fill named required secrets in a .env with fresh random values. Says what
# it will fill, and whether each name is absent or empty, before it writes.
# Never prints a value.
#
#   deploy/generate-env.sh [--dry-run] [--env-file PATH] NAME...
#   deploy/generate-env.sh [--dry-run] [--env-file PATH] --fresh
#
# NAME...  fill exactly these, and nothing else. A name that already has a
#          value is refused, never replaced.
# --fresh  fill every secret below, for a new .env (cp .env.example .env).
#          Refused if any of them already has a value: on an existing
#          deployment, name what to fill.
# --dry-run  print what it would fill, and write nothing.
#
# The names are required (2026-11-25). Until then, with no arguments, it
# filled every empty secret it knew. In live's move off MinIO's root it was
# run to fill the two storage keys and also filled WINGRC_APP_DB_PASSWORD,
# which live's .env did not have and does not read (it sets
# WINGRC_DATABASE_URL outright). Nobody asked for that, and only the
# procedure's expected output ("it prints only the two names") showed it
# (docs/roadmap.md, "The app reaches MinIO as root", the move).
#
# WINGRC_ENVIRONMENT is not a secret and is not generated: it is required,
# and its value is a decision (production or development).
#
# Rewrites the file in place (same inode), so its owner and mode stay as
# they were; a new file is created 0600.
set -euo pipefail

cd "$(dirname "$0")/.."

env_file=.env
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --env-file) env_file="${2:?--env-file needs a path}"; shift 2 ;;
    *) args+=("$1"); shift ;;
  esac
done

python3 - "$env_file" "${args[@]+"${args[@]}"}" <<'PY'
import base64
import os
import secrets
import sys

path, argv = sys.argv[1], sys.argv[2:]


def token(n=32):
    return secrets.token_urlsafe(n)


def fernet():
    # A Fernet key is 32 random bytes, URL-safe base64; no library needed.
    return "local:" + base64.urlsafe_b64encode(os.urandom(32)).decode()


# Name -> generator. MinIO's user is a name, not a secret, but it is
# required and has no default, so a fresh install gets one too. The storage
# keys are the app's own account, which `provision` makes, scoped to the
# bucket's objects; never MinIO's root (app/storage_access.py). Until
# 2026-11-23 they were copied from root, because the app made its bucket.
GENERATED = {
    "POSTGRES_PASSWORD": token,
    # wingrc_app's: the role requests are served as. The backend's start
    # sets the role's password to it (backend/start.sh). Unread on a
    # deployment that sets WINGRC_DATABASE_URL outright, as live does.
    "WINGRC_APP_DB_PASSWORD": token,
    "MINIO_ROOT_USER": lambda: "wingrc",
    "MINIO_ROOT_PASSWORD": token,
    "WINGRC_AUTH_FLOW_SECRET": lambda: token(48),
    "WINGRC_CREDENTIAL_ENCRYPTION_KEYS": fernet,
    "WINGRC_STORAGE_ACCESS_KEY": lambda: "wingrc-app",
    "WINGRC_STORAGE_SECRET_KEY": token,
}

USAGE = (
    "usage: deploy/generate-env.sh [--dry-run] [--env-file PATH] NAME...\n"
    "       deploy/generate-env.sh [--dry-run] [--env-file PATH] --fresh\n"
    "names: " + " ".join(GENERATED)
)


def refuse(msg):
    print(f"generate-env: {msg}", file=sys.stderr)
    sys.exit(2)


dry_run = "--dry-run" in argv
fresh = "--fresh" in argv
names = [a for a in argv if a not in ("--dry-run", "--fresh")]

for a in names:
    if a.startswith("-"):
        refuse(f"unknown option {a}\n{USAGE}")
if fresh and names:
    refuse(f"--fresh fills every secret; do not name any as well\n{USAGE}")
if not fresh and not names:
    refuse(f"name the secrets to fill, or --fresh for a new .env\n{USAGE}")
unknown = [n for n in names if n not in GENERATED]
if unknown:
    refuse(f"not a secret this script generates: {' '.join(unknown)}\n{USAGE}")

lines = []
if os.path.exists(path):
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

values = {}
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        values[k.strip()] = v

wanted = list(GENERATED) if fresh else list(dict.fromkeys(names))
already = [n for n in wanted if values.get(n)]
if already:
    if fresh:
        refuse(
            f"this .env is not new: {' '.join(already)} already set. "
            "Name only the secrets to fill."
        )
    refuse(f"already set, never replaced: {' '.join(already)}")

state = {n: ("empty" if n in values else "absent") for n in wanted}
print("would fill: " + ", ".join(f"{n} ({state[n]})" for n in wanted), flush=True)
if dry_run:
    print("dry run: nothing written")
    sys.exit(0)

if not os.path.exists(path):
    old = os.umask(0o077)
    try:
        open(path, "x", encoding="utf-8").close()
    finally:
        os.umask(old)

filled = {n: GENERATED[n]() for n in wanted}
out, seen = [], set()
for line in lines:
    k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
    if k in filled:
        out.append(f"{k}={filled[k]}")
        seen.add(k)
    else:
        out.append(line)
out += [f"{k}={v}" for k, v in filled.items() if k not in seen]

with open(path, "r+", encoding="utf-8") as f:
    f.seek(0)
    f.write("\n".join(out) + "\n")
    f.truncate()

print("filled:", " ".join(filled))
PY
