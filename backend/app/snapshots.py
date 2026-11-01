"""Local snapshots of a deployment, and the restore that proves one is usable.

Roadmap item S. **Not a backup**: a snapshot sits on the same host as the
data. It covers a bad migration, an application bug or CLI run that deletes
data, and operator error. It does not cover losing the host or its disk,
ransomware that reaches the host, or the snapshot directory being removed.
docs/deployment.md, "Local snapshots", says the same for operators.

**Both stores, as one set.** Evidence bytes live in the object store and the
database holds only their keys, so a database restored alone renders every
attachment as present while none of the bytes exist. Row counts and
`recompute_sprs` read only the database and pass on that restore. A snapshot
is a directory, `<root>/<id>/`:

- `db.dump`: `pg_dump --format=custom`, taken inside an exported snapshot;
- `objects/<key>`: every object in the bucket, not only the keys rows name;
- `manifest.json`, written last: the schema head, per-table row counts and
  each assessment's stored SPRS score, read in that same database snapshot,
  and the size and SHA-256 of the dump and of every object.

It is built as `<id>.partial` and renamed when complete, so a directory
named like an id is a finished set. A restore refuses a set whose files do
not match its manifest. That is what stops a dump from one snapshot being
restored against another's objects.

**The window.** The dump comes first and the object copy after it, seconds
to minutes apart. Every upload path stores the bytes before it commits the
row, so a row in the dump names an object that already existed, and the
later copy finds it. An object written during the window is copied with no
row naming it; verification counts it as unreferenced, not as a failure.
The exception is a delete during the window (a logo replaced, a product
document deleted, after the dump): the dump names an object the copy no
longer finds. `create` checks every key the dump's rows name against what
it copied and records any it did not find (`missing_at_snapshot`), so
verification reports those as missing when the snapshot was taken, not as
lost in the restore.

**The key is not in the set.** WINGRC_CREDENTIAL_ENCRYPTION_KEYS lives in
the environment. The manifest records key labels, never key material.
Verification decrypts each stored credential to prove the key and the data
came back together, and reports success or failure only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.pool import NullPool

FORMAT = 1
ID_FORMAT = "%Y%m%dT%H%M%SZ"
_ID_RE = re.compile(r"^\d{8}T\d{6}Z$")
PARTIAL_SUFFIX = ".partial"
MANIFEST = "manifest.json"
DUMP = "db.dump"
OBJECTS = "objects"
_CHUNK = 1024 * 1024

# Every column that holds an object-store key, with the column recording the
# object's size where there is one. test_snapshots.py fails when a model
# gains a column ending in `storage_key` that is not listed here.
OBJECT_KEY_COLUMNS: tuple[tuple[str, str, str | None], ...] = (
    ("evidence", "storage_key", "file_size_bytes"),
    ("product_document", "storage_key", "file_size_bytes"),
    ("document_version", "storage_key", None),
    ("organization", "logo_storage_key", None),
)

_USER_TABLES_SQL = (
    "SELECT format('%I.%I', schemaname, tablename) FROM pg_tables "
    "WHERE schemaname NOT IN ('pg_catalog', 'information_schema') ORDER BY 1"
)

Log = Callable[[str], None]


class SnapshotError(RuntimeError):
    """Refused or failed. The message says which, and why."""


def snapshot_id(at: datetime) -> str:
    return at.astimezone(UTC).strftime(ID_FORMAT)


def parse_id(name: str) -> datetime | None:
    if not _ID_RE.match(name):
        return None
    return datetime.strptime(name, ID_FORMAT).replace(tzinfo=UTC)


def complete_snapshots(root: Path) -> list[tuple[str, datetime, Path]]:
    """Finished sets under `root`, oldest first: a directory whose name is an
    id and which holds a manifest. `.partial` and anything else is ignored."""
    found = []
    for entry in os.scandir(root):
        at = parse_id(entry.name)
        if at is not None and entry.is_dir() and os.path.isfile(os.path.join(entry.path, MANIFEST)):
            found.append((entry.name, at, Path(entry.path)))
    return sorted(found, key=lambda t: t[1])


def status(root: Path, max_age_hours: int, now: datetime) -> dict:
    """What /health reports: the newest complete snapshot's age against the
    schedule. A scheduled job that quietly stops leaves nothing behind to
    say so; this reads the result instead of trusting the schedule.

    `ok` and `stale` compare the newest id's time with `now`. `none` means
    the directory is there and holds no finished set. `not configured`
    means the directory is not mounted at all, and `unreadable` that it is
    there but cannot be listed. An id dated more than an hour ahead of
    `now` reads `invalid`, because a clock that is wrong would otherwise
    hide staleness.
    """
    base: dict = {"max_age_hours": max_age_hours}
    if not root.is_dir():
        return {**base, "status": "not configured", "detail": f"{root} is not mounted"}
    try:
        found = complete_snapshots(root)
    except OSError as e:
        return {**base, "status": "unreadable", "detail": f"{root}: {type(e).__name__}"}
    if not found:
        return {**base, "status": "none", "detail": f"no complete snapshot in {root}"}
    sid, at, _ = found[-1]
    age = (now - at).total_seconds() / 3600
    result = {**base, "newest": sid, "age_hours": round(age, 1), "count": len(found)}
    if age < -1:
        return {
            **result,
            "status": "invalid",
            "detail": "the newest snapshot is dated in the future",
        }
    return {**result, "status": "stale" if age > max_age_hours else "ok"}


@dataclass(frozen=True)
class SpacePlan:
    ok: bool
    reason: str
    # Ids rotated out once the new snapshot is complete, never before.
    delete_after: tuple[str, ...] = ()


def plan_space(
    existing: list[tuple[str, int]],
    *,
    estimate: int,
    keep: int,
    max_total: int,
    free: int,
    fs_size: int,
    min_free_fraction: float,
) -> SpacePlan:
    """Whether a new snapshot of about `estimate` bytes may be taken.

    `existing` is (id, bytes) for the complete snapshots, oldest first.
    Retention keeps the newest `keep`, counting the new one. A cap or
    free-space shortfall refuses: nothing is deleted to make room, because
    overwriting the last good snapshot to take an untested one is the
    worse failure. The free-space check counts the old snapshots as still
    present, as they are while the new one is written.
    """
    if keep < 1:
        raise ValueError("keep must be at least 1")
    kept = existing[len(existing) - (keep - 1) :] if keep > 1 else []
    rotated = existing[: len(existing) - len(kept)]
    total = sum(b for _, b in kept) + estimate
    if total > max_total:
        return SpacePlan(
            False,
            f"refusing: the {len(kept)} kept snapshot(s) plus this one need about "
            f"{total} bytes, over the {max_total}-byte cap. Nothing was deleted.",
        )
    reserve = int(fs_size * min_free_fraction)
    if free - estimate < reserve:
        return SpacePlan(
            False,
            f"refusing: the filesystem has {free} bytes free, this snapshot needs about "
            f"{estimate}, and {reserve} must stay free. Nothing was deleted.",
        )
    return SpacePlan(True, "", tuple(i for i, _ in rotated))


def object_path(base: Path, key: str) -> Path:
    """Where object `key` lives under `base`. Keys are server-made
    (`{org}/evidence/{id}/{id}.pdf`); anything that could leave `base` is
    refused rather than normalised."""
    parts = key.split("/")
    if not key or key.startswith("/") or "\\" in key or any(p in ("", ".", "..") for p in parts):
        raise SnapshotError(f"refusing object key {key!r}: not a plain relative path")
    return base.joinpath(*parts)


def _file_digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def owner_url() -> str:
    """The owning role: pg_dump as `wingrc_app` would meet row-level
    security, which pg_dump refuses rather than dumping a filtered copy."""
    from .config import get_settings

    s = get_settings()
    return s.migration_database_url or s.database_url


def _engine(url: str):
    return create_engine(url, poolclass=NullPool)


def _libpq(url: str) -> tuple[str, dict[str, str]]:
    """A libpq connection string without the password, and an environment
    carrying it, so the password is not on the command line."""
    u = make_url(url)
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password
    dsn = u.set(drivername="postgresql", password=None).render_as_string(hide_password=False)
    return dsn, env


def _require_object_store(storage):
    from .storage import MinIOClient

    if not isinstance(storage, MinIOClient):
        raise SnapshotError(
            "refusing: no object store is configured. A database-only snapshot is "
            "hollow: evidence rows would name bytes the set does not hold."
        )
    return storage


def _referenced_keys(conn: Connection) -> dict[str, dict]:
    """Every object key a row names: {key: {"tables": [...], "sizes": [...]}}."""
    refs: dict[str, dict] = {}
    for table, column, size_column in OBJECT_KEY_COLUMNS:
        size_sql = size_column or "NULL"
        rows = conn.execute(
            text(f"SELECT {column}, {size_sql} FROM {table} WHERE {column} IS NOT NULL")
        )
        for key, size in rows:
            ref = refs.setdefault(key, {"tables": [], "sizes": []})
            ref["tables"].append(table)
            if size is not None:
                ref["sizes"].append(size)
    return refs


def _row_counts(conn: Connection) -> dict[str, int]:
    tables = conn.execute(text(_USER_TABLES_SQL)).scalars().all()
    return {t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in tables}


def _configured_labels() -> list[str] | None:
    from .crypto import CredentialCipherError, _parse_keys

    try:
        return [label for label, _ in _parse_keys()]
    except CredentialCipherError:
        return None


@contextmanager
def _lock(root: Path) -> Iterator[None]:
    import fcntl  # POSIX only; snapshots run in the Linux image

    fd = os.open(root / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SnapshotError("refusing: another snapshot-create holds the lock") from None
        yield
    finally:
        os.close(fd)


def _pg_dump(url: str, pg_snapshot: str, dest: Path) -> None:
    dsn, env = _libpq(url)
    result = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            f"--snapshot={pg_snapshot}",
            "--file",
            str(dest),
            "--dbname",
            dsn,
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0 or not dest.is_file():
        raise SnapshotError(f"pg_dump failed (exit {result.returncode}): {result.stderr.strip()}")


def _copy_objects(storage, base: Path) -> list[dict]:
    items = []
    for key, _ in storage.list_objects():
        path = object_path(base, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        chunks, content_type = storage.open_object(key)
        h = hashlib.sha256()
        size = 0
        with open(path, "wb") as f:
            for chunk in chunks:
                f.write(chunk)
                h.update(chunk)
                size += len(chunk)
            f.flush()
            os.fsync(f.fileno())
        items.append(
            {"key": key, "bytes": size, "sha256": h.hexdigest(), "content_type": content_type}
        )
    return sorted(items, key=lambda i: i["key"])


def create(
    root: Path,
    *,
    storage,
    keep: int,
    max_total_bytes: int,
    min_free_fraction: float,
    log: Log = print,
) -> dict:
    """Take one snapshot into `root` and rotate old ones out. Reads only:
    nothing is written to the database or the object store."""
    storage = _require_object_store(storage)
    os.umask(0o077)
    if not root.is_dir():
        raise SnapshotError(f"refusing: {root} does not exist")
    os.chmod(root, 0o700)
    with _lock(root):
        for entry in sorted(root.iterdir()):
            if entry.name.endswith(PARTIAL_SUFFIX):
                log(f"removing {entry.name}: an unfinished snapshot, never a restore point")
                shutil.rmtree(entry)
        sid = snapshot_id(datetime.now(UTC))
        if (root / sid).exists():
            raise SnapshotError(f"refusing: {sid} already exists")
        partial = root / (sid + PARTIAL_SUFFIX)
        url = owner_url()
        engine = _engine(url)
        try:
            with engine.connect() as conn:
                conn.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
                with conn.begin():
                    conn.execute(text("SET LOCAL row_security = off"))
                    pg_snapshot = conn.execute(text("SELECT pg_export_snapshot()")).scalar_one()
                    database_at = conn.execute(text("SELECT now()")).scalar_one()
                    counts = _row_counts(conn)
                    head = conn.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one()
                    sprs = {
                        str(i): s
                        for i, s in conn.execute(
                            text("SELECT id, sprs_score FROM assessment ORDER BY id")
                        )
                    }
                    labels_in_use = sorted(
                        label
                        for label in conn.execute(
                            text(
                                "SELECT DISTINCT credential_key_version "
                                "FROM integration_connection "
                                "WHERE encrypted_credential IS NOT NULL"
                            )
                        ).scalars()
                        if label is not None
                    )
                    refs = _referenced_keys(conn)
                    db_bytes = conn.execute(
                        text("SELECT pg_database_size(current_database())")
                    ).scalar_one()

                    usage = shutil.disk_usage(root)
                    existing = [(i, _tree_bytes(p)) for i, _, p in complete_snapshots(root)]
                    plan = plan_space(
                        existing,
                        estimate=db_bytes + sum(size for _, size in storage.list_objects()),
                        keep=keep,
                        max_total=max_total_bytes,
                        free=usage.free,
                        fs_size=usage.total,
                        min_free_fraction=min_free_fraction,
                    )
                    if not plan.ok:
                        raise SnapshotError(plan.reason)
                    partial.mkdir()
                    _pg_dump(url, pg_snapshot, partial / DUMP)
        finally:
            engine.dispose()

        try:
            items = _copy_objects(storage, partial / OBJECTS)
            copied = {i["key"] for i in items}
            missing = sorted(k for k in refs if k not in copied)
            dump_sha, dump_bytes = _file_digest(partial / DUMP)
            configured = _configured_labels()
            manifest = {
                "format": FORMAT,
                "id": sid,
                "build": os.environ.get("WINGRC_BUILD_SHA", "unknown"),
                "window": {
                    "database_at": database_at.isoformat(),
                    "objects_done_at": datetime.now(UTC).isoformat(),
                },
                "database": {
                    "file": DUMP,
                    "bytes": dump_bytes,
                    "sha256": dump_sha,
                    "schema_head": head,
                    "row_counts": counts,
                    "sprs_scores": sprs,
                },
                "objects": {
                    "bucket": storage.bucket,
                    "count": len(items),
                    "bytes": sum(i["bytes"] for i in items),
                    "items": items,
                },
                "references": {"count": len(refs), "missing_at_snapshot": missing},
                "credential_key_labels": {"configured": configured, "in_use": labels_in_use},
            }
            with open(partial / MANIFEST, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=1, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            _fsync_dir(partial)
            os.rename(partial, root / sid)
            _fsync_dir(root)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise

        for old in plan.delete_after:
            shutil.rmtree(root / old)
            log(f"rotated out {old} (retention keeps {keep})")

    unreadable = sorted(set(labels_in_use) - set(configured or []))
    return {
        "id": sid,
        "path": str(root / sid),
        "bytes": dump_bytes + manifest["objects"]["bytes"],
        "tables": len(counts),
        "rows": sum(counts.values()),
        "objects": len(items),
        "references": len(refs),
        "missing_at_snapshot": missing,
        "labels_in_use_not_configured": unreadable,
        "rotated": list(plan.delete_after),
    }


def load_manifest(path: Path) -> dict:
    try:
        with open(path / MANIFEST, encoding="utf-8") as f:
            manifest = json.load(f)
    except FileNotFoundError:
        raise SnapshotError(
            f"refusing: {path} has no {MANIFEST}; it is not a finished snapshot"
        ) from None
    if manifest.get("format") != FORMAT:
        raise SnapshotError(
            f"refusing: manifest format {manifest.get('format')!r}, expected {FORMAT}"
        )
    return manifest


def _first(problems: list[str], n: int = 20) -> str:
    shown = "\n  ".join(problems[:n])
    more = f"\n  ... and {len(problems) - n} more" if len(problems) > n else ""
    return f"  {shown}{more}"


def validate_set(path: Path) -> dict:
    """The manifest, if every file in the set matches it; otherwise refuse.
    A dump or an object from another snapshot fails here, by content."""
    manifest = load_manifest(path)
    problems = []
    db = manifest["database"]
    dump = path / db["file"]
    if not dump.is_file():
        problems.append(f"{db['file']} is missing")
    elif _file_digest(dump) != (db["sha256"], db["bytes"]):
        problems.append(
            f"{db['file']} does not match the manifest's SHA-256 and size: "
            "it is not this snapshot's dump"
        )
    items = {i["key"]: i for i in manifest["objects"]["items"]}
    base = path / OBJECTS
    on_disk = (
        {p.relative_to(base).as_posix() for p in base.rglob("*") if p.is_file()}
        if base.is_dir()
        else set()
    )
    for key in sorted(on_disk - items.keys()):
        problems.append(f"{OBJECTS}/{key} is not in the manifest")
    for key in sorted(items):
        if key not in on_disk:
            problems.append(f"{OBJECTS}/{key} is in the manifest and missing from the set")
        elif _file_digest(object_path(base, key)) != (items[key]["sha256"], items[key]["bytes"]):
            problems.append(f"{OBJECTS}/{key} does not match the manifest's SHA-256 and size")
    if problems:
        raise SnapshotError(
            "refusing: the set does not match its manifest, so its parts may come from "
            "different snapshots:\n" + _first(problems)
        )
    return manifest


_APP_ROLE_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'wingrc_app') THEN
        CREATE ROLE wingrc_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS INHERIT;
    END IF;
END
$$;
"""


