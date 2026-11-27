"""Storage abstraction for evidence artifacts.

In development/tests: NullStorageClient is used when WINGRC_STORAGE_ENDPOINT
is unset — uploads are accepted but bytes are discarded.

In production: MinIOClient wraps boto3 (S3-compatible).  Targets MinIO for
self-host; swap endpoint for AWS S3 or Azure Blob in cloud deployments.

FastAPI dep:
    storage: StorageClient = Depends(get_storage_client)

Test override:
    app.dependency_overrides[get_storage_client] = lambda: InMemoryStorageClient()
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterator
from functools import lru_cache
from urllib.parse import quote

# Default read-chunk size for stream_bytes(): large enough to keep the
# per-chunk threadpool round-trip (see stream_bytes' own docstring) from
# dominating, small enough that no single chunk is a meaningful memory
# spike even under many concurrent downloads.
_DEFAULT_CHUNK_SIZE = 256 * 1024


def evidence_download_path(org_id: uuid.UUID, evidence_id: uuid.UUID) -> str:
    """The backend route an Evidence file streams from -- shared by
    routers/evidence.py (its own EvidenceOut.download_url) and
    routers/orgs.py (system-description diagrams, which are Evidence rows
    too, just displayed inline rather than linked). A bare app-relative
    path, not an absolute URL -- the frontend's own /api mount prefix is
    a deployment detail (nginx/Vite dev proxy) this module has no business
    knowing about; see frontend/src/api.ts's BASE + assetUrl().
    """
    return f"/orgs/{org_id}/evidence/{evidence_id}/download"


def download_filename(title: str, ext: str) -> str:
    """Filename to force-download as, given a display title and extension.

    Appends ext only if title doesn't already end with it (case-insensitive)
    — covers both the default (raw filename, already has the extension) and
    custom-title cases.
    """
    if ext and not title.lower().endswith(ext.lower()):
        return f"{title}{ext}"
    return title


def content_disposition(filename: str) -> str:
    """Build an RFC 6266 'attachment' Content-Disposition value for `filename`.

    Forces the browser to save rather than render inline, regardless of
    content type. Includes both a quoted-string ASCII fallback (filename=,
    for older clients) and a UTF-8 percent-encoded extended value
    (filename*=, RFC 5987) so non-ASCII names still round-trip correctly.
    filename is user-supplied (evidence title / org name); CR/LF are
    stripped and quote/backslash escaped to prevent header injection.
    """
    filename = filename.replace("\r", "").replace("\n", "")
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii")
    ascii_fallback = ascii_fallback.replace("\\", "\\\\").replace('"', '\\"')
    encoded = quote(filename, safe="")
    return f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{encoded}'


class StorageClient(ABC):
    @abstractmethod
    def upload_file(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def presigned_url(
        self, key: str, expires_in: int = 300, download_filename: str | None = None
    ) -> str:
        """Presigned GET URL — a bearer credential: anyone holding the link
        can fetch the object until it expires, with no per-request check of
        session validity, org membership, MFA state, or lockout, and no
        audit trail of who actually used it.

        Non-sensitive, non-CUI-adjacent display assets ONLY (the org logo
        is the one caller left — routers/orgs.py's _build_profile_out /
        upload_logo). Do NOT use this for Evidence or anything backed by an
        Evidence row (screenshots/exports of a customer's security-control
        configuration, network/data-flow diagrams) — those stream through
        the backend instead so every access re-checks the requester's
        session (see stream_bytes() below, and routers/evidence.py's
        download_evidence / storage.evidence_download_path). Evidence
        download hardening (docs/roadmap.md) is the reason this docstring
        exists at all — read it before reaching for this method again.
        """
        ...

    @abstractmethod
    def delete_file(self, key: str) -> None: ...

    def is_configured(self) -> bool:
        """False only for NullStorageClient. Lets a caller distinguish
        "no object at this key" from "no storage backend at all" without
        inferring it from an empty bytes/iterator result, which get_bytes()/
        stream_bytes() also (legitimately) return for a zero-byte object.
        """
        return True

    def get_bytes(self, key: str) -> bytes:  # noqa: ARG002
        """Download and return object bytes. NullStorageClient returns b''.
        Override in real clients. Tests that need embedded files override this.

        Whole-object read — fine for bundle export (bounded, one deliberate
        operation) but never for a hot per-request download path; use
        stream_bytes() there instead.
        """
        return b""

    def stream_bytes(  # noqa: ARG002
        self, key: str, chunk_size: int = _DEFAULT_CHUNK_SIZE
    ) -> Iterator[bytes]:
        """Yield the object's bytes in chunks without holding the whole
        file in memory at once — the hot-path counterpart to get_bytes().
        NullStorageClient/default: empty iterator. Override in real clients.

        Passed directly to a Starlette StreamingResponse in
        routers/evidence.py: a plain (sync) iterator is fine there —
        Starlette wraps it in iterate_in_threadpool, dispatching each
        next() call (one chunk read) through anyio's worker threadpool
        individually rather than pinning one worker for the whole
        transfer. See docs/roadmap.md's evidence-download-hardening entry
        for the load measurement behind that claim.
        """
        return iter(())


class NullStorageClient(StorageClient):
    """Used when no storage endpoint is configured.  Bytes are discarded."""

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        pass

    def presigned_url(
        self, key: str, expires_in: int = 300, download_filename: str | None = None
    ) -> str:
        return ""

    def delete_file(self, key: str) -> None:
        pass

    def is_configured(self) -> bool:
        return False


class StorageNotReady(RuntimeError):
    """The bucket cannot be used: missing, unreadable, or unreachable. The
    message names the cause and the provisioning step."""


# A key nothing writes. Reading it tells "bucket missing" from "bucket
# there" with only s3:GetObject: MinIO answers NoSuchKey or NoSuchBucket
# (measured 2026-11-23; storage_access.py).
_CHECK_KEY = ".wingrc/start-check-never-written"
_PROVISION = "`docker compose run --rm --no-deps provision` (docs/deployment.md, \"Provisioning\")"


class MinIOClient(StorageClient):
    """S3-compatible client via boto3.

    It never creates the bucket. Until 2026-11-23 the constructor did
    (head, then create on any error), which is why the app had to hold
    MinIO's root credentials: no scoped account can be granted bucket
    creation. Provisioning creates it (app/provision.py), the start checks
    it (`check_bucket`), and constructing a client makes no call at all
    (tests/test_storage_access.py). The policy the app's account is
    granted, method by method, is in storage_access.py.

    Two boto3 clients are created when public_endpoint is set:
      _s3      — internal endpoint; used for upload/delete (backend→MinIO traffic)
      _s3_pub  — public endpoint; used for presigned URL generation so URLs
                 contain a host browsers can resolve (e.g. LAN IP, not 'minio')
    When public_endpoint is None, _s3_pub falls back to _s3.
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str,
        public_endpoint: str | None = None,
        timeout: float | None = None,
    ) -> None:
        import boto3  # lazy — only installed when storage is configured
        from botocore.client import Config

        # A bound for a caller that must answer quickly (/health's probe,
        # deployment_checks.py): one attempt, connect and read each capped.
        # botocore's defaults are 60 s each with retries, and /health holds
        # one of the threadpool's 40 tokens while it waits.
        bounded = (
            dict(connect_timeout=timeout, read_timeout=timeout, retries={"total_max_attempts": 1})
            if timeout is not None
            else {}
        )
        self._bucket = bucket
        client_kwargs: dict = dict(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(
                signature_version="s3v4",
                # Suppress Content-MD5 and ETag-MD5 validation: botocore calls
                # hashlib.md5() for these by default, which hard-fails when
                # OpenSSL is in FIPS mode.  "when_required" means: only add a
                # checksum / validate when the API contract requires it (it does
                # not for plain put_object / delete_object against MinIO).
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                **bounded,
            ),
        )
        self._s3 = boto3.client("s3", endpoint_url=endpoint, **client_kwargs)
        self._s3_pub = (
            boto3.client("s3", endpoint_url=public_endpoint, **client_kwargs)
            if public_endpoint
            else self._s3
        )

    def check_bucket(self) -> None:
        """Raise StorageNotReady unless the bucket exists and this account
        may read it. Never creates it. Each error says which: until
        2026-11-23 any failure of the head led to a create, so a scoped
        account on a missing bucket would have reported `CreateBucket
        AccessDenied`, for a call the operator never made."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._s3.get_object(Bucket=self._bucket, Key=_CHECK_KEY)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code == "NoSuchKey":
                return
            if code == "NoSuchBucket":
                raise StorageNotReady(
                    f"The storage bucket {self._bucket!r} does not exist. The app does "
                    f"not create it; provisioning does: {_PROVISION}."
                ) from None
            if code in ("AccessDenied", "403"):
                raise StorageNotReady(
                    f"The storage account may not read bucket {self._bucket!r} "
                    f"(GetObject: AccessDenied). Provisioning grants it: {_PROVISION}."
                ) from None
            raise StorageNotReady(
                f"Checking bucket {self._bucket!r} failed: {code}."
            ) from None
        except BotoCoreError as e:
            raise StorageNotReady(
                f"Could not reach the object store to check bucket {self._bucket!r} "
                f"({type(e).__name__})."
            ) from None

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def presigned_url(
        self, key: str, expires_in: int = 300, download_filename: str | None = None
    ) -> str:
        params: dict = {"Bucket": self._bucket, "Key": key}
        if download_filename:
            params["ResponseContentDisposition"] = content_disposition(download_filename)
        return self._s3_pub.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in,
        )

    def delete_file(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def get_bytes(self, key: str) -> bytes:
        resp = self._s3.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()  # type: ignore[no-any-return]

    def stream_bytes(self, key: str, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> Iterator[bytes]:
        # get_object() itself is one blocking HTTP call (opens the stream;
        # doesn't read the body) -- happens synchronously in the caller's
        # own threadpool-dispatched request, same as any other blocking
        # storage call in this codebase. Body.iter_chunks() is botocore's
        # own incremental reader over the underlying connection -- each
        # chunk is read from the socket on demand, never the whole object
        # at once.
        resp = self._s3.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].iter_chunks(chunk_size=chunk_size)  # type: ignore[no-any-return]

    def list_objects(self) -> Iterator[tuple[str, int]]:
        """Every object in the bucket as (key, size). For snapshots.py only:
        a snapshot copies the whole bucket, not just the keys rows name."""
        pages = self._s3.get_paginator("list_objects_v2").paginate(Bucket=self._bucket)
        for page in pages:
            for obj in page.get("Contents", []):
                yield obj["Key"], obj["Size"]

    def open_object(self, key: str) -> tuple[Iterator[bytes], str | None]:
        """(chunks, content type) for one object; raises if it is absent."""
        resp = self._s3.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].iter_chunks(chunk_size=_DEFAULT_CHUNK_SIZE), resp.get("ContentType")

    @property
    def bucket(self) -> str:
        return self._bucket


@lru_cache(maxsize=1)
def _build_client() -> StorageClient:
    from .config import get_settings

    s = get_settings()
    if s.storage_endpoint:
        missing = [
            name
            for name, value in (
                ("WINGRC_STORAGE_ACCESS_KEY", s.storage_access_key),
                ("WINGRC_STORAGE_SECRET_KEY", s.storage_secret_key),
            )
            if not value
        ]
        if missing:
            # They had public defaults until 2026-11-17; none now.
            raise RuntimeError(
                f"WINGRC_STORAGE_ENDPOINT is set but {' and '.join(missing)} "
                "is not. Set it in .env (docs/deployment.md, step 1)."
            )
        return MinIOClient(
            endpoint=s.storage_endpoint,
            access_key=s.storage_access_key,
            secret_key=s.storage_secret_key,
            bucket=s.storage_bucket,
            region=s.storage_region,
            public_endpoint=s.storage_public_endpoint,
        )
    return NullStorageClient()


def get_storage_client() -> StorageClient:
    """FastAPI dependency.  Override in tests via dependency_overrides."""
    return _build_client()


def main() -> None:
    """The backend's start (start.sh): refuse to serve without a usable
    bucket, naming why. Nothing to check when no endpoint is set."""
    client = _build_client()
    if not isinstance(client, MinIOClient):
        print("storage: no endpoint set, uploads are discarded; nothing to check.")
        return
    try:
        client.check_bucket()
    except StorageNotReady as e:
        raise SystemExit(f"storage: {e}") from None
    print(f"storage: bucket {client.bucket!r} is there and readable.")


if __name__ == "__main__":
    main()
