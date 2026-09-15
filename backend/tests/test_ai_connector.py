"""Tests for connectors/ai.py.

Unlike test_smtp_connector.py (a real local fake SMTP server -- protocol
negotiation genuinely needs one), there's no local fake Anthropic server
worth standing up here: the only thing to verify is that run_completion()
categorizes each of the real anthropic SDK's exception types into the
right AIConnectionError message, and that a successful call returns the
completion text. So the anthropic.Anthropic client itself is monkeypatched
to a fake that either raises a REAL anthropic exception instance (built
against the real httpx2.Request/Response the SDK's own exception classes
require -- not a stand-in class, so this stays honest to the real
exception hierarchy read from anthropic/_exceptions.py) or returns a
canned successful response shape.
"""

from __future__ import annotations

import anthropic
import httpx2
import pytest

from app.connectors import ai as ai_connector
from app.connectors.ai import (
    AIConfigError,
    AIConnectionError,
    AISettings,
    _test_connection,
    parse_settings,
    run_completion,
)

_REQUEST_URL = "https://api.anthropic.com/v1/messages"


def _status_error(cls, status_code: int, message: str, error_type: str = "error") -> Exception:
    """Builds a real instance of one of anthropic's APIStatusError subclasses
    -- these require a genuine httpx2.Request/Response pair to construct
    (status_code, request_id, type are all read off the response), so a
    bare `cls(message)` doesn't work."""
    request = httpx2.Request("POST", _REQUEST_URL)
    body = {"error": {"type": error_type, "message": message}}
    response = httpx2.Response(status_code, request=request, json=body)
    return cls(message, response=response, body=body)


def _connection_error(cls=anthropic.APIConnectionError, **kwargs) -> Exception:
    request = httpx2.Request("POST", _REQUEST_URL)
    if cls is anthropic.APITimeoutError:
        return cls(request=request)
    return cls(request=request, **kwargs)


class _FakeMessages:
    def __init__(self, *, raises: Exception | None = None, text: str = "OK"):
        self._raises = raises
        self._text = text

    def create(self, **kwargs):
        if self._raises is not None:
            raise self._raises
        block = type("Block", (), {"text": self._text})()
        return type("Message", (), {"content": [block]})()


class _FakeAnthropicClient:
    def __init__(self, *, raises: Exception | None = None, text: str = "OK", **_kwargs):
        self.messages = _FakeMessages(raises=raises, text=text)


def _patch_anthropic(monkeypatch, *, raises: Exception | None = None, text: str = "OK"):
    monkeypatch.setattr(
        anthropic,
        "Anthropic",
        lambda **kwargs: _FakeAnthropicClient(raises=raises, text=text, **kwargs),
    )


def _settings(**overrides) -> AISettings:
    defaults = {"provider": "anthropic", "model": "claude-sonnet-4-6", "api_key": "sk-ant-test"}
    defaults.update(overrides)
    return AISettings(**defaults)


# ---------------------------------------------------------------------------
# parse_settings
# ---------------------------------------------------------------------------


def test_parse_settings_defaults_provider_to_anthropic():
    settings = parse_settings({}, {"api_key": "sk-ant-test"})
    assert settings.provider == "anthropic"


def test_parse_settings_unsupported_provider_rejected():
    with pytest.raises(AIConfigError, match="not implemented"):
        parse_settings({"provider": "azure_openai"}, {"api_key": "sk-ant-test"})


def test_parse_settings_missing_api_key_rejected():
    with pytest.raises(AIConfigError, match="API key"):
        parse_settings({}, {})


def test_parse_settings_blank_model_defaults():
    settings = parse_settings({"model": "  "}, {"api_key": "sk-ant-test"})
    assert settings.model == ai_connector._DEFAULT_MODEL


def test_parse_settings_explicit_model_overrides_default():
    settings = parse_settings({"model": "claude-haiku-4-5"}, {"api_key": "sk-ant-test"})
    assert settings.model == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# run_completion -- success
# ---------------------------------------------------------------------------


