"""Provider registry and shared public types."""
from __future__ import annotations

from . import openai, openrouter
from .base import CancelCheck, EmbeddingResponse, LLMResponse, ProviderError, Usage

_PROVIDER_CALLS = {
    "openrouter": lambda **kwargs: openrouter.call_openrouter(**kwargs),
    "openai": lambda **kwargs: openai.call_openai(**kwargs),
}
_PROVIDER_STREAM_CALLS = {
    "openrouter": lambda **kwargs: openrouter.stream_openrouter(**kwargs),
    "openai": lambda **kwargs: openai.stream_openai(**kwargs),
}
_PROVIDER_EMBEDDING_CALLS = {
    "openrouter": lambda **kwargs: openrouter.call_openrouter_embedding(**kwargs),
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


def stream_provider(
    provider: str,
    *,
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_s: float = 60.0,
    max_retries: int = 3,
    cancel_check: CancelCheck | None = None,
) -> LLMResponse:
    if provider not in _PROVIDER_STREAM_CALLS:
        raise ProviderError(f"Unknown provider {provider!r}")
    return _PROVIDER_STREAM_CALLS[provider](
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        max_retries=max_retries,
        cancel_check=cancel_check,
    )


def call_embedding_provider(
    provider: str,
    *,
    model: str,
    input_payload,
    dimensions: int | None = None,
    timeout_s: float = 60.0,
    max_retries: int = 3,
) -> EmbeddingResponse:
    if provider not in _PROVIDER_EMBEDDING_CALLS:
        raise ProviderError(f"Unknown embedding provider {provider!r}")
    return _PROVIDER_EMBEDDING_CALLS[provider](
        model=model,
        input_payload=input_payload,
        dimensions=dimensions,
        timeout_s=timeout_s,
        max_retries=max_retries,
    )


__all__ = [
    "LLMResponse",
    "EmbeddingResponse",
    "Usage",
    "ProviderError",
    "call_provider",
    "call_embedding_provider",
    "stream_provider",
    "openai",
    "openrouter",
]
