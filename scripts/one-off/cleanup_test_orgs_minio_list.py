# One-off cleanup step 2 of 3: LIST (don't delete) MinIO objects under each
# target org's storage prefix, before touching anything.
#
# Storage keys are org-prefixed for both evidence and logos:
#   evidence: {org_id}/evidence/{evidence_id}/{evidence_id}{ext}
#   logos:    {org_id}/logos/{uuid}{ext}
# so listing by prefix covers everything for an org in one pass, independent
# of what the DB rows say. All four target orgs have zero evidence rows per
# the SQL preview -- anything listed below is therefore either a logo or an
# unexpected leftover, worth seeing before deciding to delete it.
#
# Run via the backend container so it reuses the app's already-configured
# storage credentials (WINGRC_STORAGE_* env vars) instead of needing the
# MinIO root password pasted into a script. After `git pull` on wl-util-1,
# this file lives at scripts/one-off/cleanup_test_orgs_minio_list.py:
#
#   docker compose cp scripts/one-off/cleanup_test_orgs_minio_list.py backend:/tmp/
#   docker compose exec backend python3 /tmp/cleanup_test_orgs_minio_list.py

import boto3

from app.config import get_settings

# Same four org IDs as cleanup_test_orgs.sql.
ORG_IDS = [
    "d57e20df-43c7-42cc-81b2-c6387e1383f1",  # Test
    "16307191-8e64-4433-a025-d830cb334cac",  # Test2
    "a8e452ec-0e48-43da-9841-d9b54248f75d",  # Test3
    "c30d0dab-8638-4772-95d3-e99e1175180a",  # Test4
]

s = get_settings()
s3 = boto3.client(
    "s3",
    endpoint_url=s.storage_endpoint,
    aws_access_key_id=s.storage_access_key,
    aws_secret_access_key=s.storage_secret_key,
    region_name=s.storage_region,
)

total = 0
for org_id in ORG_IDS:
    prefix = f"{org_id}/"
    resp = s3.list_objects_v2(Bucket=s.storage_bucket, Prefix=prefix)
    # list_objects_v2 caps at 1000 keys/page; these are throwaway test orgs
    # so pagination isn't wired up here -- if IsTruncated comes back True,
    # stop and say so rather than silently only seeing the first 1000.
    if resp.get("IsTruncated"):
        print(f"!!! {prefix}: response truncated, more than 1000 objects -- "
              f"investigate before proceeding, this script doesn't paginate")
    contents = resp.get("Contents", [])
    print(f"--- {prefix} ({len(contents)} object(s)) ---")
    for obj in contents:
        print(f"    {obj['Key']}  ({obj['Size']} bytes, modified {obj['LastModified']})")
    total += len(contents)

print(f"\nTotal objects across all {len(ORG_IDS)} org prefixes: {total}")
