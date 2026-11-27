"""The bundled breached-password list refuses what it lists.

The list did not exist until 2026-11-27, and until 2026-11-20 its absence
read as an empty set with nothing logged (docs/roadmap.md, "The bundled
breached-password list does not exist"). So this check reported success
while doing nothing from the beginning. These tests therefore prove it
with a known positive and a near miss, never with an entry count: a count
reports without checking.

HIBP is made unreachable in every test that asks the check, so a refusal
can only come from the bundled list.
"""
from __future__ import annotations

import hashlib
import logging

import pytest

import app.auth as auth_module
from app import breached_passwords
from app.config import get_settings

# In the source list (Pwdb_top-10000000.txt, line 15720) as all lowercase;
# typed here in a case the source does not have, so a refusal shows the
# generator and the lookup lowercase alike.
LISTED = "mAnChEsTeRuNiTeD"
NOT_LISTED = "manchesterunitex"  # one letter off; in neither the source nor the file

@pytest.fixture
def no_hibp(monkeypatch):
    """HIBP unreachable, as on an air-gapped deployment; counts the tries."""
    tries = []

    def refuse(*a, **k):
        tries.append(a)
        raise OSError("HIBP unreachable in this test")

    monkeypatch.setattr(auth_module.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(auth_module, "_COMMON_PASSWORDS", None)
    assert get_settings().pwned_passwords_check, "fixture must run with the check on"
    return tries


# --- the bundled list ----------------------------------------------------


def test_a_listed_password_is_refused_with_hibp_unreachable(no_hibp):
    assert auth_module.check_pwned_password(LISTED) is True


def test_a_near_miss_is_accepted_with_hibp_unreachable(no_hibp):
    assert auth_module.check_pwned_password(NOT_LISTED) is False
    assert no_hibp, "fixture must have refused an HIBP lookup or this test proves nothing"


def test_the_bundled_list_is_not_a_problem():
    assert auth_module.common_password_list_problem() is None


def test_the_list_was_built_for_this_password_policy():
    """An entry outside the policy's bounds can never match, and an entry
    inside them that the build left out is a gap. Regenerate when the
    policy moves."""
    bp = breached_passwords
    header, _ = bp.read()
    assert int(header["min_length"]) == bp.MIN_LEN == auth_module._PASSWORD_MIN_LEN
    assert int(header["max_length"]) == bp.MAX_LEN == auth_module._PASSWORD_MAX_LEN
    assert header["key_version"] == str(bp.KEY_VERSION)
    assert header["source_sha256"] == bp.SOURCE_SHA256
    assert bp.SOURCE_COMMIT in header["source"]


def test_the_file_holds_keys_not_passwords():
    _, keys = breached_passwords.read()
    assert LISTED.lower() not in keys
    assert all(len(k) == breached_passwords.KEY_HEX_DIGITS for k in keys)
    assert breached_passwords.breached_key(LISTED) in keys


# --- the generator and the lookup agree ----------------------------------


def test_the_lookup_uses_the_generators_key_function(monkeypatch, no_hibp):
    """check_pwned_password must ask breached_passwords.breached_key, the
    function build() writes with, not a copy of it."""
    asked = []
    real = breached_passwords.breached_key
    monkeypatch.setattr(breached_passwords, "breached_key",
                        lambda p: asked.append(p) or real(p))
    auth_module.check_pwned_password(NOT_LISTED)
    assert asked == [NOT_LISTED]


def test_what_the_generator_writes_the_lookup_refuses(no_hibp, monkeypatch, tmp_path):
    """End to end over a source of our own: mixed case in the source, a
    third case typed, CRLF line endings, a line under the policy's
    minimum."""
    raw = b"CorrectHorseBatteryStapleXYZ\r\nShortOne\r\n"
    built = breached_passwords.build(raw, hashlib.sha256(raw).hexdigest())
    listed = tmp_path / "breached_passwords.txt"
    listed.write_bytes(built)
    monkeypatch.setattr(auth_module, "COMMON_PASSWORDS_PATH", str(listed))
    _, keys = breached_passwords.read(str(listed))
    assert len(keys) == 1, "fixture must drop the short line or this test proves less"
    assert auth_module.check_pwned_password("cORRECThORSEbATTERYsTAPLExyz") is True
    assert auth_module.check_pwned_password("CorrectHorseBatteryStapleXYQ") is False


def test_the_generator_refuses_a_source_that_is_not_the_pinned_one():
    with pytest.raises(SystemExit, match="refusing"):
        breached_passwords.build(b"anything\n")


# --- a list that checks nothing says so ----------------------------------


def _write(tmp_path, body: str) -> str:
    p = tmp_path / "breached_passwords.txt"
    p.write_text(body, encoding="ascii")
    return str(p)


def test_a_missing_list_is_a_problem_while_the_check_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(auth_module, "COMMON_PASSWORDS_PATH", str(tmp_path / "absent.txt"))
    assert get_settings().pwned_passwords_check, "fixture must run with the check on"
    problem = auth_module.common_password_list_problem()
    assert problem is not None and problem.startswith("missing:")


def test_a_list_with_no_keys_is_a_problem(monkeypatch, tmp_path):
    empty = _write(tmp_path, "# key_version: 1\n")
    monkeypatch.setattr(auth_module, "COMMON_PASSWORDS_PATH", empty)
    assert auth_module.common_password_list_problem().startswith("empty:")


def test_a_list_from_another_key_version_is_a_problem(monkeypatch, tmp_path, no_hibp):
    key = breached_passwords.breached_key(LISTED)
    monkeypatch.setattr(auth_module, "COMMON_PASSWORDS_PATH",
                        _write(tmp_path, f"# key_version: 0\n{key}\n"))
    assert auth_module.common_password_list_problem().startswith("stale:")
    assert auth_module.check_pwned_password(LISTED) is False, (
        "a stale list is not read: its keys would mean something else"
    )


def test_loading_a_missing_list_logs_an_error(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(auth_module, "COMMON_PASSWORDS_PATH", str(tmp_path / "absent.txt"))
    # The suite's in-process alembic run (fileConfig) disables loggers that
    # already exist; production runs alembic in its own process.
    monkeypatch.setattr(logging.getLogger("app.auth"), "disabled", False)
    with caplog.at_level(logging.ERROR, logger="app.auth"):
        assert auth_module._load_common_passwords() == frozenset()
    assert any("breached-password list missing" in r.getMessage() for r in caplog.records)


# --- through the endpoints -----------------------------------------------


@pytest.mark.integration
def test_change_password_refuses_a_listed_password_and_accepts_a_near_miss(db_session, no_hibp):
    from app.auth import hash_password
    from app.main import app
    from tests.test_account_self_service import _client_as, _seed_local_user, _seed_org

    current = "correct-horse-battery-staple-and-then-some"
    org = _seed_org(db_session)
    user = _seed_local_user(db_session, org_id=org.id, password_hash=hash_password(current))
    try:
        client = _client_as(db_session, user)
        r = client.post("/auth/change-password",
                        json={"current_password": current, "new_password": LISTED})
        assert r.status_code == 422, r.text
        assert "known data breach" in r.json()["detail"]

        r = client.post("/auth/change-password",
                        json={"current_password": current, "new_password": NOT_LISTED})
        assert r.status_code == 200, r.text
    finally:
        app.dependency_overrides.clear()
    assert no_hibp, "fixture must have refused an HIBP lookup or this test proves nothing"


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


@pytest.mark.integration
def test_health_reports_the_bundled_list_ok(db_session):
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app
    from tests.conftest import _app_session

    app.dependency_overrides[get_session] = _app_session(db_session)
    try:
        body = TestClient(app).get("/health").json()
    finally:
        app.dependency_overrides.clear()
    assert body["breached_password_list"] == "ok", body
    assert "breached_password_list" not in [w["key"] for w in body["warnings"]]
