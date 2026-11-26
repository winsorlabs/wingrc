#!/usr/bin/env bash
# The deployment's warnings from a /health body on stdin, one line each:
#   WARNING: <key>: <detail>
# deploy.sh prints them after the gate has accepted the deploy, and puts
# them in the deploy record. They never fail a deploy: the one condition
# that does (the storage probe) already set the top-level status, and the
# gate refused on that.
#
# The list is /health's `warnings` field, which backend/app/deployment_checks.py
# decides, so this, the msp_admin banner and /health cannot disagree. It
# parses the JSON and reads that field by name; it never matches the body's
# text (health-gate.sh says why). A body with no `warnings` field (a
# backend from before 2026-11-25) prints nothing. A body it cannot parse
# prints one WARNING saying so, rather than nothing.
set -euo pipefail

"${PYTHON:-python3}" -c '
import json, sys
try:
    body = json.loads(sys.stdin.read())
except ValueError:
    print("WARNING: health: the /health body could not be read, so its warnings are unknown")
    sys.exit(0)
items = body.get("warnings") if isinstance(body, dict) else None
for w in items if isinstance(items, list) else []:
    if isinstance(w, dict):
        print("WARNING: %s: %s" % (w.get("key", "unknown"), w.get("detail", "")))
'
