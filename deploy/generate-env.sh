#!/usr/bin/env bash
# Fill every required secret that is missing or empty in a .env with a fresh
# random value, and print only the names it filled. Never prints a value.
#
#   deploy/generate-env.sh [path/to/.env]    # default: .env beside compose
#
# For a fresh install (cp .env.example .env, then this) and for a bench
# stack. It never changes a value already set, so on an existing deployment
# it fills only what a newer compose file requires and the .env lacks
# (WINGRC_APP_DB_PASSWORD, from 2026-11-23): docs/deployment.md, "Serving
# as the app role". An existing deployment moves off a default by
# "Moving off a default credential", not by this.
#
# WINGRC_ENVIRONMENT is not a secret and is not generated: it is required,
# and its value is a decision (production or development).
#
# Rewrites the file in place (same inode), so its owner and mode stay as
# they were; a new file is created 0600.
set -euo pipefail

cd "$(dirname "$0")/.."
env_file="${1:-.env}"
if [ ! -e "$env_file" ]; then
  (umask 077 && : > "$env_file")
fi

python3 - "$env_file" <<'PY'
import base64
import os
import secrets
import sys

path = sys.argv[1]


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
    # sets the role's password to it (backend/start.sh).
    "WINGRC_APP_DB_PASSWORD": token,
    "MINIO_ROOT_USER": lambda: "wingrc",
    "MINIO_ROOT_PASSWORD": token,
    "WINGRC_AUTH_FLOW_SECRET": lambda: token(48),
    "WINGRC_CREDENTIAL_ENCRYPTION_KEYS": fernet,
    "WINGRC_STORAGE_ACCESS_KEY": lambda: "wingrc-app",
    "WINGRC_STORAGE_SECRET_KEY": token,
}

with open(path, encoding="utf-8") as f:
    lines = f.read().splitlines()

values = {}
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        k, v = line.split("=", 1)
        values[k.strip()] = v

filled = {}
for name, make in GENERATED.items():
    if not values.get(name):
        filled[name] = make()

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

print("filled:", " ".join(filled) if filled else "nothing (every required secret was set)")
PY
