"""A missing breached-password list is reported, never read as "checked".

The bundled list has never existed, and until 2026-11-20 its absence read
as an empty set with nothing logged (docs/roadmap.md, "The bundled
breached-password list does not exist").
"""
from __future__ import annotations

import logging

import pytest

import app.auth as auth_module
from app.config import get_settings


def test_a_missing_list_is_a_problem_while_the_check_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(auth_module, "COMMON_PASSWORDS_PATH", str(tmp_path / "absent.txt"))
    assert get_settings().pwned_passwords_check, "fixture must run with the check on"
    problem = auth_module.common_password_list_problem()
    assert problem is not None and problem.startswith("missing:")


def test_a_present_list_is_not_a_problem(monkeypatch, tmp_path):
    listed = tmp_path / "common_passwords.txt"
    listed.write_text("password\n", encoding="utf-8")
    monkeypatch.setattr(auth_module, "COMMON_PASSWORDS_PATH", str(listed))
    assert auth_module.common_password_list_problem() is None


def test_loading_a_missing_list_logs_an_error(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(auth_module, "COMMON_PASSWORDS_PATH", str(tmp_path / "absent.txt"))
    # The suite's in-process alembic run (fileConfig) disables loggers that
    # already exist; production runs alembic in its own process.
    monkeypatch.setattr(logging.getLogger("app.auth"), "disabled", False)
    with caplog.at_level(logging.ERROR, logger="app.auth"):
        assert auth_module._load_common_passwords() == frozenset()
    assert any("breached-password list missing" in r.getMessage() for r in caplog.records)


@pytest.mark.integration
def test_health_reports_a_missing_list(monkeypatch, tmp_path, db_session):
    """deploy.sh prints /health's body, so every deploy shows it."""
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app
    from tests.conftest import _app_session

    monkeypatch.setattr(auth_module, "COMMON_PASSWORDS_PATH", str(tmp_path / "absent.txt"))
    app.dependency_overrides[get_session] = _app_session(db_session)
    try:
        body = TestClient(app).get("/health").json()
    finally:
        app.dependency_overrides.clear()
    assert body["breached_password_list"].startswith("missing:"), body
