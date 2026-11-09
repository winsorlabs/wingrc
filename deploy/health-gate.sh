#!/usr/bin/env bash
# Whether a /health body on stdin is a finished deploy of commit $1: exit 0
# only when the body's top-level "status" is "ok" and every component
# reports $1. deploy.sh's wait loop asks this and nothing else. On a
# refusal it prints one line on stderr saying why, which the loop logs per
# try, so a deploy's record shows how the wait went.
#
# It parses the JSON and reads the top-level field. It must never match the
# body's text: until 2026-11-06 the loop grepped for '"status":"ok"', and
# the snapshots block (item S) added a nested "status":"ok" beside it, so a
# split or incomplete deploy passed (docs/roadmap.md, "The deploy gate
# passed what it existed to refuse"). tests/test_health_gate.py holds the
# body that did it.
set -euo pipefail

[ $# -eq 1 ] || { echo "usage: health-gate.sh <commit-sha> < health.json" >&2; exit 2; }

"${PYTHON:-python3}" -c '
import json, sys
sha = sys.argv[1]

def refuse(reason):
    print(reason, file=sys.stderr)
    sys.exit(1)

raw = sys.stdin.read()
try:
    body = json.loads(raw)
except ValueError:
    refuse("no JSON body (empty)" if not raw.strip() else "no JSON body (%d bytes)" % len(raw))
if not isinstance(body, dict):
    refuse("body is not a JSON object")
components = body.get("components")
if not isinstance(components, dict):
    components = {}
names = ("backend", "nginx", "worker")
seen = "; ".join("%s %s" % (n, str(components.get(n, "missing"))[:12]) for n in names)
if body.get("status") != "ok":
    refuse("status %s: %s" % (body.get("status", "missing"), seen))
if not all(components.get(n) == sha for n in names):
    refuse("status ok but not all on %s: %s" % (sha[:12], seen))
' "$1"
