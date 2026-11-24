#!/usr/bin/env bash
# Clear what a bench cycle on wl-util-1 leaves that `claude` cannot clear
# itself. Run by Jarrod after a bench cycle:
#
#   sudo scripts/bench-cleanup.sh             # lists each path and acts on it
#   scripts/bench-cleanup.sh --dry-run        # lists only; needs no sudo
#
# Two places, and nothing else (docs/bench-stack-verification.md, "After a
# bench cycle", says why each is not claude's):
#   1. /var/lib/wingrc-snapshots/wingrc_verify_*: a bench project's
#      snapshot directory, made there when the bench did not point
#      WINGRC_SNAPSHOT_DIR into its clone. REMOVED: it is bench data in a
#      directory claude cannot write. Never `wingrc` (live's) or any name
#      not starting wingrc_verify_.
#   2. Under /home/claude/bench, the topmost entries claude does not own: a
#      snapshot directory snapshot.sh handed to the image's user (uid
#      10001, 0700), or files a container wrote as root (node_modules,
#      logs, screenshots). HANDED BACK to claude (chown), not removed:
#      some are a bench's only copy of its output, and what to keep is
#      claude's to decide in its own directory, which it then can.
# It skips anything a running container mounts (a bench still running) and
# never follows a symlink. It is not a boundary against claude, whose
# docker group is root-equivalent on this host already; it is the
# permission, used by the person who holds it.
#
# BENCH_HOME, BENCH_OWNER, SNAP_ROOT, BENCH_CHOWN_TO/_GROUP and BENCH_CLEANUP_TEST
# (with BENCH_CLEANUP_MOUNTED standing in for docker's mounts) are for
# tests/test_bench_cleanup.py only.
set -euo pipefail

BENCH_HOME="${BENCH_HOME:-/home/claude/bench}"
BENCH_OWNER="${BENCH_OWNER:-claude}"
BENCH_CHOWN_TO="${BENCH_CHOWN_TO:-$BENCH_OWNER}"
BENCH_CHOWN_GROUP="${BENCH_CHOWN_GROUP:-$BENCH_CHOWN_TO}"
SNAP_ROOT="${SNAP_ROOT:-/var/lib/wingrc-snapshots}"

dry=0
case "${1:-}" in
  --dry-run) dry=1 ;;
  "") ;;
  *) echo "usage: bench-cleanup.sh [--dry-run]" >&2; exit 2 ;;
esac
if [ "$dry" -eq 0 ] && [ "$(id -u)" -ne 0 ] && [ -z "${BENCH_CLEANUP_TEST:-}" ]; then
  echo "bench-cleanup.sh: run with sudo, or --dry-run to list only" >&2
  exit 2
fi

# Sources of every running container's mounts. Without docker there is no
# way to tell a running bench from a finished one, so refuse.
if [ -n "${BENCH_CLEANUP_TEST:-}" ]; then
  mounted="${BENCH_CLEANUP_MOUNTED:-}"
elif command -v docker > /dev/null 2>&1; then
  mounted="$(docker ps -q | xargs -r docker inspect -f '{{range .Mounts}}{{.Source}}{{"\n"}}{{end}}' | sort -u)"
else
  echo "bench-cleanup.sh: docker not found; cannot tell which paths a running bench uses" >&2
  exit 2
fi

in_use() {  # $1 is, contains, or lies inside a mounted path
  local m
  while IFS= read -r m; do
    [ -n "$m" ] || continue
    case "$m/" in "$1/"*) return 0 ;; esac
    case "$1/" in "$m/"*) return 0 ;; esac
  done <<< "$mounted"
  return 1
}

unchanged() {  # $1 resolves to itself: no symlink anywhere in it
  local r
  r="$(realpath -e -- "$1" 2>/dev/null)" || return 1
  [ "$r" = "$1" ]
}

describe() {  # owner and mode; size when it can be read (not without sudo)
  echo "uid $(stat -c '%u (%U), mode %a' -- "$1"), $( (du -sh -- "$1" 2>/dev/null || true) | cut -f1)"
}

acted=0
skipped=0
act() {  # verb path
  local verb=$1 p=$2
  if ! unchanged "$p"; then
    echo "skip (a symlink in its path): $p"; skipped=$((skipped + 1)); return
  fi
  if in_use "$p"; then
    echo "skip (a running container mounts it): $p"; skipped=$((skipped + 1)); return
  fi
  local info; info="$(describe "$p")"
  if [ "$dry" -eq 1 ]; then
    echo "would $verb ($info): $p"
  elif [ "$verb" = remove ]; then
    rm -rf --one-file-system -- "$p"; echo "removed ($info): $p"
  else
    chown -R --no-dereference "$BENCH_CHOWN_TO:$BENCH_CHOWN_GROUP" -- "$p"; echo "handed back to $BENCH_CHOWN_TO ($info): $p"
  fi
  acted=$((acted + 1))
}

for p in "$SNAP_ROOT"/wingrc_verify_*; do
  if [ -e "$p" ] && [ ! -L "$p" ] && [ "$(dirname "$p")" = "$SNAP_ROOT" ]; then act remove "$p"; fi
done
if [ -d "$BENCH_HOME" ]; then
  while IFS= read -r -d '' p; do act "hand back" "$p"; done < <(
    find -P "$BENCH_HOME" -mindepth 1 -xdev ! -user "$BENCH_OWNER" ! -type l -prune -print0)
fi
echo "bench-cleanup: $acted $([ "$dry" -eq 1 ] && echo "to act on" || echo "done"), $skipped skipped"
