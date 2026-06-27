"""AI provider factory."""

from __future__ import annotations

from .base import AIProvider


def get_ai_provider(ai_provider: str = "none") -> AIProvider:
    """Return the configured AIProvider instance.

    Args:
        ai_provider: Value from Settings.ai_provider — "none" | "anthropic".
    """
    if ai_provider == "none":
        from .none_ import NullProvider

        return NullProvider()
    if ai_provider == "anthropic":
        from .anthropic_ import AnthropicProvider

        return AnthropicProvider()
    raise ValueError(
        f"Unknown AI provider: {ai_provider!r}. Supported: none, anthropic"
    )
