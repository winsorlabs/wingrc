"""Unit tests for storage.py's Content-Disposition helpers.

Evidence/logo download links force `attachment` disposition so browsers save
the file instead of rendering it inline (e.g. images/PDFs) — see
storage.content_disposition(). Titles/filenames feeding into this are
user-supplied, so escaping/injection-safety is the load-bearing behavior
under test here.
"""
from __future__ import annotations

from app.storage import content_disposition, download_filename


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
