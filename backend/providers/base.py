"""Shared provider request/response types."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass
class LLMResponse:
    text: str
    usage: Usage
    model: str


class ProviderError(RuntimeError):
    pass
