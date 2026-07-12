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

from abc import ABC, abstractmethod
from functools import lru_cache


class StorageClient(ABC):
    @abstractmethod
    def upload_file(self, key: str, data: bytes, content_type: str) -> None: ...

    @abstractmethod
    def presigned_url(self, key: str, expires_in: int = 300) -> str: ...

    @abstractmethod
    def delete_file(self, key: str) -> None: ...

    def get_bytes(self, key: str) -> bytes:  # noqa: ARG002
        """Download and return object bytes. NullStorageClient returns b''.
        Override in real clients. Tests that need embedded files override this."""
        return b""


class NullStorageClient(StorageClient):
    """Used when no storage endpoint is configured.  Bytes are discarded."""

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        pass

    def presigned_url(self, key: str, expires_in: int = 300) -> str:
        return ""

    def delete_file(self, key: str) -> None:
        pass


class MinIOClient(StorageClient):
    """S3-compatible client via boto3.  Auto-creates the bucket on first use.

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
    ) -> None:
        import boto3  # lazy — only installed when storage is configured
        from botocore.client import Config

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
            ),
        )
        self._s3 = boto3.client("s3", endpoint_url=endpoint, **client_kwargs)
        self._s3_pub = (
            boto3.client("s3", endpoint_url=public_endpoint, **client_kwargs)
            if public_endpoint
            else self._s3
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self._s3.head_bucket(Bucket=self._bucket)
        except Exception:
            self._s3.create_bucket(Bucket=self._bucket)

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def presigned_url(self, key: str, expires_in: int = 300) -> str:
        return self._s3_pub.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete_file(self, key: str) -> None:
        self._s3.delete_object(Bucket=self._bucket, Key=key)

    def get_bytes(self, key: str) -> bytes:
        resp = self._s3.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()  # type: ignore[no-any-return]


@lru_cache(maxsize=1)
def _build_client() -> StorageClient:
    from .config import get_settings

    s = get_settings()
    if s.storage_endpoint:
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
