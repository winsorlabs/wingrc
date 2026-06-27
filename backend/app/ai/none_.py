"""Null AI provider — raises on use, keeping the default config safe."""

from __future__ import annotations

from .base import AIProvider


class NullProvider(AIProvider):
    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        raise RuntimeError(
            "No AI provider configured. Set WINGRC_AI_PROVIDER to 'anthropic' "
            "and supply ANTHROPIC_API_KEY, or configure 'azure_openai' / 'local'."
        )
