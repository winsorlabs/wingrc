"""Provisioning: the privileged one-shot step.

It runs in compose's `provision` service, which holds credentials no
serving container holds, does its work, and exits before `backend`
starts (`depends_on: condition: service_completed_successfully`; and
deploy.sh runs it before recreating the app, because `up --no-deps` skips
dependencies). Today it holds MinIO's root and does the object store's
part:

1. creates the bucket if it is missing (the app never does:
   `storage.MinIOClient`);
2. writes the app's policy (`storage_access.app_policy`), creates or
   updates the app's account with the keys in `.env`, and attaches the
   policy. `user_add` on an existing account replaces its secret, so a
   rotation is: new secret in `.env`, then provision, then recreate the
   app;
3. reads the account back as the app, through the same reading `/health`
   reports, and fails unless it is scoped.

When the app's keys are MinIO's root (a deployment that has not moved,
docs/deployment.md, "Object storage account"), it does step 1 only and
says so; `/health` then reads `unscoped`.

**The database owner is meant to join this container** (the second half
of the same design; docs/roadmap.md, "The app reaches MinIO as root"):
migrations and `app_role`'s password step move here from start.sh, and
`WINGRC_MIGRATION_DATABASE_URL` leaves `x-backend-env` for
`x-provision-env`. The three things that read the owner's URL from the
backend's environment today each have a place here already: snapshots
run in this container (deploy/snapshot.sh, since this slice), a
privileged CLI command becomes `docker compose run --rm --no-deps
provision wingrc ...`, and migrations at start become a step of this
module.

Prints names, never key values.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from .config import get_settings
from .storage_access import POLICY_NAME, account_reading, app_policy

APP_KEY_ENV = "WINGRC_PROVISION_APP_ACCESS_KEY"
APP_SECRET_ENV = "WINGRC_PROVISION_APP_SECRET_KEY"


def _root_s3(s):
    import boto3
    from botocore.client import Config

    return boto3.client(
        "s3", endpoint_url=s.storage_endpoint,
        aws_access_key_id=s.storage_access_key, aws_secret_access_key=s.storage_secret_key,
        region_name=s.storage_region,
        config=Config(signature_version="s3v4", request_checksum_calculation="when_required",
                      response_checksum_validation="when_required"),
    )


def ensure_bucket(s3, bucket: str) -> str:
    """'exists' or 'created'. Only a missing bucket leads to a create;
    any other error is raised as it is."""
    from botocore.exceptions import ClientError

    try:
        s3.head_bucket(Bucket=bucket)
        return "exists"
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
            raise
    try:
        s3.create_bucket(Bucket=bucket)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
            raise
    return "created"


def attach(admin, user: str) -> str:
    """Attach the app's policy; already attached is fine."""
    from minio.error import MinioAdminException

    try:
        admin.attach_policy([POLICY_NAME], user=user)
        return "attached"
    except MinioAdminException as e:
        if "XMinioAdminPolicyChangeAlreadyApplied" in str(e):
            return "already attached"
        raise


def main() -> None:
    s = get_settings()
    if not s.storage_endpoint:
        print("provision: no storage endpoint set; nothing to provision.")
        return
    app_key, app_secret = os.environ.get(APP_KEY_ENV), os.environ.get(APP_SECRET_ENV)
    if not app_key or not app_secret:
        raise SystemExit(
            f"provision: {APP_KEY_ENV} and {APP_SECRET_ENV} are required; compose sets "
            "them from WINGRC_STORAGE_ACCESS_KEY and WINGRC_STORAGE_SECRET_KEY."
        )
    bucket = s.storage_bucket
    print(f"provision: bucket {bucket!r} {ensure_bucket(_root_s3(s), bucket)}.")

    if app_key == s.storage_access_key:
        print(
            "provision: the app's storage keys are MinIO's root, so no account was "
            "made and /health will read storage_account unscoped. Moving off root: "
            'docs/deployment.md, "Moving a deployment off MinIO\'s root".'
        )
        return

    from minio.credentials import StaticProvider
    from minio.minioadmin import MinioAdmin

    ep = urlparse(s.storage_endpoint)
    admin = MinioAdmin(
        endpoint=ep.netloc, credentials=StaticProvider(s.storage_access_key, s.storage_secret_key),
        region=s.storage_region, secure=ep.scheme == "https",
    )
    admin.policy_add(POLICY_NAME, policy=app_policy(bucket))
    admin.user_add(app_key, app_secret)
    print(f"provision: app account set; policy {POLICY_NAME!r} {attach(admin, app_key)}.")

    reading = account_reading(s.storage_endpoint, app_key, app_secret, s.storage_region, bucket)
    print(f"provision: the app's account reads {reading['access']}")
    if reading["access"] != "scoped":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
