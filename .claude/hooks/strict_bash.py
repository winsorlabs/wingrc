"""PreToolUse hook for the Bash tool: run every command under `set -o pipefail`.

Why: a failure piped into `| tail` read as success, and a failed step in a
`;` chain let later steps push conflict markers (CLAUDE.md, pipefail
bullet, 2026-10-29).

Only pipefail. Measured 2026-10-30: the Bash tool runs each command in a
context where bash ignores `set -e` and the ERR trap (both were set and a
bare `false` did not stop the chain), so `-e` here would claim a stop that
never happens. pipefail does work: `false | cat` ends with exit 1. A `;`
chain is therefore not caught by this hook.

It only rewrites the command. It never returns a permissionDecision, so the
rewritten command goes through the normal permission flow; returning "allow"
here would skip every permission check.

Escape hatches:
- a pipe whose first stage may legitimately fail: `grep x f | head || true`;
- a whole command: start it with `#strict-off` (left untouched);
- a command that already starts with `set -` is left as written.

This file is tracked; what enables it is not. Registration goes in the
checkout's `.claude/settings.local.json`, which is machine-specific and
lost on a fresh clone, silently. On wl-dt26 (`python` there is the Store
alias, so `py -3`):

    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type":
      "command", "command": "py -3 C:/dev/wingrc/.claude/hooks/strict_bash.py",
      "timeout": 10}]}]}

Check it is on: `false | cat` in the Bash tool must exit 1.
"""
import json
import sys

payload = json.load(sys.stdin)
tool_input = payload.get("tool_input") or {}
command = tool_input.get("command")
if not isinstance(command, str):
    sys.exit(0)

head = command.lstrip()
if head.startswith("#strict-off") or head.startswith("set -"):
    sys.exit(0)

updated = dict(tool_input)
updated["command"] = "set -o pipefail\n" + command
json.dump({"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}, sys.stdout)
