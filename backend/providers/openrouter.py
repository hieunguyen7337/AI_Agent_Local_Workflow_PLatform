"""OpenRouter chat completions adapter."""
from __future__ import annotations

import os
import time

import httpx

from .base import LLMResponse, ProviderError, Usage

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(ProviderError):
    pass


def call_openrouter(
    *,
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_s: float = 60.0,
    max_retries: int = 3,
    api_key: str | None = None,
) -> LLMResponse:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is not set")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout_s) as client:
                r = client.post(OPENROUTER_URL, json=payload, headers=headers)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                raise OpenRouterError(f"transient {r.status_code}: {r.text[:200]}")
            if r.status_code != 200:
                raise OpenRouterError(f"openrouter {r.status_code}: {r.text[:500]}")
            data = r.json()
            text = data["choices"][0]["message"]["content"]
            usage_raw = data.get("usage", {}) or {}
            usage = Usage(
                input_tokens=int(usage_raw.get("prompt_tokens", 0)),
                output_tokens=int(usage_raw.get("completion_tokens", 0)),
            )
            return LLMResponse(text=text, usage=usage, model=model)
        except (httpx.HTTPError, OpenRouterError) as e:
            last_err = e
            if attempt == max_retries - 1:
                break
            time.sleep(min(2**attempt, 8))

    raise OpenRouterError(f"failed after {max_retries} attempts: {last_err}")
