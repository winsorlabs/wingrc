"""The sign-in state cookies' HMAC key: no default, refused when weak.

Until 2026-11-17 `auth_flow_secret` defaulted to a literal in this public
repo, and docker-compose.yml never passed WINGRC_AUTH_FLOW_SECRET, so every
compose deployment signed wingrc_auth_flow, wingrc_mfa_pending and
wingrc_mfa_setup with a key anyone could read (docs/roadmap.md, "The auth
state cookies were signed with a public key"). These tests hold the fix:
no state cookie is made or accepted under a missing, short or public key.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from pathlib import Path

import pytest

from app import auth
from app.config import Settings, get_settings

STRONG = "a-deployment-key-that-is-long-enough-0123456789"


@pytest.fixture
def key(monkeypatch):
    def set_key(value):
        if value is None:
            monkeypatch.delenv("WINGRC_AUTH_FLOW_SECRET", raising=False)
        else:
            monkeypatch.setenv("WINGRC_AUTH_FLOW_SECRET", value)
        get_settings.cache_clear()

    yield set_key
    get_settings.cache_clear()


def _signed_with(secret: str, payload: dict) -> str:
    """A cookie made the way sign_state_cookie makes one, under any key: what
    someone holding the old public key could forge."""
    msg = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()
    return f"{base64.urlsafe_b64encode(msg).rstrip(b'=').decode()}.{sig}"


def test_the_setting_has_no_default():
    assert Settings.model_fields["auth_flow_secret"].default is None


@pytest.mark.parametrize(
    ("value", "says"),
    [
        (None, "is not set"),
        ("", "is not set"),
        ("dev-auth-flow-secret-change-in-production", "is the public default"),
        ("x" * 31, "shorter than 32"),
    ],
)
def test_signing_refuses_and_names_the_variable(key, value, says):
    key(value)
    with pytest.raises(auth.AuthFlowSecretError) as err:
        auth.sign_state_cookie(auth.make_state_payload({"flow": "x"}))
    assert "WINGRC_AUTH_FLOW_SECRET" in str(err.value)
    assert says in str(err.value)


def test_a_cookie_forged_with_the_public_key_is_refused(key):
    """The attack: a pending-MFA cookie made with the repo's old key. Under a
    real key it fails the signature; under the old key it is refused before
    the signature is even compared."""
    forged = _signed_with(
        "dev-auth-flow-secret-change-in-production",
        {"phase": "enroll", "user_id": "u", "org_id": "o", "exp": int(time.time()) + 300},
    )
    key(STRONG)
    assert auth.verify_state_cookie(forged) is None
    key("dev-auth-flow-secret-change-in-production")
    assert auth.verify_state_cookie(forged) is None


@pytest.mark.parametrize("value", [None, "", "x" * 31])
def test_verifying_under_a_weak_key_accepts_nothing(key, value):
    key(value)
    cookie = _signed_with(value or "", {"flow": "x", "exp": int(time.time()) + 300})
    assert auth.verify_state_cookie(cookie) is None


def test_a_real_key_round_trips(key):
    """The precondition for the refusals above meaning anything."""
    key(STRONG)
    payload = auth.make_state_payload({"flow": "x"})
    assert auth.verify_state_cookie(auth.sign_state_cookie(payload)) == payload, (
        "fixture must sign and verify under a strong key, or the refusals prove nothing"
    )


def test_exactly_the_minimum_length_is_accepted(key):
    key("y" * 32)
    assert auth.verify_state_cookie(auth.sign_state_cookie(auth.make_state_payload({})))


def test_compose_requires_it_with_no_default():
    """The key reaches the container only through docker-compose.yml. A
    `:-` default there would be the original defect again."""
    import yaml

    compose = Path(__file__).resolve().parents[2] / "docker-compose.yml"
    if not compose.exists():
        pytest.skip("docker-compose.yml is outside this tree (the backend image); CI runs this")
    env = yaml.safe_load(compose.read_text(encoding="utf-8"))["x-backend-env"]
    value = env.get("WINGRC_AUTH_FLOW_SECRET")
    assert value is not None, "the backend never sees WINGRC_AUTH_FLOW_SECRET"
    required = r"\$\{WINGRC_AUTH_FLOW_SECRET:\?WINGRC_AUTH_FLOW_SECRET is required[^}]*\}"
    assert re.fullmatch(required, value), value
