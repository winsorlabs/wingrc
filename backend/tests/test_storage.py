"""Unit tests for storage.py's Content-Disposition helpers, and the
evidence-download-hardening additions: evidence_download_path(),
StorageClient.is_configured()/stream_bytes() defaults.

Evidence/logo download links force `attachment` disposition so browsers save
the file instead of rendering it inline (e.g. images/PDFs) — see
storage.content_disposition(). Titles/filenames feeding into this are
user-supplied, so escaping/injection-safety is the load-bearing behavior
under test here.
"""
from __future__ import annotations

import uuid

from app.storage import (
    NullStorageClient,
    StorageClient,
    content_disposition,
    download_filename,
    evidence_download_path,
)


def test_download_filename_appends_missing_extension():
    assert download_filename("Firewall export", ".csv") == "Firewall export.csv"


def test_download_filename_leaves_existing_extension_alone():
    assert download_filename("screenshot.png", ".png") == "screenshot.png"


def test_download_filename_case_insensitive_extension_match():
    assert download_filename("Report.PDF", ".pdf") == "Report.PDF"


def test_download_filename_no_extension_returns_title_unchanged():
    assert download_filename("some title", "") == "some title"


def test_content_disposition_basic():
    value = content_disposition("report.pdf")
    assert value == "attachment; filename=\"report.pdf\"; filename*=UTF-8''report.pdf"


def test_content_disposition_escapes_quotes_and_backslashes():
    value = content_disposition('evil".txt\\')
    # Quoted-string fallback must escape embedded " and \ per RFC 2183.
    assert 'filename="evil\\".txt\\\\"' in value


def test_content_disposition_strips_crlf_to_prevent_header_injection():
    value = content_disposition("title\r\nX-Injected: true.txt")
    assert "\r" not in value
    assert "\n" not in value


def test_content_disposition_encodes_non_ascii_in_extended_value():
    value = content_disposition("résumé.pdf")
    assert "filename*=UTF-8''r%C3%A9sum%C3%A9.pdf" in value
    # ASCII fallback replaces non-ASCII chars rather than failing.
    assert 'filename="r?sum?.pdf"' in value


# ---------------------------------------------------------------------------
# evidence_download_path() -- app-relative, no /api prefix (that's the
# frontend's job, see frontend/src/api.ts's assetUrl())
# ---------------------------------------------------------------------------


def test_evidence_download_path_shape():
    org_id = uuid.uuid4()
    ev_id = uuid.uuid4()
    assert evidence_download_path(org_id, ev_id) == f"/orgs/{org_id}/evidence/{ev_id}/download"


def test_evidence_download_path_is_not_absolute():
    path = evidence_download_path(uuid.uuid4(), uuid.uuid4())
    assert not path.startswith("http")
    assert not path.startswith("/api")


# ---------------------------------------------------------------------------
# StorageClient defaults -- what an unconfigured/Null deployment does
# ---------------------------------------------------------------------------


class _MinimalStorageClient(StorageClient):
    """The bare minimum a real client must implement -- proves the base
    class's own is_configured()/get_bytes()/stream_bytes() defaults apply
    to any subclass that doesn't override them, not just NullStorageClient."""

    def upload_file(self, key: str, data: bytes, content_type: str) -> None:
        pass

    def presigned_url(
        self, key: str, expires_in: int = 300, download_filename: str | None = None
    ) -> str:
        return ""

    def delete_file(self, key: str) -> None:
        pass


def test_storage_client_default_is_configured_true():
    assert _MinimalStorageClient().is_configured() is True


def test_storage_client_default_get_bytes_empty():
    assert _MinimalStorageClient().get_bytes("any-key") == b""


def test_storage_client_default_stream_bytes_empty_iterator():
    assert list(_MinimalStorageClient().stream_bytes("any-key")) == []


def test_null_storage_client_is_not_configured():
    """The signal download_evidence uses to 404 with "Storage not
    configured" instead of silently streaming zero bytes."""
    assert NullStorageClient().is_configured() is False


def test_null_storage_client_stream_bytes_empty():
    assert list(NullStorageClient().stream_bytes("any-key")) == []
