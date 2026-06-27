"""Anthropic API provider.

Reads ANTHROPIC_API_KEY from the environment (standard SDK behaviour).
The model defaults to claude-sonnet-4-6; override via the model argument
for tenants who want a cheaper or more capable tier.
"""

from __future__ import annotations

from .base import AIProvider

_DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicProvider(AIProvider):
    def __init__(self, model: str = _DEFAULT_MODEL) -> None:
        try:
            import anthropic as _anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package not installed. "
                "Run: pip install 'wingrc-backend[ai]'"
            ) from exc
        self._client = _anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        self._model = model

    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        msg = self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text
