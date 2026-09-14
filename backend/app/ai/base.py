"""Abstract AI provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class AIProvider(ABC):
    @abstractmethod
    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        """Send a prompt pair and return the text response."""
        ...

    @property
    def identity(self) -> str:
        """A "provider:model" string for provenance records (e.g.

        Product.ai_generated_model) so callers never need to hardcode a
        provider-type check to build one themselves.
        """
        return self.__class__.__name__
