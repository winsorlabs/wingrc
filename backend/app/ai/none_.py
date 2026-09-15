"""Null AI provider — raises on use, keeping the default config safe."""

from __future__ import annotations

from .base import AIProvider


class NullProvider(AIProvider):
    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        raise RuntimeError(
            "No AI provider configured. Configure one in Administration -> AI Provider. "
            "(Azure OpenAI / local-model support is planned but not implemented yet.)"
        )

    @property
    def identity(self) -> str:
        return "none"
