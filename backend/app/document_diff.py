"""Readable diffs between two document versions (roadmap N.2).

Pure functions over strings -- no DB, no ORM, no request context -- so
this is unit-testable without a database, matching the split
`assessment.py` (pure domain) / `engine.py` (DB adapter) already
establishes.

The audience is the reason this module exists in the shape it does. "What
changed in this policy between March and September" is a question a
C3PAO assessor asks, and the answer has to be readable by someone who has
never seen a unified diff. So the output is structured rows the frontend
renders as side-by-side or inline -- not a patch string the client has to
parse, and not raw `difflib` opcodes.

Three things make it readable rather than merely correct:

  - **Word-level spans inside a changed line.** A one-word edit in a
    60-word paragraph highlights that word, not the whole paragraph.
  - **Collapsed unchanged runs.** A 400-line policy with one edited
    sentence shows that sentence with a few lines of context and a
    "N unchanged lines" marker, rather than 399 rows of noise.
  - **Paragraph-granular lines.** Markdown bodies wrap at paragraph
    boundaries, not at a column, so a "line" here is usually a whole
    paragraph or list item -- which is the unit a human actually reasons
    about when reviewing a policy change.

Version 1 (nothing to diff against) is not a special case the caller has
to handle: `diff_bodies(None, body)` returns every line as an insert,
with `is_initial` set so the view can label it "initial version" rather
than "everything was added".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

# Default unchanged-line context kept on either side of a change.
_CONTEXT_LINES = 3

# Split into words plus their trailing whitespace, so joining the tokens
# back together reproduces the line exactly and span offsets stay true to
# the original text.
_WORD_RE = re.compile(r"\S+\s*")


@dataclass(frozen=True)
class Span:
    """Half-open `[start, end)` character range within a single line."""

    start: int
    end: int


@dataclass(frozen=True)
class DiffRow:
    """One rendered row.

    `op` is one of:
      equal    -- unchanged; both texts present and identical
      insert   -- present only in the new version
      delete   -- present only in the old version
      replace  -- present in both, changed; `old_spans`/`new_spans` mark
                  the changed words within the line
      skip     -- a collapsed run of unchanged lines; `skipped` holds how
                  many. Carries no text.
    """

    op: str
    old_line_no: int | None = None
    new_line_no: int | None = None
    old_text: str | None = None
    new_text: str | None = None
    old_spans: tuple[Span, ...] = ()
    new_spans: tuple[Span, ...] = ()
    skipped: int = 0


@dataclass(frozen=True)
class BodyDiff:
    rows: tuple[DiffRow, ...] = ()
    added_lines: int = 0
    removed_lines: int = 0
    changed_lines: int = 0
    identical: bool = False
    is_initial: bool = False


@dataclass(frozen=True)
class SetDiff:
    """Added/removed membership between two sets of opaque ids."""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    unchanged: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


@dataclass(frozen=True)
class FieldChange:
    field_name: str
    old_value: str | None
    new_value: str | None


def _split_lines(body: str | None) -> list[str]:
    if body is None:
        return []
    # splitlines() rather than split("\n") so a trailing newline does not
    # produce a phantom empty final line that shows up as a spurious
    # insert/delete row on every diff.
    return body.replace("\r\n", "\n").replace("\r", "\n").splitlines()


def _token_offsets(tokens: list[str]) -> list[int]:
    offsets: list[int] = []
    pos = 0
    for tok in tokens:
        offsets.append(pos)
        pos += len(tok)
    return offsets


def _span_for(tokens: list[str], offsets: list[int], first: int, last: int) -> Span:
    """Span covering tokens[first:last], excluding the final token's own
    trailing whitespace so a highlight stops at the last word rather than
    running to the start of the next one.
    """
    start = offsets[first]
    end = offsets[last - 1] + len(tokens[last - 1].rstrip())
    return Span(start, end)


def _word_spans(old: str, new: str) -> tuple[tuple[Span, ...], tuple[Span, ...]]:
    """Word-level change spans between two versions of one line."""
    old_tokens = _WORD_RE.findall(old)
    new_tokens = _WORD_RE.findall(new)
    old_offsets = _token_offsets(old_tokens)
    new_offsets = _token_offsets(new_tokens)

    old_spans: list[Span] = []
    new_spans: list[Span] = []
    matcher = SequenceMatcher(None, old_tokens, new_tokens, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            continue
        if i2 > i1:
            old_spans.append(_span_for(old_tokens, old_offsets, i1, i2))
        if j2 > j1:
            new_spans.append(_span_for(new_tokens, new_offsets, j1, j2))
    return tuple(old_spans), tuple(new_spans)


def _collapse(rows: list[DiffRow], context: int) -> list[DiffRow]:
    """Replace long runs of `equal` rows with a single `skip` marker.

    Runs short enough to be worth showing in full (<= 2*context + 1) are
    left alone -- collapsing a 5-line run into "3 unchanged lines" plus
    two context rows saves nothing and costs the reader a click.
    """
    if context < 0:
        return rows
    out: list[DiffRow] = []
    i = 0
    n = len(rows)
    while i < n:
        if rows[i].op != "equal":
            out.append(rows[i])
            i += 1
            continue
        j = i
        while j < n and rows[j].op == "equal":
            j += 1
        run = rows[i:j]
        lead = context if i > 0 else 0
        trail = context if j < n else 0
        if len(run) <= lead + trail + 1:
            out.extend(run)
        else:
            out.extend(run[:lead])
            out.append(DiffRow(op="skip", skipped=len(run) - lead - trail))
            if trail:
                out.extend(run[len(run) - trail :])
        i = j
    return out


def diff_bodies(
    old_body: str | None, new_body: str | None, *, context_lines: int = _CONTEXT_LINES
) -> BodyDiff:
    """Structured, render-ready diff between two document bodies.

    `old_body=None` means there is no earlier version -- the caller does
    not special-case version 1; the result carries `is_initial=True` and
    every line as an insert.
    """
    is_initial = old_body is None
    old_lines = _split_lines(old_body)
    new_lines = _split_lines(new_body)

    if old_lines == new_lines:
        rows = tuple(
            DiffRow(
                op="equal", old_line_no=n + 1, new_line_no=n + 1, old_text=line, new_text=line
            )
            for n, line in enumerate(new_lines)
        )
        return BodyDiff(
            rows=tuple(_collapse(list(rows), context_lines)),
            identical=True,
            is_initial=is_initial,
        )

    rows: list[DiffRow] = []
    added = removed = changed = 0
    matcher = SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                rows.append(
                    DiffRow(
                        op="equal",
                        old_line_no=i1 + k + 1,
                        new_line_no=j1 + k + 1,
                        old_text=old_lines[i1 + k],
                        new_text=new_lines[j1 + k],
                    )
                )
        elif op == "replace":
            # Pair old/new lines positionally within the replaced run so
            # an edited paragraph shows as one changed row rather than a
            # delete row followed by an unrelated-looking insert row.
            for k in range(max(i2 - i1, j2 - j1)):
                has_old = i1 + k < i2
                has_new = j1 + k < j2
                if has_old and has_new:
                    o, nw = old_lines[i1 + k], new_lines[j1 + k]
                    o_spans, n_spans = _word_spans(o, nw)
                    rows.append(
                        DiffRow(
                            op="replace",
                            old_line_no=i1 + k + 1,
                            new_line_no=j1 + k + 1,
                            old_text=o,
                            new_text=nw,
                            old_spans=o_spans,
                            new_spans=n_spans,
                        )
                    )
                    changed += 1
                elif has_old:
                    rows.append(
                        DiffRow(
                            op="delete", old_line_no=i1 + k + 1, old_text=old_lines[i1 + k]
                        )
                    )
                    removed += 1
                else:
                    rows.append(
                        DiffRow(
                            op="insert", new_line_no=j1 + k + 1, new_text=new_lines[j1 + k]
                        )
                    )
                    added += 1
        elif op == "delete":
            for k in range(i2 - i1):
                rows.append(
                    DiffRow(op="delete", old_line_no=i1 + k + 1, old_text=old_lines[i1 + k])
                )
                removed += 1
        elif op == "insert":
            for k in range(j2 - j1):
                rows.append(
                    DiffRow(op="insert", new_line_no=j1 + k + 1, new_text=new_lines[j1 + k])
                )
                added += 1

    return BodyDiff(
        rows=tuple(_collapse(rows, context_lines)),
        added_lines=added,
        removed_lines=removed,
        changed_lines=changed,
        identical=False,
        is_initial=is_initial,
    )


def diff_sets(old_ids: list[str], new_ids: list[str]) -> SetDiff:
    old_set, new_set = set(old_ids), set(new_ids)
    return SetDiff(
        added=tuple(sorted(new_set - old_set)),
        removed=tuple(sorted(old_set - new_set)),
        unchanged=tuple(sorted(old_set & new_set)),
    )
