from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Optional


class AIProviderError(RuntimeError):
    """Provider unreachable, timed out, or returned an unusable response."""


class AIValidationError(AIProviderError):
    """Output did not validate against the requested JSON Schema."""


@dataclass(frozen=True)
class ModelConfig:
    host: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5-coder:14b-instruct-q5_K_M"
    num_ctx: int = 8192
    temperature: float = 0.0
    timeout_s: float = 60.0
    max_retries: int = 1
    max_output_tokens: Optional[int] = None
    extra: dict = field(default_factory=dict)


class AIProvider(abc.ABC):
    """Minimal provider contract. Implementations must never execute code."""

    name: str = "abstract"

    @abc.abstractmethod
    def health(self) -> dict:
        """Return {'status': 'healthy'|'unavailable'|'error', ...} — never raises."""

    @abc.abstractmethod
    def chat(self, messages: list[dict], *, json_schema: Optional[dict] = None) -> dict:
        """Free-form chat. If json_schema is provided, validate the output."""

    @abc.abstractmethod
    def structured(self, prompt: str, schema: dict, *, system: Optional[str] = None) -> dict:
        """Return a dict validated against ``schema`` (JSON Schema draft-07 subset)."""
