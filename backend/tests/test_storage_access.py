"""The app's object-storage account: what it may do, and what /health says.

No database and no object store: boto3's Stubber stands in for MinIO,
whose real answers were measured on the bench (storage_access.py's
docstring) and are repeated against a real MinIO there.
"""
from __future__ import annotations

import inspect
import urllib.error

import pytest
from botocore.stub import Stubber

from app import provision, storage_access
from app.storage import MinIOClient, StorageNotReady
from app.storage_access import access_verdict, app_policy

BUCKET = "evidence"


def _client() -> MinIOClient:
    return MinIOClient(
        endpoint="http://storage.invalid:9000", access_key="k", secret_key="s",
        bucket=BUCKET, region="us-east-1",
    )


# --- The policy, written out --------------------------------------------


def test_the_app_is_granted_three_object_actions_on_its_bucket():
    assert storage_access.APP_ACTIONS == ("s3:GetObject", "s3:PutObject", "s3:DeleteObject")
    assert app_policy(BUCKET) == {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
            "Resource": ["arn:aws:s3:::evidence/*"],
        }],
    }


def test_every_client_method_is_accounted_for_in_the_policy_derivation():
    """storage_access.py's docstring derives the policy method by method.
    A method added to MinIOClient fails here until it has a line there."""
    methods = {n for n, _ in inspect.getmembers(MinIOClient) if not n.startswith("_")}
    assert methods == {
        "upload_file", "presigned_url", "delete_file", "get_bytes", "stream_bytes",
        "open_object", "list_objects", "bucket", "check_bucket", "is_configured",
    }
    doc = storage_access.__doc__ or ""
    for m in methods - {"is_configured"}:
        assert f"`{m}`" in doc, m


# --- The verdict: cases written out --------------------------------------

ROOT = {"Version": "2012-10-17", "Statement": [
    {"Effect": "Allow", "Action": ["admin:*"]},
    {"Effect": "Allow", "Action": ["kms:*"]},
    {"Effect": "Allow", "Action": ["s3:*"], "Resource": ["arn:aws:s3:::*"]},
]}


def test_the_apps_own_policy_reads_scoped():
    v = access_verdict(app_policy(BUCKET), BUCKET)
    assert v["access"] == "scoped"
    assert v["administers"] is False
    assert v["beyond_the_app"] == [] and v["missing"] == []


def test_root_reads_unscoped_and_says_it_administers_the_store():
    """MinIO's accountinfo for root, as measured: admin:*, kms:*, s3:*."""
    v = access_verdict(ROOT, BUCKET)
    assert v["administers"] is True
    assert v["access"].startswith("unscoped: the app's storage account administers the object store")
    assert "Object storage account" in v["access"]


@pytest.mark.parametrize(("statement", "named"), [
    ({"Action": ["s3:*"], "Resource": ["arn:aws:s3:::evidence/*"]}, "s3:* on arn:aws:s3:::evidence/*"),
    ({"Action": ["s3:GetObject"], "Resource": ["arn:aws:s3:::*"]}, "s3:GetObject on arn:aws:s3:::*"),
    ({"Action": ["s3:ListBucket"], "Resource": ["arn:aws:s3:::evidence"]}, "s3:ListBucket on arn:aws:s3:::evidence"),
    ({"Action": ["s3:CreateBucket"], "Resource": ["arn:aws:s3:::*"]}, "s3:CreateBucket on arn:aws:s3:::*"),
    ({"Action": "s3:GetObject", "Resource": "arn:aws:s3:::other/*"}, "s3:GetObject on arn:aws:s3:::other/*"),
])
def test_anything_beyond_the_three_on_this_bucket_reads_unscoped_and_is_named(statement, named):
    policy = app_policy(BUCKET)
    policy["Statement"].append({"Effect": "Allow", **statement})
    v = access_verdict(policy, BUCKET)
    assert v["access"].startswith("unscoped: the app's storage account is granted more than it uses")
    assert named in v["beyond_the_app"]


def test_a_missing_action_reads_incomplete():
    policy = app_policy(BUCKET)
    policy["Statement"][0]["Action"] = ["s3:GetObject", "s3:PutObject"]
    v = access_verdict(policy, BUCKET)
    assert v["missing"] == ["s3:DeleteObject"]
    assert v["access"].startswith("incomplete:")


def test_a_deny_is_not_read_as_a_grant():
    policy = app_policy(BUCKET)
    policy["Statement"].append({"Effect": "Deny", "Action": ["s3:*"], "Resource": ["*"]})
    assert access_verdict(policy, BUCKET)["access"] == "scoped"


def test_a_not_action_statement_reads_unscoped():
    policy = app_policy(BUCKET)
    policy["Statement"].append({"Effect": "Allow", "NotAction": ["s3:PutObject"], "Resource": ["*"]})
    assert access_verdict(policy, BUCKET)["access"].startswith("unscoped:")


