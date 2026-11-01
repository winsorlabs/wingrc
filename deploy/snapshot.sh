#!/usr/bin/env bash
# Take one local snapshot of this checkout's stack: the database and the
# object store as one set (backend/app/snapshots.py). NOT a backup: it sits
# on the same host as the data. See docs/deployment.md, "Local snapshots".
#
#   deploy/snapshot.sh
#
# Run from the checkout whose stack it snapshots; COMPOSE_PROJECT_NAME
# selects a stack started with -p. Reads only. Exit 1 on a refusal (the
# cap, a short disk, another run holding the lock) or a failure, leaving
# earlier snapshots untouched; exit 3 when rows already named objects the
# store did not hold. Nothing schedules this; enabling it is a deliberate
# step (docs/deployment.md).
set -euo pipefail

cd "$(dirname "$0")/.."

project="$(docker compose config --format json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
dir="${WINGRC_SNAPSHOT_DIR:-/var/lib/wingrc-snapshots/$project}"

# Docker creates the directory as root. Hand it to the image's user and
# close it: 0700, so only that uid and root read the dumps, which hold
# every tenant's records in plaintext. On this host the docker group is
# root-equivalent, so its members can read them too.
docker compose run --rm --no-deps -T --user root -v "$dir:/snapshots-rw" --entrypoint sh backend \
  -c 'chown wingrc:wingrc /snapshots-rw && chmod 0700 /snapshots-rw'
docker compose run --rm --no-deps -T -v "$dir:/snapshots-rw" backend \
  wingrc snapshot-create --dir /snapshots-rw
