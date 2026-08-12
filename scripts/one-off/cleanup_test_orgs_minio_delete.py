# One-off cleanup step 3 of 3: DELETE MinIO objects under each target org's
# storage prefix. Run this ONLY after reviewing
# cleanup_test_orgs_minio_list.py's output and after cleanup_test_orgs.sql
# has committed (not before -- if the SQL run fails/rolls back, deleting the
# files first would orphan DB rows pointing at now-missing objects instead
# of the other way around).
#
# Same ORG_IDS list as the list script and the SQL script -- keep all three
# in sync.
#
#   docker compose exec backend python3 /tmp/cleanup_test_orgs_minio_delete.py

import boto3

from app.config import get_settings

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

for org_id in ORG_IDS:
    prefix = f"{org_id}/"
    resp = s3.list_objects_v2(Bucket=s.storage_bucket, Prefix=prefix)
    contents = resp.get("Contents", [])
    if not contents:
        print(f"{prefix}: nothing to delete")
        continue
    keys = [{"Key": o["Key"]} for o in contents]
    result = s3.delete_objects(Bucket=s.storage_bucket, Delete={"Objects": keys})
    deleted = result.get("Deleted", [])
    errors = result.get("Errors", [])
    print(f"{prefix}: deleted {len(deleted)}/{len(keys)} object(s)")
    for err in errors:
        print(f"    ERROR deleting {err['Key']}: {err['Code']} {err['Message']}")