def test_an_unanswered_read_says_unknown_and_names_nothing(monkeypatch):
    def refuse(*a, **k):
        raise urllib.error.HTTPError("http://x", 403, "Forbidden", {}, None)

    monkeypatch.setattr(storage_access, "read_account_info", refuse)
    r = storage_access.account_reading("http://x", "the-key", "the-secret", "us-east-1", BUCKET)
    assert r == {"access": f"unknown: accountinfo answered HTTP 403. {storage_access.DOC}"}
    assert "the-key" not in str(r) and "the-secret" not in str(r)


def test_the_reading_never_names_the_account(monkeypatch):
    monkeypatch.setattr(storage_access, "read_account_info", lambda *a: {
        "AccountName": "the-key", "Policy": app_policy(BUCKET), "Buckets": [],
    })
    r = storage_access.account_reading("http://x", "the-key", "s", "us-east-1", BUCKET)
    assert r["access"] == "scoped"
    assert "the-key" not in str(r)


def test_no_endpoint_reads_not_configured(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_endpoint", None)
    assert storage_access.health_reading()["access"].startswith("not configured")


# --- The app never creates the bucket ------------------------------------


def test_constructing_the_client_makes_no_call():
    """It used to head the bucket and create it on any error: the reason
    the app needed root. Stubber with nothing queued fails on any call."""
    from unittest import mock

    import boto3

    real = boto3.client
    made = []

    def track(*a, **k):
        c = real(*a, **k)
        s = Stubber(c)
        s.activate()
        made.append(s)
        return c

    with mock.patch("boto3.client", side_effect=track):
        _client()
    assert made, "fixture must build its boto3 clients through boto3.client, or this test proves nothing"
    for s in made:
        s.assert_no_pending_responses()


def _check_with(code: str, status: int):
    c = _client()
    with Stubber(c._s3) as s:
        s.add_client_error("get_object", service_error_code=code, http_status_code=status)
        try:
            return c.check_bucket()
        finally:
            s.assert_no_pending_responses()


def test_a_bucket_that_is_there_passes_the_check():
    assert _check_with("NoSuchKey", 404) is None


def test_a_missing_bucket_is_named_with_the_provisioning_step_and_never_created():
    """A create would be a second, unqueued call, and Stubber would fail it."""
    with pytest.raises(StorageNotReady) as e:
        _check_with("NoSuchBucket", 404)
    msg = str(e.value)
    assert "'evidence' does not exist" in msg
    assert "docker compose run --rm --no-deps provision" in msg
    assert "CreateBucket" not in msg


def test_an_unreadable_bucket_says_get_object_was_refused():
    with pytest.raises(StorageNotReady) as e:
        _check_with("AccessDenied", 403)
    assert "GetObject: AccessDenied" in str(e.value)


# --- Provisioning ----------------------------------------------------------


def _root_s3():
    import boto3

    return boto3.client("s3", endpoint_url="http://storage.invalid:9000",
                        aws_access_key_id="r", aws_secret_access_key="r", region_name="us-east-1")


def test_provisioning_creates_only_a_missing_bucket():
    s3 = _root_s3()
    with Stubber(s3) as s:
        s.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
        s.add_response("create_bucket", {}, {"Bucket": BUCKET})
        assert provision.ensure_bucket(s3, BUCKET) == "created"
        s.assert_no_pending_responses()
    with Stubber(s3) as s:
        s.add_response("head_bucket", {}, {"Bucket": BUCKET})
        assert provision.ensure_bucket(s3, BUCKET) == "exists"


def test_provisioning_raises_a_refusal_rather_than_creating():
    s3 = _root_s3()
    with Stubber(s3) as s, pytest.raises(Exception, match="403"):
        s.add_client_error("head_bucket", service_error_code="403", http_status_code=403)
        provision.ensure_bucket(s3, BUCKET)


def test_provisioning_makes_no_account_when_the_apps_keys_are_root(monkeypatch, capsys):
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "storage_endpoint", "http://storage.invalid:9000")
    monkeypatch.setattr(s, "storage_access_key", "root")
    monkeypatch.setattr(s, "storage_secret_key", "rootpw")
    monkeypatch.setenv(provision.APP_KEY_ENV, "root")
    monkeypatch.setenv(provision.APP_SECRET_ENV, "rootpw")
    monkeypatch.setattr(provision, "ensure_bucket", lambda s3, b: "exists")
    monkeypatch.setattr(provision, "attach", lambda *a: pytest.fail("no account is made for root"))
    provision.main()
    out = capsys.readouterr().out
    assert "the app's storage keys are MinIO's root, so no account was made" in out
    assert "rootpw" not in out


def test_provisioning_refuses_without_the_apps_keys(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_endpoint", "http://storage.invalid:9000")
    monkeypatch.delenv(provision.APP_KEY_ENV, raising=False)
    with pytest.raises(SystemExit, match=provision.APP_KEY_ENV):
        provision.main()
