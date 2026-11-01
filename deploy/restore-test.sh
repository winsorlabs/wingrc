#!/usr/bin/env bash
# The restore test: restore one local snapshot into a throwaway stack,
# verify it (backend/app/snapshots.py), remove the stack, and prove nothing
# was left behind. A snapshot never restored is a file, not a restore point.
#
#   deploy/restore-test.sh <snapshot-id>             # the test
#   deploy/restore-test.sh <snapshot-id> <mutation>  # a mutation check
#
# A mutation breaks the restore on purpose and passes only if the check it
# targets fails. Each check has to be seen failing at least once:
#
#   empty-objects       database only, bucket left empty  -> object bytes
#                       (row counts and SPRS still pass: the hollow restore)
#   missing-object      one evidence object deleted        -> object bytes
#   corrupt-object      one object's bytes changed         -> object bytes
#   wrong-key           verified with another Fernet key   -> credentials
#   row-deleted         one audit_log row deleted          -> row counts
#   score-changed       every control_state flipped        -> SPRS
#   schema-head         alembic_version changed            -> schema head
#   not-empty           restored twice into one database   -> restore refuses
#   mismatched-dump:<other-id>     this set, other's dump    -> restore refuses
#   mismatched-objects:<other-id>  this set, other's objects -> restore refuses
#
# Writes only to the throwaway stack, which gets its own compose project,
# volumes and network, and publishes no ports. It runs the source's backend
# image, never a rebuild. Run from the checkout whose stack took the
# snapshot (COMPOSE_PROJECT_NAME for a stack started with -p). The record,
# including the volume and container lists from before and after, lands in
# ~/wingrc-restore-tests/. No key value is printed or written; the record
# and the snapshot are grepped for each one before the verdict.
set -euo pipefail

cd "$(dirname "$0")/.."

id="${1:?usage: deploy/restore-test.sh <snapshot-id> [mutation]}"
mutation="${2:-}"
case "$mutation" in
  "" | empty-objects | missing-object | corrupt-object | wrong-key | row-deleted \
    | score-changed | schema-head | not-empty | mismatched-dump:?* | mismatched-objects:?*) ;;
  *) echo "unknown mutation: $mutation" >&2; exit 2 ;;
esac

project="$(docker compose config --format json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
dir="${WINGRC_SNAPSHOT_DIR:-/var/lib/wingrc-snapshots/$project}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
test_project="wingrc_restoretest_$(printf '%s' "$stamp" | tr 'A-Z' 'a-z')"
record="${WINGRC_RESTORE_RECORD_DIR:-$HOME/wingrc-restore-tests}/$stamp-$id${mutation:+-${mutation%%:*}}"
mkdir -p "$record"
chmod 0700 "$record"
echo "restore test of $id from $project${mutation:+, mutation $mutation}; record: $record"

state() {
  echo "# volumes"; docker volume ls -q | sort
  echo "# containers"; docker ps -a --format '{{.Names}}' | sort
  echo "# networks"; docker network ls --format '{{.Name}}' | sort
}
state > "$record/state-before.txt"

# The source's own image, by its compose tag; `!reset` drops the build so
# nothing is rebuilt.
cat > "$record/compose.restore.yml" <<EOF
services:
  backend:
    image: ${project}-backend:latest
    pull_policy: never
    build: !reset null
EOF
tc() {
  WINGRC_SNAPSHOT_DIR="$dir" docker compose -p "$test_project" \
    -f docker-compose.yml -f "$record/compose.restore.yml" "$@"
}
# The throwaway database is fresh, so its owner is the compose file's
# POSTGRES_USER, whatever the source's .env says.
cli() {
  tc run --rm --no-deps -T \
    -e WINGRC_DATABASE_URL=postgresql+psycopg://wingrc:wingrc@db:5432/wingrc \
    -e WINGRC_MIGRATION_DATABASE_URL= \
    "$@"
}
# The source project's backend, for the snapshot directory (owned by its uid).
src_rw() {
  docker compose run --rm --no-deps -T -v "$dir:/snapshots-rw" --entrypoint sh backend -c "$1"
}

hybrid=""
cleanup() {
  tc down -v --remove-orphans > "$record/down.log" 2>&1 || true
  if [ -n "$hybrid" ]; then
    src_rw "rm -rf '/snapshots-rw/$hybrid'" >> "$record/down.log" 2>&1 || true
  fi
}
trap cleanup EXIT

target="/snapshots/$id"
case "$mutation" in
  mismatched-dump:* | mismatched-objects:*)
    other="${mutation#*:}"
    # Not an id, so /health and rotation ignore it; removed at cleanup.
    hybrid=".mutation-$stamp"
    if [ "${mutation%%:*}" = mismatched-dump ]; then
      swap="cp /snapshots-rw/$other/db.dump /snapshots-rw/$hybrid/db.dump"
    else
      swap="rm -rf /snapshots-rw/$hybrid/objects && cp -a /snapshots-rw/$other/objects /snapshots-rw/$hybrid/objects"
    fi
    src_rw "test -d /snapshots-rw/$other && cp -a /snapshots-rw/$id /snapshots-rw/$hybrid && $swap"
    target="/snapshots/$hybrid"
    ;;
esac

tc up -d --wait db minio > "$record/up.log" 2>&1
# The key values this stack runs with, kept in this shell for the final grep
# and never printed.
keys="$(tc config --format json | python3 -c '
import json, sys
env = json.load(sys.stdin)["services"]["backend"]["environment"]
for entry in (env.get("WINGRC_CREDENTIAL_ENCRYPTION_KEYS") or "").split(","):
    if ":" in entry:
        print(entry.split(":", 1)[1].strip())
