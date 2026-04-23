"""Provider registry and shared public types."""
from __future__ import annotations

from . import openai, openrouter
from .base import LLMResponse, ProviderError, Usage

_PROVIDER_CALLS = {
    "openrouter": lambda **kwargs: openrouter.call_openrouter(**kwargs),
    "openai": lambda **kwargs: openai.call_openai(**kwargs),
}


def call_provider(
    provider: str,
    *,
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_s: float = 60.0,
    max_retries: int = 3,
) -> LLMResponse:
    if provider not in _PROVIDER_CALLS:
        raise ProviderError(f"Unknown provider {provider!r}")
    return _PROVIDER_CALLS[provider](
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )


__all__ = ["LLMResponse", "Usage", "ProviderError", "call_provider", "openai", "openrouter"]
