"""Unit tests for app/document_diff.py (roadmap N.2).

No DB -- these are pure functions, so they run in the local
`-m "not integration"` subset.
"""

from __future__ import annotations

from app.document_diff import diff_bodies, diff_sets

POLICY_V1 = (
    "# Access Control Policy\n"
    "\n"
    "All users must authenticate with MFA.\n"
    "\n"
    "Access is reviewed annually.\n"
)


def _ops(diff) -> list[str]:
    return [r.op for r in diff.rows]


def _row(diff, op: str):
    return next(r for r in diff.rows if r.op == op)


# ---------------------------------------------------------------------------
# The common case: what changed since we approved this
# ---------------------------------------------------------------------------


def test_single_word_edit_highlights_only_that_word() -> None:
    new = POLICY_V1.replace(
        "authenticate with MFA", "authenticate with phishing-resistant MFA"
    )
    diff = diff_bodies(POLICY_V1, new)

    assert diff.changed_lines == 1
    assert diff.added_lines == 0
    assert diff.removed_lines == 0
    row = _row(diff, "replace")
    highlighted = [row.new_text[s.start : s.end] for s in row.new_spans]
    assert highlighted == ["phishing-resistant"], (
        "a one-word edit must highlight that word, not the whole paragraph"
    )


def test_spans_never_include_trailing_whitespace() -> None:
    diff = diff_bodies("alpha beta gamma", "alpha DELTA gamma")
    row = _row(diff, "replace")
    for span in row.new_spans:
        assert row.new_text[span.start : span.end] == row.new_text[span.start : span.end].rstrip()


def test_added_paragraph_is_an_insert_not_a_rewrite() -> None:
    new = POLICY_V1 + "\nExceptions require ISSO approval.\n"
    diff = diff_bodies(POLICY_V1, new)
    assert diff.added_lines >= 1
    assert diff.changed_lines == 0
    assert "insert" in _ops(diff)


def test_removed_paragraph_is_a_delete() -> None:
    new = POLICY_V1.replace("Access is reviewed annually.\n", "")
    diff = diff_bodies(POLICY_V1, new)
    assert diff.removed_lines >= 1
    assert "delete" in _ops(diff)


def test_line_numbers_track_both_sides() -> None:
    diff = diff_bodies("a\nb\nc", "a\nB\nc", context_lines=10)
    row = _row(diff, "replace")
    assert row.old_line_no == 2
    assert row.new_line_no == 2


# ---------------------------------------------------------------------------
# The edges the view must not break on
# ---------------------------------------------------------------------------


def test_version_one_has_no_prior_and_does_not_break() -> None:
    """N.2 section 2's first requirement: handle version 1 without a
    broken view. The caller passes None and gets a usable diff back, not
    an exception and not an empty result.
    """
    diff = diff_bodies(None, POLICY_V1)
    assert diff.is_initial is True
    assert diff.identical is False
    assert set(_ops(diff)) == {"insert"}
    assert diff.added_lines == len(POLICY_V1.splitlines())


def test_identical_bodies_are_flagged_not_rendered_as_churn() -> None:
    diff = diff_bodies(POLICY_V1, POLICY_V1)
    assert diff.identical is True
    assert diff.added_lines == diff.removed_lines == diff.changed_lines == 0


def test_both_sides_empty() -> None:
    diff = diff_bodies(None, None)
    assert diff.identical is True
    assert diff.rows == ()


def test_body_emptied_entirely() -> None:
    diff = diff_bodies(POLICY_V1, "")
    assert diff.identical is False
    assert diff.removed_lines == len(POLICY_V1.splitlines())


def test_crlf_and_lf_are_not_a_difference() -> None:
    """A Windows editor and a browser must not make every line look
    changed."""
    diff = diff_bodies(POLICY_V1.replace("\n", "\r\n"), POLICY_V1)
    assert diff.identical is True


def test_trailing_newline_is_not_a_phantom_line() -> None:
    assert diff_bodies("a\nb", "a\nb\n").identical is True


# ---------------------------------------------------------------------------
# Readability of a long document
# ---------------------------------------------------------------------------


def test_long_unchanged_runs_collapse_with_context() -> None:
    old = "\n".join(f"Paragraph {i}." for i in range(60))
    new = old.replace("Paragraph 30.", "Paragraph thirty, revised.")
    diff = diff_bodies(old, new, context_lines=3)

    ops = _ops(diff)
    assert ops.count("skip") == 2, "runs before and after the change should collapse"
    assert ops.count("replace") == 1
    assert len(diff.rows) < 15, f"a 60-line doc with one edit rendered {len(diff.rows)} rows"
    # 59 lines are unchanged; 6 of them stay visible as context (3 either
    # side of the edit) and the remaining 53 collapse into the two markers.
    assert sum(r.skipped for r in diff.rows if r.op == "skip") == 53


def test_short_unchanged_runs_are_not_collapsed() -> None:
    """Collapsing a 3-line run into a marker plus context saves nothing
    and costs the reader a click."""
    old = "a\nb\nc\nd\ne"
    new = "A\nb\nc\nd\nE"
    diff = diff_bodies(old, new, context_lines=3)
    assert "skip" not in _ops(diff)


def test_context_lines_zero_drops_all_unchanged_rows() -> None:
    old = "\n".join(f"line {i}" for i in range(20))
    new = old.replace("line 10", "line ten")
    diff = diff_bodies(old, new, context_lines=0)
    assert _ops(diff) == ["skip", "replace", "skip"]


# ---------------------------------------------------------------------------
# Objective-set diff
# ---------------------------------------------------------------------------


def test_set_diff_reports_added_removed_unchanged() -> None:
    d = diff_sets(["a", "b", "c"], ["b", "c", "d"])
    assert d.added == ("d",)
    assert d.removed == ("a",)
    assert d.unchanged == ("b", "c")
    assert d.changed is True


def test_set_diff_unchanged_is_not_flagged_as_changed() -> None:
    d = diff_sets(["a", "b"], ["b", "a"])
    assert d.changed is False
    assert d.added == () and d.removed == ()


def test_set_diff_from_empty() -> None:
    d = diff_sets([], ["a"])
    assert d.added == ("a",) and d.removed == ()
    assert d.changed is True
