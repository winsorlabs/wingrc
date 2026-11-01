"""Local snapshots (snapshots.py): the pure parts, without a database.

The end-to-end restore, and the mutation check that shows each verification
failing on a broken restore, run on the bench (deploy/restore-test.sh;
docs/roadmap.md's item S records the results).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import models, snapshots
from app.snapshots import SnapshotError

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


def _finished(root: Path, at: datetime) -> Path:
    d = root / snapshots.snapshot_id(at)
    d.mkdir()
    (d / snapshots.MANIFEST).write_text("{}")
    return d


# --- the age reader -------------------------------------------------------


def test_status_not_configured_when_the_directory_is_absent(tmp_path):
    s = snapshots.status(tmp_path / "missing", 26, NOW)
    assert s["status"] == "not configured"


def test_status_none_when_nothing_is_finished(tmp_path):
    (tmp_path / (snapshots.snapshot_id(NOW) + snapshots.PARTIAL_SUFFIX)).mkdir()
    (tmp_path / snapshots.snapshot_id(NOW - timedelta(hours=1))).mkdir()  # no manifest
    assert snapshots.status(tmp_path, 26, NOW)["status"] == "none"


def test_status_ok_reads_the_newest(tmp_path):
    _finished(tmp_path, NOW - timedelta(hours=50))
    _finished(tmp_path, NOW - timedelta(hours=2))
    s = snapshots.status(tmp_path, 26, NOW)
    assert s["status"] == "ok"
    assert s["newest"] == snapshots.snapshot_id(NOW - timedelta(hours=2))
    assert s["count"] == 2


def test_status_stale_when_the_newest_is_older_than_the_limit(tmp_path):
    """A backdated snapshot reads stale: the reader reports a schedule that
    stopped, which nothing else would."""
    _finished(tmp_path, NOW - timedelta(hours=27))
    s = snapshots.status(tmp_path, 26, NOW)
    assert s["status"] == "stale"
    assert s["age_hours"] == 27.0


def test_status_ignores_a_newer_partial(tmp_path):
    _finished(tmp_path, NOW - timedelta(hours=30))
    (tmp_path / (snapshots.snapshot_id(NOW) + snapshots.PARTIAL_SUFFIX)).mkdir()
    assert snapshots.status(tmp_path, 26, NOW)["status"] == "stale"


def test_status_invalid_when_dated_in_the_future(tmp_path):
    _finished(tmp_path, NOW + timedelta(hours=3))
    assert snapshots.status(tmp_path, 26, NOW)["status"] == "invalid"


# --- retention and the cap ------------------------------------------------

GIB = 1024**3


def _plan(existing, **kw):
    args = dict(
        estimate=1 * GIB,
        keep=3,
        max_total=10 * GIB,
        free=50 * GIB,
        fs_size=100 * GIB,
        min_free_fraction=0.1,
    )
    args.update(kw)
    return snapshots.plan_space(existing, **args)


def test_plan_keeps_the_newest_and_rotates_the_rest_afterwards():
    plan = _plan([("a", GIB), ("b", GIB), ("c", GIB), ("d", GIB)])
    assert plan.ok
    assert plan.delete_after == ("a", "b")


def test_plan_refuses_over_the_cap_and_deletes_nothing():
    plan = _plan([("a", 4 * GIB), ("b", 4 * GIB)], estimate=3 * GIB)
    assert not plan.ok
    assert plan.delete_after == ()
    assert "cap" in plan.reason


def test_plan_counts_only_what_retention_keeps_against_the_cap():
    # keep=3: "a" will be rotated out, so b + c + new = 9 GiB fits under 10.
    assert _plan(
        [("a", 4 * GIB), ("b", 4 * GIB), ("c", 4 * GIB)], estimate=GIB, max_total=9 * GIB
    ).ok


def test_plan_refuses_a_short_disk():
    plan = _plan([], free=10 * GIB + GIB // 2)
    assert not plan.ok
    assert "free" in plan.reason


def test_plan_keep_one():
    plan = _plan([("a", GIB), ("b", GIB)], keep=1)
    assert plan.ok and plan.delete_after == ("a", "b")


# --- object keys ----------------------------------------------------------


@pytest.mark.parametrize("key", ["", "/etc/passwd", "a/../b", "a//b", "./a", "a\\b", ".."])
def test_object_path_refuses_keys_that_could_leave_the_set(tmp_path, key):
    with pytest.raises(SnapshotError):
        snapshots.object_path(tmp_path, key)


def test_object_path_maps_a_server_key(tmp_path):
    assert (
        snapshots.object_path(tmp_path, "o/evidence/e/e.pdf")
        == tmp_path / "o" / "evidence" / "e" / "e.pdf"
    )


def test_every_storage_key_column_is_read_by_verification():
    """OBJECT_KEY_COLUMNS is a closed list: a new column holding an object
    key that is missing from it would restore with nobody checking its bytes."""
    found = {
        (table.name, col.name)
        for table in models.Base.metadata.tables.values()
        for col in table.columns
        if col.name.endswith("storage_key")
    }
    assert found == {(t, c) for t, c, _ in snapshots.OBJECT_KEY_COLUMNS}


# --- the set must match its manifest --------------------------------------


def _digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _make_set(root: Path, name: str, dump: bytes, objects: dict[str, bytes]) -> Path:
    d = root / name
    (d / snapshots.OBJECTS).mkdir(parents=True)
    (d / snapshots.DUMP).write_bytes(dump)
    items = []
    for key, data in objects.items():
        p = snapshots.object_path(d / snapshots.OBJECTS, key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        items.append(
            {"key": key, "bytes": len(data), "sha256": _digest(data), "content_type": None}
        )
    manifest = {
        "format": snapshots.FORMAT,
        "id": name,
        "database": {"file": snapshots.DUMP, "bytes": len(dump), "sha256": _digest(dump)},
        "objects": {"items": items},
    }
    (d / snapshots.MANIFEST).write_text(json.dumps(manifest))
    return d


def test_validate_accepts_a_consistent_set(tmp_path):
    d = _make_set(tmp_path, "s1", b"dump-1", {"o/evidence/1/1.pdf": b"one"})
    assert snapshots.validate_set(d)["id"] == "s1"


def test_validate_refuses_another_snapshots_dump(tmp_path):
    """The mismatched pair: Tuesday's dump against Monday's objects."""
    monday = _make_set(tmp_path, "monday", b"dump-monday", {"k/1": b"one"})
    tuesday = _make_set(tmp_path, "tuesday", b"dump-tuesday", {"k/1": b"one", "k/2": b"two"})
    (monday / snapshots.DUMP).write_bytes((tuesday / snapshots.DUMP).read_bytes())
    with pytest.raises(SnapshotError, match="not this snapshot's dump"):
        snapshots.validate_set(monday)


def test_validate_refuses_another_snapshots_objects(tmp_path):
    d = _make_set(tmp_path, "s", b"dump", {"k/1": b"one"})
    (d / snapshots.OBJECTS / "k" / "2").write_bytes(b"two")
    (d / snapshots.OBJECTS / "k" / "1").write_bytes(b"uno")
    with pytest.raises(SnapshotError) as e:
        snapshots.validate_set(d)
    assert "k/2 is not in the manifest" in str(e.value)
    assert "k/1 does not match" in str(e.value)


def test_validate_refuses_a_missing_object(tmp_path):
    d = _make_set(tmp_path, "s", b"dump", {"k/1": b"one"})
    (d / snapshots.OBJECTS / "k" / "1").unlink()
    with pytest.raises(SnapshotError, match="missing from the set"):
        snapshots.validate_set(d)


def test_validate_refuses_a_directory_without_a_manifest(tmp_path):
    (tmp_path / "x").mkdir()
    with pytest.raises(SnapshotError, match="not a finished snapshot"):
        snapshots.validate_set(tmp_path / "x")


# --- verification's object checks, given an inventory ---------------------


def _manifest(items: dict[str, bytes], missing=()):
    return {
        "objects": {
            "items": [{"key": k, "bytes": len(v), "sha256": _digest(v)} for k, v in items.items()]
        },
        "references": {"missing_at_snapshot": list(missing)},
    }


def _inv(items: dict[str, bytes]):
    return {k: (_digest(v), len(v)) for k, v in items.items()}


REFS = {"o/e/1": {"tables": ["evidence"], "sizes": [3]}}


def test_references_pass_when_every_named_object_is_back():
    c = snapshots._check_references(
        REFS, _inv({"o/e/1": b"one", "o/x": b"orphan"}), _manifest({"o/e/1": b"one"})
    )
    assert c.ok is True, c.problems
    assert "1 object(s) no row names" in c.detail


def test_references_fail_on_an_empty_bucket():
    """The hollow restore: every row present, no bytes."""
    c = snapshots._check_references(REFS, {}, _manifest({"o/e/1": b"one"}))
    assert c.ok is False
    assert "no such object" in c.problems[0]


def test_references_fail_on_changed_bytes_of_the_same_size():
    c = snapshots._check_references(REFS, _inv({"o/e/1": b"uno"}), _manifest({"o/e/1": b"one"}))
    assert c.ok is False
    assert "SHA-256" in c.problems[0]


def test_references_fail_on_a_size_the_row_does_not_record():
    c = snapshots._check_references(REFS, _inv({"o/e/1": b"on"}), _manifest({"o/e/1": b"on"}))
    assert c.ok is False
    assert "the row records [3]" in c.problems[0]


def test_references_name_an_object_missing_at_snapshot_as_such():
    c = snapshots._check_references(REFS, {}, _manifest({}, missing=["o/e/1"]))
    assert c.ok is False
    assert "already missing when the snapshot was taken" in c.problems[0]


def test_references_with_no_rows_is_not_a_pass():
    assert snapshots._check_references({}, {}, _manifest({})).ok is None


def test_object_set_fails_on_a_missing_or_extra_object():
    c = snapshots._check_object_set(_inv({"b": b"2"}), _manifest({"a": b"1"}))
    assert c.ok is False
    assert len(c.problems) == 2


def test_configured_labels_carry_no_key_material(monkeypatch):
    from app.config import get_settings

    key = "DFKuK2mEIJpixerxDMGU5xyQQwpWQBZxCFjOaZKjNSs="
    monkeypatch.setenv("WINGRC_CREDENTIAL_ENCRYPTION_KEYS", f"prod:{key}")
    get_settings.cache_clear()
    try:
        labels = snapshots._configured_labels()
    finally:
        get_settings.cache_clear()
    assert labels == ["prod"]
    assert key not in json.dumps(labels)
