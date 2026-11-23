"""What the app's object-storage account may do, and what it is granted.

Until 2026-11-23 the app reached MinIO as root. `MinIOClient.__init__`
created the bucket when it found none, and no scoped account can be
given that, so the only credential the code could start with was the
one that can do everything: MinIO's admin API, every account and policy,
every bucket. Bucket creation moved to provisioning (`app/provision.py`),
and the app's account is now granted `APP_ACTIONS` on this bucket's
objects and nothing else (docs/roadmap.md, "The app reaches MinIO as
root").

**The policy, from the code, method by method** (`storage.MinIOClient`).
Every action is on `arn:aws:s3:::<bucket>/*`, the bucket's objects; none
is on the bucket itself or on any other bucket:

- `upload_file`: `put_object` -> `s3:PutObject`.
- `presigned_url`: signing is local, but the browser's GET runs with the
  signer's rights -> `s3:GetObject`.
- `delete_file`: `delete_object` -> `s3:DeleteObject`.
- `get_bytes`, `stream_bytes`, `open_object`: `get_object` ->
  `s3:GetObject`.
- `check_bucket`, the start's check: `get_object` of a key that never
  exists -> `s3:GetObject`. Measured on MinIO 2025-09-07: without
  `s3:ListBucket`, a missing key still answers `NoSuchKey` and a missing
  bucket `NoSuchBucket`, so the check needs no more than reads do.
- `list_objects` and `bucket`: `snapshots.py` only (create, the
  restore's empty-bucket check, verify). They run in the provisioning
  container as root (deploy/snapshot.sh), so `s3:ListBucket` is not the
  serving account's.

`tests/test_storage_access.py` holds this list written out, and fails
when a method is added to `MinIOClient` without a line here.

**The reading** (`account_reading`) is what MinIO says the account is
granted, from its `accountinfo` admin call, which any account may make
about itself. It is reported on `/health` as `storage_account`, the way
`database_role` reports the database's, so a deployment still on root
says so in every deploy's output. It is read with a plain signed GET
rather than the `minio` SDK, so the serving process never imports the
SDK's key-derivation libraries (they are for provisioning's encrypted
admin calls only).
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

APP_ACTIONS = ("s3:GetObject", "s3:PutObject", "s3:DeleteObject")
POLICY_NAME = "wingrc-app"
DOC = 'See docs/deployment.md, "Object storage account".'


def object_resource(bucket: str) -> str:
    return f"arn:aws:s3:::{bucket}/*"


def app_policy(bucket: str) -> dict:
    """The policy provisioning attaches to the app's account."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": list(APP_ACTIONS),
            "Resource": [object_resource(bucket)],
        }],
    }


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]


def access_verdict(policy: dict | None, bucket: str) -> dict:
    """What an account's policy grants beyond the app's needs, and whether
    it administers the store. Allow statements only: a Deny or a Condition
    can only narrow them, so reading without them over-reports, never
    under-reports. A NotAction or NotResource statement grants by
    exclusion and is reported as beyond the app.
    """
    wanted = object_resource(bucket)
    actions: set[str] = set()
    administers = False
    beyond: list[str] = []
    granted: set[str] = set()
    for st in _as_list((policy or {}).get("Statement")):
        if not isinstance(st, dict) or st.get("Effect") != "Allow":
            continue
        if "NotAction" in st or "NotResource" in st:
            beyond.append("a NotAction or NotResource statement")
            continue
        resources = _as_list(st.get("Resource")) or ["*"]
        for a in _as_list(st.get("Action")):
            actions.add(a)
            if a == "*" or a.startswith("admin:"):
                administers = True
                continue
            for r in resources:
                if a in APP_ACTIONS and r == wanted:
                    granted.add(a)
                else:
                    beyond.append(f"{a} on {r}")
    missing = [a for a in APP_ACTIONS if a not in granted]
    if administers:
        access = (
            "unscoped: the app's storage account administers the object store "
            "(MinIO's root, or an admin policy). Anyone holding the app's storage "
            "keys can make and remove accounts, policies and buckets, and read "
            f"every bucket. {DOC}"
        )
    elif beyond:
        access = (
            f"unscoped: the app's storage account is granted more than it uses: "
            f"{', '.join(sorted(set(beyond)))}. {DOC}"
        )
    elif missing:
        access = f"incomplete: the app's storage account lacks {', '.join(missing)}. {DOC}"
    else:
        access = "scoped"
    return {
        "actions": sorted(actions),
        "administers": administers,
        "beyond_the_app": sorted(set(beyond)),
        "missing": missing,
        "access": access,
    }


def read_account_info(endpoint: str, access_key: str, secret_key: str, region: str) -> dict:
    """MinIO's `accountinfo` for these keys: GET /minio/admin/v3/accountinfo,
    signed SigV4 for service s3. Raises on any failure; the caller reports."""
    from botocore.auth import S3SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    url = endpoint.rstrip("/") + "/minio/admin/v3/accountinfo"
    req = AWSRequest(
        method="GET", url=url,
        headers={"x-amz-content-sha256": hashlib.sha256(b"").hexdigest()},
    )
    S3SigV4Auth(Credentials(access_key, secret_key), "s3", region or "us-east-1").add_auth(req)
    with urllib.request.urlopen(  # noqa: S310 -- the configured storage endpoint
        urllib.request.Request(url, headers=dict(req.headers.items())), timeout=5
    ) as resp:
        return json.loads(resp.read())


def account_reading(endpoint: str, access_key: str, secret_key: str, region: str,
                    bucket: str) -> dict:
    """The verdict for these keys, or `access: unknown: ...` when MinIO
    cannot be asked. Never names the account: the access key is half a
    credential and /health is unauthenticated."""
    try:
        info = read_account_info(endpoint, access_key, secret_key, region)
    except urllib.error.HTTPError as e:
        return {"access": f"unknown: accountinfo answered HTTP {e.code}. {DOC}"}
    except Exception as e:  # noqa: BLE001 -- reported, not raised
        return {"access": f"unknown: could not ask the store ({type(e).__name__}). {DOC}"}
    policy = info.get("Policy")
    if isinstance(policy, str):
        try:
            policy = json.loads(policy)
        except ValueError:
            policy = None
    return {"bucket": bucket, **access_verdict(policy, bucket)}


_TTL_SECONDS = 300
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def health_reading() -> dict:
    """`/health`'s `storage_account`, read at most every five minutes per
    process: the healthcheck calls /health every few seconds, and the
    account's grants change only through provisioning."""
    from .config import get_settings

    s = get_settings()
    if not s.storage_endpoint:
        return {
            "access": "not configured: WINGRC_STORAGE_ENDPOINT is unset, so uploads are discarded"
        }
    now = time.monotonic()
    with _lock:
        hit = _cache.get("reading")
        if hit and hit[0] > now:
            return hit[1]
    reading = account_reading(
        s.storage_endpoint, s.storage_access_key or "", s.storage_secret_key or "",
        s.storage_region, s.storage_bucket,
    )
    # An unanswered read is retried sooner, so a store that was still
    # starting does not leave "unknown" in the body for five minutes.
    ttl = 30 if reading["access"].startswith("unknown") else _TTL_SECONDS
    with _lock:
        _cache["reading"] = (now + ttl, reading)
    return reading
