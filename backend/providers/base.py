"""Shared provider request/response types."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class LLMResponse:
    text: str
    usage: Usage
    model: str


@dataclass
class EmbeddingResponse:
    embedding: list[float]
    usage: Usage
    model: str


class ProviderError(RuntimeError):
    pass


CancelCheck = Callable[[], bool]
