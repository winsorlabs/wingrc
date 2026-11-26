#!/usr/bin/env bash
# Which environment variables a recreate would change: for each named
# service, the names whose value in compose's resolved configuration (this
# checkout's docker-compose.yml and .env, as `docker compose config` reads
# them now) differs from the running container's. Names only; no value is
# printed, compared outside this process, or written anywhere.
#
#   deploy/env-diff.sh SERVICE...
#
# Run it after editing .env and before recreating anything: the names it
# prints are exactly what the recreate changes, and a name you did not mean
# to change is a line to put back first. It answers from the running
# containers, so it needs no copy of .env to diff against; a copy holds
# every credential (docs/deployment.md, "MinIO's root password", where it
# replaced one).
#
# Prints one line per changed name, `SERVICE: NAME`, or `SERVICE: no
# change`. A name the running container lacks counts as changed. Exit 0
# when nothing changes, 1 when something would, 2 on a usage error or a
# service with no running container.
set -euo pipefail

cd "$(dirname "$0")/.."
[ $# -ge 1 ] || { echo "usage: deploy/env-diff.sh SERVICE..." >&2; exit 2; }

config="$(docker compose config --format json)"
status=0
for svc in "$@"; do
  ids="$(docker compose ps -q "$svc")"
  [ -n "$ids" ] || { echo "$svc: no running container" >&2; exit 2; }
  for id in $ids; do
    running="$(docker inspect --format '{{json .Config.Env}}' "$id")"
    rc=0
    CONFIG="$config" RUNNING="$running" "${PYTHON:-python3}" - "$svc" <<'PY' || rc=$?
import json, os, sys
svc = sys.argv[1]
services = json.loads(os.environ["CONFIG"])["services"]
if svc not in services:
    print(f"{svc}: not a service in this compose file", file=sys.stderr)
    sys.exit(2)
want = services[svc].get("environment") or {}
have = dict(e.split("=", 1) for e in json.loads(os.environ["RUNNING"]) or [])
changed = sorted(k for k, v in want.items() if v is not None and have.get(k) != v)
for k in changed:
    print(f"{svc}: {k}")
if not changed:
    print(f"{svc}: no change")
sys.exit(1 if changed else 0)
PY
    case "$rc" in
      0) ;;
      1) status=1 ;;
      *) exit "$rc" ;;
    esac
  done
done
exit "$status"
