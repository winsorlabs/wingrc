"""deployment_checks.py: which readings are warnings, and the storage probe.

The banner's rule (CLAUDE.md, "Banners"): a line shows only when something
is wrong and someone can fix it. The cases are written out here, not taken
from the module, so removing one from the implementation turns a test red
rather than shrinking it.
"""

from __future__ import annotations

import json

import pytest

from app import deployment_checks
from app.deployment_checks import warnings

SCOPED = {"access": "scoped", "administers": False, "beyond_the_app": [], "missing": []}
ENFORCED = {"role": "wingrc_app", "superuser": False, "bypassrls": False,
            "owner_of_rls_tables": 0, "row_level_security": "enforced"}


def _keys(**over):
    args = dict(storage="ok", storage_account=SCOPED, database_role=ENFORCED,
                breached_password_list="ok")
    args.update(over)
    return [w["key"] for w in warnings(**args)]


def test_a_correct_deployment_has_no_warning():
    assert _keys() == []


@pytest.mark.parametrize(
    ("over", "key"),
    [
        ({"storage": "failed: The storage bucket 'evidence' does not exist."},
         "storage_unreachable"),
        ({"storage_account": {"access": "unscoped: the app's storage account administers the "
                              "object store", "administers": True}}, "storage_account"),
        ({"storage_account": {"access": "unscoped: granted more than it uses: s3:ListBucket on x",
                              "beyond_the_app": ["s3:ListBucket on x"]}}, "storage_account"),
        ({"storage_account": {"access": "incomplete: lacks s3:DeleteObject",
                              "missing": ["s3:DeleteObject"]}}, "storage_account"),
        ({"database_role": {**ENFORCED, "role": "wingrc", "superuser": True,
                            "row_level_security": "bypassed: wingrc is a superuser"}},
         "database_role"),
        ({"database_role": {**ENFORCED, "bypassrls": True,
                            "row_level_security": "bypassed: wingrc_app has BYPASSRLS"}},
         "database_role"),
        ({"database_role": {**ENFORCED, "owner_of_rls_tables": 3,
                            "row_level_security": "bypassed: owns 3 tables"}}, "database_role"),
        ({"breached_password_list": "missing: /app/app/data/x does not exist"},
         "breached_password_list"),
    ],
    ids=["storage-failed", "administers", "beyond", "incomplete", "superuser", "bypassrls",
         "owner", "breached-list"],
)
def test_each_wrong_and_fixable_reading_is_a_warning(over, key):
    assert _keys(**over) == [key]


@pytest.mark.parametrize(
    "over",
    [
        {"storage_account": {"access": "unknown: accountinfo answered HTTP 501. See ..."}},
        {"storage_account": {"access": "unknown: could not ask the store (URLError). See ..."}},
        {"storage_account": {"access": "not configured: WINGRC_STORAGE_ENDPOINT is unset"}},
        {"storage": "not configured: WINGRC_STORAGE_ENDPOINT is unset, so uploads are discarded"},
    ],
    ids=["unknown-http", "unknown-unreachable", "account-not-configured", "probe-not-configured"],
)
def test_a_reading_that_cannot_be_answered_is_never_a_warning(over):
    """Jarrod, 2026-11-25: a store that is not MinIO can never answer the
    account check, so its line would never clear on a correct
    configuration. It stays a field in /health."""
    assert _keys(**over) == []


def test_all_four_come_in_a_fixed_order_with_the_banners_fields():
    out = warnings(
        storage="failed: x",
        storage_account={"access": "unscoped: y", "administers": True},
        database_role={**ENFORCED, "role": "wingrc", "superuser": True,
                       "row_level_security": "bypassed: z"},
        breached_password_list="missing: w",
    )
    assert [w["key"] for w in out] == [
        "storage_unreachable", "storage_account", "database_role", "breached_password_list",
    ]
    assert [w["detail"] for w in out] == ["failed: x", "unscoped: y", "bypassed: z", "missing: w"]
    assert out[1]["administers"] is True
    assert (out[2]["role"], out[2]["superuser"]) == ("wingrc", True)
    json.dumps(out)


# --- the probe -----------------------------------------------------------


@pytest.fixture
def storage_settings(monkeypatch):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "storage_endpoint", "http://minio.invalid:9000")
    monkeypatch.setattr(s, "storage_access_key", "k")
    monkeypatch.setattr(s, "storage_secret_key", "s")
    monkeypatch.setattr(deployment_checks, "_cache", {})
    return s


def test_the_probe_is_the_starts_check_and_says_why_it_failed(storage_settings, monkeypatch):
    from app import storage

    def _missing(self):
        raise storage.StorageNotReady("The storage bucket 'evidence' does not exist.")

    monkeypatch.setattr(storage.MinIOClient, "check_bucket", _missing)
    assert deployment_checks.storage_probe() == (
        "failed: The storage bucket 'evidence' does not exist."
    )


def test_the_probe_reads_ok_and_is_cached(storage_settings, monkeypatch):
    from app import storage

    calls = []
    monkeypatch.setattr(storage.MinIOClient, "check_bucket", lambda self: calls.append(1))
    assert deployment_checks.storage_probe() == "ok"
    assert deployment_checks.storage_probe() == "ok"
    assert len(calls) == 1, "a second /health inside the TTL must not call the store"


def test_the_probe_never_raises(storage_settings, monkeypatch):
    from app import storage

    def _boom(self):
        raise ValueError("anything")

    monkeypatch.setattr(storage.MinIOClient, "check_bucket", _boom)
    assert deployment_checks.storage_probe() == "failed: the storage check raised ValueError."


def test_the_probe_is_bounded(storage_settings, monkeypatch):
    """botocore's defaults are 60 s connect and read with retries; /health
    holds a threadpool token while it waits."""
    from app import storage

    seen = {}

    def _record(self):
        cfg = self._s3.meta.config
        seen.update(connect=cfg.connect_timeout, read=cfg.read_timeout,
                    attempts=cfg.retries.get("total_max_attempts"))

    monkeypatch.setattr(storage.MinIOClient, "check_bucket", _record)
    deployment_checks.storage_probe()
    assert seen == {"connect": deployment_checks.PROBE_TIMEOUT,
                    "read": deployment_checks.PROBE_TIMEOUT, "attempts": 1}
    assert deployment_checks.PROBE_TIMEOUT <= 5


def test_no_endpoint_is_not_configured(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_endpoint", None)
    assert deployment_checks.storage_probe().startswith("not configured")


# --- /health -------------------------------------------------------------


def test_failed_storage_is_status_error_with_a_200(health_probe_client, monkeypatch):
    """Jarrod, 2026-11-25: fail the deploy, keep the 200. A restart fixes
    nothing about unreachable storage."""
    monkeypatch.setenv("WINGRC_BUILD_SHA", "a" * 40)
    failed = "failed: Could not reach the object store (EndpointConnectionError)."
    monkeypatch.setattr(deployment_checks, "storage_probe", lambda: failed)
    r = health_probe_client.get("/health", headers={"X-WinGRC-Nginx-Build": "a" * 40})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "error"
    assert body["storage"].startswith("failed:")
    assert body["warnings"][0]["key"] == "storage_unreachable"


def test_working_storage_leaves_status_to_the_components(health_probe_client, monkeypatch):
    monkeypatch.setattr(deployment_checks, "storage_probe", lambda: "ok")
    body = health_probe_client.get("/health").json()
    assert body["storage"] == "ok"
    assert body["status"] == "incomplete", "a direct call has no nginx or worker"
    assert "storage_unreachable" not in [w["key"] for w in body["warnings"]]
