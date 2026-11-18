"""Secure follows WINGRC_ENVIRONMENT on every cookie the app sets.

Until 2026-11-17 docker-compose.yml pinned WINGRC_ENVIRONMENT to the literal
"development", so compose deployments, live included, issued every cookie
without Secure (docs/roadmap.md, "The compose file ships default
credentials"). There are four helpers that set a cookie, and these tests
hold all four, plus the claim that nothing else sets one.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import Response
from pydantic import ValidationError

from app import auth
from app.config import Settings, get_settings

KEY = "a-deployment-key-that-is-long-enough-0123456789"

# Written out, not discovered: each helper and how to call it.
HELPERS = {
    "set_session_cookie": lambda r: auth.set_session_cookie(r, "raw-token"),
    "clear_session_cookie": lambda r: auth.clear_session_cookie(r),
    "set_state_cookie": lambda r: auth.set_state_cookie(r, "wingrc_mfa_pending", {"x": 1}),
    "clear_state_cookie": lambda r: auth.clear_state_cookie(r, "wingrc_mfa_pending"),
}


@pytest.fixture
def environment(monkeypatch):
    def set_env(value):
        monkeypatch.setenv("WINGRC_ENVIRONMENT", value)
        monkeypatch.setenv("WINGRC_AUTH_FLOW_SECRET", KEY)
        get_settings.cache_clear()

    yield set_env
    get_settings.cache_clear()


def _set_cookie_headers(response: Response) -> list[str]:
    return [v.decode() for k, v in response.raw_headers if k.decode().lower() == "set-cookie"]


@pytest.mark.parametrize("helper", sorted(HELPERS))
def test_production_sets_secure(environment, helper):
    environment("production")
    response = Response()
    HELPERS[helper](response)
    headers = _set_cookie_headers(response)
    assert len(headers) == 1, f"fixture must set exactly one cookie, got {headers}"
    assert re.search(r";\s*secure(;|$)", headers[0], re.IGNORECASE), headers[0]


@pytest.mark.parametrize("helper", sorted(HELPERS))
def test_development_does_not(environment, helper):
    environment("development")
    response = Response()
    HELPERS[helper](response)
    headers = _set_cookie_headers(response)
    assert len(headers) == 1, f"fixture must set exactly one cookie, got {headers}"
    assert not re.search(r";\s*secure(;|$)", headers[0], re.IGNORECASE), headers[0]


@pytest.mark.parametrize("value", ["Production", "prod", "staging", ""])
def test_any_other_value_refuses_at_startup(monkeypatch, value):
    """A typo must not silently mean "not production"."""
    monkeypatch.setenv("WINGRC_ENVIRONMENT", value)
    with pytest.raises(ValidationError):
        Settings()


def test_nothing_else_sets_a_cookie():
    """The four helpers are the only cookie writers, so the tests above
    cover every cookie. A new set_cookie call elsewhere fails here."""
    app_dir = Path(auth.__file__).resolve().parent
    found = []
    for path in sorted(app_dir.rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.(set_cookie|delete_cookie)\(", line):
                found.append((path.relative_to(app_dir).as_posix(), n))
    in_auth = [f for f in found if f[0] == "auth.py"]
    assert found == in_auth, f"cookie writers outside auth.py: {sorted(set(found) - set(in_auth))}"
    assert len(in_auth) == len(HELPERS), (
        f"auth.py has {len(in_auth)} set_cookie calls; HELPERS lists {len(HELPERS)}"
    )
