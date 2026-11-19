"""Every baseline validation problem names its kind from PROBLEM_KINDS.

The wizard writes its own sentence per kind and tells "to decide" from
"wrong" by it (docs/PLAN-tool-import.md, section 5.8). A problem with no
kind, or one the screen does not know, would show as nothing, so this
parses the module rather than trusting a reader to add both.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app import baseline_import

SOURCE = Path(baseline_import.__file__)


def _calls() -> list[ast.Call]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "ValidationProblem"
    ]


def test_every_problem_has_a_listed_kind():
    calls = _calls()
    assert len(calls) >= 15, "fixture must find the module's ValidationProblem calls"
    found: set[str] = set()
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "kind" in kw, f"line {call.lineno}: a ValidationProblem with no kind"
        node = kw["kind"]
        values = (
            [node.body, node.orelse] if isinstance(node, ast.IfExp) else [node]
        )
        for v in values:
            assert isinstance(v, ast.Constant), f"line {call.lineno}: kind must be a literal"
            assert v.value in baseline_import.PROBLEM_KINDS, (
                f"line {call.lineno}: kind {v.value!r} is not in PROBLEM_KINDS"
            )
            found.add(v.value)
    assert found == set(baseline_import.PROBLEM_KINDS), (
        f"listed but never raised: {sorted(set(baseline_import.PROBLEM_KINDS) - found)}"
    )
