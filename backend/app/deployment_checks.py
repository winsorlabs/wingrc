"""The deployment's standing conditions, and the one place that decides
which of them are wrong.

`/health` reports four facts about the deployment beyond whether this
process can serve a request:
- whether storage works for the app's own account (`storage`, the probe);
- what that account may do (`storage_account`);
- whether the database keeps tenants apart (`database_role`);
- whether the breached-password list shipped (`breached_password_list`).

`warnings()` turns those readings into the list that `/health`'s
`warnings` field and `deploy.sh`'s `WARNING:` lines show. msp_admin's
banner shows `banner_warnings()`, the same list less `NOT_FOR_BANNER`.
They read this one function, so they cannot disagree about a condition,
only about who is told.

**What reaches the banner: what the operator can fix** (Jarrod,
2026-11-27). `breached_password_list` does not: a missing bundled list is
fixed by a WinGRC release, and the only thing an operator can do about it
is turn `pwned_passwords_check` off, which disables a security control to
clear a warning. It stays in `/health` and `deploy.sh`, where the reader
is the operator at the moment they could act on it.
`tests/test_deployment_checks.py` writes out the banner's keys.

**The banner's rule** (Jarrod, 2026-11-25; CLAUDE.md, "Banners"): a line
shows only when something is wrong and someone can fix it. A reading that
cannot be answered on a correct configuration is a field in `/health` and
never a warning. A line that can never clear is a permanent banner, and one
permanent line teaches people to ignore all of them. So `storage_account`'s
`unknown: ...` (a store that is not MinIO cannot be asked; MinIO still
starting) and `not configured: ...` are not warnings, and neither is a
storage endpoint left unset. `tests/test_deployment_checks.py` writes the
cases out.

**The probe** is the start's check (`MinIOClient.check_bucket`), made as
the app's account: a GET of a key that is never written. `NoSuchKey`
means the keys sign, the bucket exists and GetObject is granted; measured
on MinIO 2025-09-07, it needs no ListBucket. Bounded to `PROBE_TIMEOUT`
seconds, one attempt, and cached `PROBE_TTL` seconds per process, because
the container's healthcheck calls `/health` every few seconds and `/health`
is unauthenticated.
"""

from __future__ import annotations

import threading
import time

# Under deploy.sh's `curl --max-time 3` and the image's 3 s HEALTHCHECK, so a
# failed probe still comes back as a body that names it, not as no body.
PROBE_TIMEOUT = 2.0
PROBE_TTL = 15.0

_cache: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()


def storage_probe() -> str:
    """`ok`, `not configured: ...`, or `failed: <why>`; never raises."""
    from .config import get_settings
    from .storage import MinIOClient, StorageNotReady

    s = get_settings()
    if not s.storage_endpoint:
        return "not configured: WINGRC_STORAGE_ENDPOINT is unset, so uploads are discarded"
    now = time.monotonic()
    with _lock:
        hit = _cache.get("probe")
        if hit and hit[0] > now:
            return hit[1]
    try:
        MinIOClient(
            endpoint=s.storage_endpoint,
            access_key=s.storage_access_key or "",
            secret_key=s.storage_secret_key or "",
            bucket=s.storage_bucket,
            region=s.storage_region,
            timeout=PROBE_TIMEOUT,
        ).check_bucket()
        reading = "ok"
    except StorageNotReady as e:
        reading = f"failed: {e}"
    except Exception as e:  # noqa: BLE001 -- reported, not raised
        reading = f"failed: the storage check raised {type(e).__name__}."
    with _lock:
        _cache["probe"] = (time.monotonic() + PROBE_TTL, reading)
    return reading


def storage_failed(reading: str) -> bool:
    return reading.startswith("failed")


def warnings(*, storage: str, storage_account: dict, database_role: dict,
             breached_password_list: str) -> list[dict]:
    """What is wrong and fixable, in a fixed order. Each item has a `key`,
    the `/health` sentence as `detail` (deploy.sh prints it), and the
    fields the banner writes its own sentence from."""
    out: list[dict] = []
    if storage_failed(storage):
        out.append({"key": "storage_unreachable", "detail": storage})
    access = str(storage_account.get("access", ""))
    if access.startswith(("unscoped", "incomplete")):
        out.append({
            "key": "storage_account",
            "detail": access,
            "administers": bool(storage_account.get("administers")),
            "beyond_the_app": list(storage_account.get("beyond_the_app") or []),
            "missing": list(storage_account.get("missing") or []),
        })
    rls = str(database_role.get("row_level_security", ""))
    if rls and rls != "enforced":
        out.append({
            "key": "database_role",
            "detail": rls,
            "role": database_role.get("role"),
            "superuser": bool(database_role.get("superuser")),
            "bypassrls": bool(database_role.get("bypassrls")),
            "owner_of_rls_tables": int(database_role.get("owner_of_rls_tables") or 0),
        })
    if breached_password_list != "ok":
        out.append({"key": "breached_password_list", "detail": breached_password_list})
    return out


# Warnings the operator cannot fix (the module docstring says why).
NOT_FOR_BANNER = frozenset({"breached_password_list"})


def banner_warnings(**found) -> list[dict]:
    """msp_admin's banner: `warnings()` less what the operator cannot fix."""
    return [w for w in warnings(**found) if w["key"] not in NOT_FOR_BANNER]


def readings(db) -> dict:
    """The four readings, as `/health` reports them. `db` is a session the
    caller has already shown can answer (`/health` runs `SELECT 1` first)."""
    from . import app_role, storage_access
    from .auth import common_password_list_problem

    role = db.execute(app_role.ROLE_SQL).one()
    storage = storage_probe()
    # The account reading has its own 5 s timeout. After a failed probe it
    # would only add that wait, on a store already known not to answer.
    account = (
        {"access": "unknown: not read, because the storage check failed"}
        if storage_failed(storage)
        else storage_access.health_reading()
    )
    return {
        "breached_password_list": common_password_list_problem() or "ok",
        "database_role": app_role.row_level_security(
            role.role, role.superuser, role.bypassrls, role.owner_skips
        ),
        "storage_account": account,
        "storage": storage,
    }