')"

restore_args=()
[ "$mutation" = empty-objects ] && restore_args=(--skip-objects)
if [ "$mutation" = not-empty ]; then
  cli backend wingrc snapshot-restore "$target" > "$record/restore-first.txt" 2>&1
fi
restore_rc=0
cli backend wingrc snapshot-restore "$target" "${restore_args[@]}" > "$record/restore.txt" 2>&1 \
  || restore_rc=$?
cat "$record/restore.txt"

verify_rc=skipped
if [ "$restore_rc" -eq 0 ]; then
  py() { cli backend python -c "$1" >> "$record/mutation.txt" 2>&1; }
  sql() { tc exec -T db psql -v ON_ERROR_STOP=1 -U wingrc -d wingrc -c "$1" >> "$record/mutation.txt" 2>&1; }
  pick='from app.storage import get_storage_client as g; s = g(); k = next(k for k, _ in s.list_objects() if "/evidence/" in k)'
  case "$mutation" in
    missing-object) py "$pick; s.delete_file(k); print('deleted', k)" ;;
    corrupt-object) py "$pick; d = s.get_bytes(k); s.upload_file(k, bytes(b ^ 0xFF for b in d), 'application/octet-stream'); print('flipped every byte of', k, len(d))" ;;
    row-deleted) sql "DELETE FROM audit_log WHERE id = (SELECT id FROM audit_log ORDER BY id LIMIT 1)" ;;
    score-changed) sql "UPDATE control_state SET status = CASE WHEN status = 'met' THEN 'not_met' ELSE 'met' END" ;;
    schema-head) sql "UPDATE alembic_version SET version_num = 'mutated'" ;;
  esac
  [ -f "$record/mutation.txt" ] && cat "$record/mutation.txt"
  verify_rc=0
  if [ "$mutation" = wrong-key ]; then
    # Generated here and never printed; it reaches the container through the
    # environment compose interpolates, not a command line.
    wrong="$(cli backend python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
    keys="$keys"$'\n'"$wrong"
    WINGRC_CREDENTIAL_ENCRYPTION_KEYS="wrong:$wrong" \
      cli backend wingrc snapshot-verify "$target" > "$record/verify.txt" 2>&1 || verify_rc=$?
  else
    cli backend wingrc snapshot-verify "$target" > "$record/verify.txt" 2>&1 || verify_rc=$?
  fi
  cat "$record/verify.txt"
fi

cleanup
trap - EXIT
state > "$record/state-after.txt"

verdict=pass
reasons=()
if ! diff -u "$record/state-before.txt" "$record/state-after.txt" > "$record/state.diff"; then
  verdict=fail; reasons+=("the test left volumes, containers or networks behind (state.diff)")
fi
left="$(src_rw "ls -A /snapshots-rw | grep '^\.mutation-' || true")"
if [ -n "$left" ]; then
  verdict=fail; reasons+=("a mutation copy was left in the snapshot directory: $left")
fi

# grep's exit 1 is "no match"; 0 (a key found) and 2 (an error) both fail.
rc=0
printf '%s\n' "$keys" | grep -v '^$' | grep -rqF -f - "$record" || rc=$?
[ "$rc" -eq 1 ] || { verdict=fail; reasons+=("key check on the record: grep exit $rc (0 = a key value is in it)"); }
rc=0
printf '%s\n' "$keys" | grep -v '^$' \
  | docker compose run --rm --no-deps -T --entrypoint sh backend -c "grep -rqF -f - /snapshots/$id" || rc=$?
[ "$rc" -eq 1 ] || { verdict=fail; reasons+=("key check on the snapshot: grep exit $rc (0 = a key value is in it)"); }

expect_fail() {  # a verify check that must have failed
  if [ "$verify_rc" = 0 ] || [ "$verify_rc" = skipped ] || ! grep -q "^FAIL     $1:" "$record/verify.txt"; then
    verdict=fail; reasons+=("mutation $mutation: expected '$1' to FAIL; verify exit $verify_rc")
  fi
}
expect_refused() {
  if [ "$restore_rc" -eq 0 ] || ! grep -q "^refusing: $1" "$record/restore.txt"; then
    verdict=fail; reasons+=("mutation $mutation: expected the restore to refuse ($1); exit $restore_rc")
  fi
}
case "$mutation" in
  "")
    [ "$restore_rc" -eq 0 ] && [ "$verify_rc" = 0 ] \
      || { verdict=fail; reasons+=("restore exit $restore_rc, verify exit $verify_rc"); } ;;
  empty-objects)
    expect_fail "object bytes"
    for c in "row counts" SPRS; do
      grep -q "^PASS     $c:" "$record/verify.txt" \
        || { verdict=fail; reasons+=("empty-objects: '$c' should still pass, showing a DB-only check is blind"); }
    done ;;
  missing-object | corrupt-object) expect_fail "object bytes" ;;
  wrong-key) expect_fail credentials ;;
  row-deleted) expect_fail "row counts" ;;
  score-changed) expect_fail SPRS ;;
  schema-head) expect_fail "schema head" ;;
  not-empty) expect_refused "the target database is not empty" ;;
  mismatched-*) expect_refused "the set does not match its manifest" ;;
esac

{
  echo "snapshot: $id ($project)"
  echo "mutation: ${mutation:-none}"
  echo "restore:  exit $restore_rc"
  echo "verify:   exit $verify_rc"
  echo "verdict:  $verdict"
  for r in "${reasons[@]}"; do echo "  $r"; done
} | tee "$record/verdict.txt"
[ "$verdict" = pass ]
