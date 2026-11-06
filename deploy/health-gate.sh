#!/usr/bin/env bash
# Whether a /health body on stdin is a finished deploy of commit $1: exit 0
# only when the body's top-level "status" is "ok" and every component
# reports $1. deploy.sh's wait loop asks this and nothing else.
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
try:
    body = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
if not isinstance(body, dict) or body.get("status") != "ok":
    sys.exit(1)
components = body.get("components")
if not isinstance(components, dict):
    sys.exit(1)
names = ("backend", "nginx", "worker")
sys.exit(0 if all(components.get(n) == sha for n in names) else 1)
' "$1"
