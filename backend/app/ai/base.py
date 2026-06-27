"""Abstract AI provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod


class AIProvider(ABC):
    @abstractmethod
    def complete(self, system: str, user: str, *, max_tokens: int = 8192) -> str:
        """Send a prompt pair and return the text response."""
        ...