def test_run_completion_returns_text(monkeypatch):
    _patch_anthropic(monkeypatch, text="Hello there.")
    result = run_completion(_settings(), "system", "user", 100)
    assert result == "Hello there."


# ---------------------------------------------------------------------------
# run_completion -- exception categorization
# ---------------------------------------------------------------------------


def test_run_completion_authentication_error(monkeypatch):
    _patch_anthropic(
        monkeypatch,
        raises=_status_error(
            anthropic.AuthenticationError, 401, "invalid x-api-key", "authentication_error"
        ),
    )
    with pytest.raises(AIConnectionError, match="401"):
        run_completion(_settings(), "system", "user", 100)


def test_run_completion_permission_denied_error(monkeypatch):
    _patch_anthropic(
        monkeypatch,
        raises=_status_error(
            anthropic.PermissionDeniedError, 403, "not permitted", "permission_error"
        ),
    )
    with pytest.raises(AIConnectionError, match="403"):
        run_completion(_settings(model="claude-opus-9"), "system", "user", 100)


def test_run_completion_rate_limit_error(monkeypatch):
    _patch_anthropic(
        monkeypatch,
        raises=_status_error(anthropic.RateLimitError, 429, "rate limited", "rate_limit_error"),
    )
    with pytest.raises(AIConnectionError, match="429"):
        run_completion(_settings(), "system", "user", 100)


def test_run_completion_not_found_error(monkeypatch):
    _patch_anthropic(
        monkeypatch,
        raises=_status_error(anthropic.NotFoundError, 404, "model not found", "not_found_error"),
    )
    with pytest.raises(AIConnectionError, match="404"):
        run_completion(_settings(model="does-not-exist"), "system", "user", 100)


def test_run_completion_timeout_error(monkeypatch):
    _patch_anthropic(monkeypatch, raises=_connection_error(anthropic.APITimeoutError))
    with pytest.raises(AIConnectionError, match="[Tt]imed out"):
        run_completion(_settings(), "system", "user", 100)


def test_run_completion_connection_error(monkeypatch):
    _patch_anthropic(monkeypatch, raises=_connection_error(message="Connection refused"))
    with pytest.raises(AIConnectionError, match="Could not reach Anthropic"):
        run_completion(_settings(), "system", "user", 100)


def test_run_completion_generic_status_error_is_catchall(monkeypatch):
    """Anything not named individually (e.g. an overload/5xx) still comes
    through as a specific status code + message, not a bare "failed"."""
    _patch_anthropic(
        monkeypatch,
        raises=_status_error(anthropic.APIStatusError, 529, "overloaded", "overloaded_error"),
    )
    with pytest.raises(AIConnectionError, match="529"):
        run_completion(_settings(), "system", "user", 100)


# ---------------------------------------------------------------------------
# _test_connection
# ---------------------------------------------------------------------------


def test_test_connection_ok(monkeypatch):
    _patch_anthropic(monkeypatch, text="OK")
    result = _test_connection({"provider": "anthropic"}, {"api_key": "sk-ant-test"})
    assert result.ok, result.message
    assert "claude-sonnet-4-6" in result.message


def test_test_connection_invalid_config_short_circuits_before_any_call(monkeypatch):
    """A missing API key must be rejected by parse_settings before
    anthropic.Anthropic is ever constructed -- patch it to blow up if
    reached, so this test fails loudly if that ordering regresses."""

    def _boom(**_kwargs):
        raise AssertionError("anthropic.Anthropic() should not have been constructed")

    monkeypatch.setattr(anthropic, "Anthropic", _boom)
    result = _test_connection({"provider": "anthropic"}, {})
    assert not result.ok
    assert "API key" in result.message


def test_test_connection_reports_specific_failure(monkeypatch):
    _patch_anthropic(
        monkeypatch,
        raises=_status_error(
            anthropic.AuthenticationError, 401, "invalid x-api-key", "authentication_error"
        ),
    )
    result = _test_connection({"provider": "anthropic"}, {"api_key": "sk-ant-bad"})
    assert not result.ok
    assert "401" in result.message