def restore(path: Path, *, storage, restore_objects: bool = True, log: Log = print) -> dict:
    """Restore one set into an empty database and an empty bucket.

    Refuses any target holding a relation, a schema other than `public`, or
    an object, so it can never overwrite data. Recovering a deployment is
    therefore: bring up fresh volumes, restore, then point the stack at
    them. `wingrc_app` is created first if absent (as migration 0016 makes
    it, with no password), because the dump grants to it; its password is
    cluster-level, not in the dump, and is set again from the environment.

    `restore_objects=False` exists for the mutation check only: it leaves
    the bucket empty so that verification can be seen to fail on it.
    """
    manifest = validate_set(path)
    storage = _require_object_store(storage)
    url = owner_url()
    engine = _engine(url)
    try:
        with engine.begin() as conn:
            relations = conn.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')"
                )
            ).scalar_one()
            schemas = (
                conn.execute(
                    text(
                        "SELECT nspname FROM pg_namespace WHERE nspname NOT IN "
                        "('public', 'pg_catalog', 'information_schema', 'pg_toast') "
                        "AND nspname NOT LIKE 'pg_temp_%' AND nspname NOT LIKE 'pg_toast_temp_%' "
                        "ORDER BY 1"
                    )
                )
                .scalars()
                .all()
            )
            if relations or schemas:
                raise SnapshotError(
                    f"refusing: the target database is not empty ({relations} relation(s); "
                    f"schemas {', '.join(schemas) or 'none besides public'}). A restore only "
                    "goes into an empty database, so it can never overwrite one."
                )
            if next(iter(storage.list_objects()), None) is not None:
                raise SnapshotError(
                    f"refusing: bucket {storage.bucket!r} is not empty. A restore only goes "
                    "into an empty bucket."
                )
            conn.execute(text(_APP_ROLE_SQL))
    finally:
        engine.dispose()

    dsn, env = _libpq(url)
    result = subprocess.run(
        [
            "pg_restore",
            "--exit-on-error",
            "--single-transaction",
            "--dbname",
            dsn,
            str(path / DUMP),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise SnapshotError(
            f"pg_restore failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    log(f"database restored from {manifest['id']} (schema {manifest['database']['schema_head']})")

    uploaded = 0
    if restore_objects:
        base = path / OBJECTS
        for item in manifest["objects"]["items"]:
            data = object_path(base, item["key"]).read_bytes()
            storage.upload_file(
                item["key"], data, item["content_type"] or "application/octet-stream"
            )
            uploaded += 1
        log(f"{uploaded} object(s) restored into bucket {storage.bucket!r}")
    else:
        log("objects NOT restored (--skip-objects): the bucket is left empty on purpose")
    return {"id": manifest["id"], "objects": uploaded}


@dataclass
class Check:
    name: str
    # None: the check had nothing to check, and says so rather than passing.
    ok: bool | None
    detail: str
    problems: list[str] = field(default_factory=list)


def _bucket_inventory(storage) -> dict[str, tuple[str, int]]:
    inventory = {}
    for key, _ in storage.list_objects():
        chunks, _ = storage.open_object(key)
        h = hashlib.sha256()
        size = 0
        for chunk in chunks:
            h.update(chunk)
            size += len(chunk)
        inventory[key] = (h.hexdigest(), size)
    return inventory


def _check_head(conn: Connection, manifest: dict) -> Check:
    expected = manifest["database"]["schema_head"]
    actual = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    if actual == expected:
        return Check("schema head", True, f"{actual}, as the manifest records")
    return Check("schema head", False, f"restored {actual!r}, manifest records {expected!r}")


def _check_counts(conn: Connection, manifest: dict) -> Check:
    expected = manifest["database"]["row_counts"]
    actual = _row_counts(conn)
    problems = [f"{t}: restored has no such table" for t in sorted(expected.keys() - actual.keys())]
    problems += [f"{t}: not in the manifest" for t in sorted(actual.keys() - expected.keys())]
    problems += [
        f"{t}: restored {actual[t]} rows, manifest records {expected[t]}"
        for t in sorted(expected.keys() & actual.keys())
        if actual[t] != expected[t]
    ]
    detail = f"{len(expected)} tables, {sum(expected.values())} rows in the manifest"
    return Check("row counts", not problems, detail, problems)


def _check_sprs(conn: Connection, manifest: dict) -> Check:
    from sqlalchemy.orm import Session

    from .engine import recompute_sprs

    expected = manifest["database"]["sprs_scores"]
    restored = {str(i) for i in conn.execute(text("SELECT id FROM assessment")).scalars()}
    problems = [f"assessment {a}: not in the manifest" for a in sorted(restored - expected.keys())]
    problems += [
        f"assessment {a}: restored has no such assessment"
        for a in sorted(expected.keys() - restored)
    ]
    session = Session(bind=conn, join_transaction_mode="create_savepoint")
    try:
        for a in sorted(expected.keys() & restored):
            score = recompute_sprs(session, uuid.UUID(a))
            if score != expected[a]:
                problems.append(
                    f"assessment {a}: recomputed {score}, the source stored {expected[a]}"
                )
    finally:
        session.close()
    if not expected and not restored:
        return Check("SPRS", None, "no assessment to recompute")
    return Check(
        "SPRS",
        not problems,
        f"{len(expected)} assessment(s) recomputed against the source's stored score",
        problems,
    )


def _check_references(refs: dict, inventory: dict, manifest: dict) -> Check:
    items = {i["key"]: i for i in manifest["objects"]["items"]}
    missing_at_snapshot = set(manifest["references"]["missing_at_snapshot"])
    problems = []
    for key in sorted(refs):
        where = ", ".join(sorted(set(refs[key]["tables"])))
        if key in missing_at_snapshot:
            problems.append(
                f"{key} ({where}): already missing when the snapshot was taken: deleted during "
                "the window, or lost on the source before it"
            )
        elif key not in inventory:
            problems.append(
                f"{key} ({where}): a row names it and the restored bucket has no such object"
            )
        else:
            sha, size = inventory[key]
            if any(s != size for s in refs[key]["sizes"]):
                problems.append(
                    f"{key} ({where}): {size} bytes in the bucket, "
                    f"the row records {refs[key]['sizes']}"
                )
            if key not in items:
                problems.append(
                    f"{key} ({where}): in the bucket but not in the snapshot's manifest"
                )
            elif sha != items[key]["sha256"]:
                problems.append(f"{key} ({where}): its bytes differ from the snapshot's (SHA-256)")
    unreferenced = len(inventory.keys() - refs.keys())
    detail = (
        f"{len(refs)} key(s) named by rows, each fetched from the bucket; "
        f"{unreferenced} object(s) no row names (written during the window, or orphaned)"
    )
    if not refs:
        return Check("object bytes", None, "no row names an object: " + detail)
    return Check("object bytes", not problems, detail, problems)


def _check_object_set(inventory: dict, manifest: dict) -> Check:
    items = {i["key"]: i for i in manifest["objects"]["items"]}
    problems = [
        f"{k}: in the manifest, not in the bucket" for k in sorted(items.keys() - inventory.keys())
    ]
    problems += [
        f"{k}: in the bucket, not in the manifest" for k in sorted(inventory.keys() - items.keys())
    ]
    problems += [
        f"{k}: differs from the manifest (SHA-256 or size)"
        for k in sorted(items.keys() & inventory.keys())
        if inventory[k] != (items[k]["sha256"], items[k]["bytes"])
    ]
    return Check("object set", not problems, f"{len(items)} object(s) in the manifest", problems)


def _check_credentials(conn: Connection) -> Check:
    from .crypto import CredentialCipherError, decrypt_credential

    rows = conn.execute(
        text(
            "SELECT connector_key, credential_key_version, encrypted_credential "
            "FROM integration_connection WHERE encrypted_credential IS NOT NULL "
            "ORDER BY connector_key"
        )
    ).all()
    if not rows:
        return Check("credentials", None, "no stored credential to decrypt")
    problems = []
    for connector, label, ciphertext in rows:
        try:
            decrypt_credential(ciphertext)
        except CredentialCipherError:
            problems.append(
                f"{connector} (key label {label!r}): "
                "could not be decrypted with the configured keys"
            )
    labels = ", ".join(sorted({str(label) for _, label, _ in rows}))
    return Check(
        "credentials",
        not problems,
        f"{len(rows)} stored credential(s), key label(s) {labels}",
        problems,
    )


def verify(path: Path, *, storage) -> list[Check]:
    """Compare the restored database and bucket with the set's manifest.
    Read-only: the database work runs in a transaction that is rolled back,
    so `recompute_sprs` writes nothing."""
    manifest = load_manifest(path)
    storage = _require_object_store(storage)
    engine = _engine(owner_url())
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                conn.execute(text("SET LOCAL row_security = off"))
                checks = [
                    _check_head(conn, manifest),
                    _check_counts(conn, manifest),
                    _check_sprs(conn, manifest),
                ]
                refs = _referenced_keys(conn)
                credentials = _check_credentials(conn)
            finally:
                tx.rollback()
    finally:
        engine.dispose()
    inventory = _bucket_inventory(storage)
    return [
        *checks,
        _check_references(refs, inventory, manifest),
        _check_object_set(inventory, manifest),
        credentials,
    ]
