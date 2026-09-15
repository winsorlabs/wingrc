"""Anthropic API provider -- the AIProvider interface's Anthropic
implementation.

api_key/model are resolved from the encrypted "ai" connector credential
by ai/__init__.py:get_ai_provider() and passed in here explicitly
(2026-09-19) -- this class no longer reads ANTHROPIC_API_KEY from the
environment at all; see connectors/ai.py's module docstring for the full
reasoning behind that move.

The actual Anthropic API call, and its error categorization (auth/rate-
limit/network/model-not-found), lives in connectors/ai.py:run_completion()
-- the same code path Administration's "Test connection" button
exercises, so the two can never report a given failure differently.
"""

from __future__ import annotations

from ..connectors.ai import AIConnectionError, AISettings, run_completion
from .base import AIProvider

_DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicProvider(AIProvider):
    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL) -> None:
        self._api_key = api_key
        self._model = model

    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        settings = AISettings(provider="anthropic", model=self._model, api_key=self._api_key)
        try:
            return run_completion(settings, system, user, max_tokens)
        except AIConnectionError as e:
            # Normalized to RuntimeError -- importers/document.py's
            # existing `except RuntimeError` -> DocumentIngestError
            # contract (the same degradation ai/none_.py's NullProvider
            # has always relied on) depends on every provider failure
            # surfacing as RuntimeError, not a connector-specific type.
            raise RuntimeError(str(e)) from e

    @property
    def identity(self) -> str:
        return f"anthropic:{self._model}"
