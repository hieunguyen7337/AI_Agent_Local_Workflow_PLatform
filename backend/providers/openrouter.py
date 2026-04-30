"""OpenRouter chat completions adapter."""
from __future__ import annotations

import json
import os
import time

import httpx

from backend.runtime.errors import CancelledError

from .base import CancelCheck, EmbeddingResponse, LLMResponse, ProviderError, Usage

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_EMBEDDINGS_URL = "https://openrouter.ai/api/v1/embeddings"


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
            if "choices" not in data:
                raise OpenRouterError(f"openrouter malformed response: {json.dumps(data)[:500]}")
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


def stream_openrouter(
    *,
    model: str,
    messages: list[dict],
    temperature: float = 0.2,
    max_tokens: int | None = None,
    timeout_s: float = 60.0,
    max_retries: int = 3,
    api_key: str | None = None,
    cancel_check: CancelCheck | None = None,
) -> LLMResponse:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is not set")

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            return _stream_request(
                OPENROUTER_URL,
                payload=payload,
                headers=headers,
                timeout_s=timeout_s,
                model=model,
                cancel_check=cancel_check,
            )
        except CancelledError:
            raise
        except (httpx.HTTPError, OpenRouterError) as e:
            last_err = e
            if attempt == max_retries - 1:
                break
            time.sleep(min(2**attempt, 8))

    raise OpenRouterError(f"failed after {max_retries} attempts: {last_err}")


def call_openrouter_embedding(
    *,
    model: str,
    input_payload,
    dimensions: int | None = None,
    timeout_s: float = 60.0,
    max_retries: int = 3,
    api_key: str | None = None,
) -> EmbeddingResponse:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise OpenRouterError("OPENROUTER_API_KEY is not set")

    payload = {
        "model": model,
        "input": input_payload,
        "encoding_format": "float",
    }
    if dimensions is not None:
        payload["dimensions"] = dimensions

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=timeout_s) as client:
                r = client.post(OPENROUTER_EMBEDDINGS_URL, json=payload, headers=headers)
            if r.status_code == 429 or 500 <= r.status_code < 600:
                raise OpenRouterError(f"transient {r.status_code}: {r.text[:200]}")
            if r.status_code != 200:
                raise OpenRouterError(f"openrouter embeddings {r.status_code}: {r.text[:500]}")
            data = r.json()
            items = data.get("data") or []
            if not items or "embedding" not in items[0]:
                raise OpenRouterError(f"openrouter embeddings malformed response: {json.dumps(data)[:500]}")
            usage_raw = data.get("usage", {}) or {}
            usage = Usage(
                input_tokens=int(usage_raw.get("prompt_tokens", usage_raw.get("total_tokens", 0))),
                output_tokens=0,
            )
            return EmbeddingResponse(
                embedding=[float(x) for x in items[0]["embedding"]],
                usage=usage,
                model=model,
            )
        except (httpx.HTTPError, OpenRouterError) as e:
            last_err = e
            if attempt == max_retries - 1:
                break
            time.sleep(min(2**attempt, 8))

    raise OpenRouterError(f"embeddings failed after {max_retries} attempts: {last_err}")


def _stream_request(
    url: str,
    *,
    payload: dict,
    headers: dict[str, str],
    timeout_s: float,
    model: str,
    cancel_check: CancelCheck | None,
) -> LLMResponse:
    parts: list[str] = []
    usage = Usage(0, 0)
    with httpx.Client(timeout=timeout_s) as client:
        with client.stream("POST", url, json=payload, headers=headers) as r:
            if r.status_code == 429 or 500 <= r.status_code < 600:
                raise OpenRouterError(f"transient {r.status_code}: {r.text[:200]}")
            if r.status_code != 200:
                raise OpenRouterError(f"openrouter {r.status_code}: {r.text[:500]}")

            for raw_line in r.iter_lines():
                if cancel_check and cancel_check():
                    r.close()
                    raise CancelledError("user_cancelled")
                line = raw_line.strip() if isinstance(raw_line, str) else ""
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                usage_raw = payload.get("usage") or {}
                if usage_raw:
                    usage = Usage(
                        input_tokens=int(usage_raw.get("prompt_tokens", 0)),
                        output_tokens=int(usage_raw.get("completion_tokens", 0)),
                    )
                delta = ""
                choices = payload.get("choices") or []
                if choices:
                    choice0 = choices[0] or {}
                    delta_obj = choice0.get("delta") or {}
                    message_obj = choice0.get("message") or {}
                    delta = (
                        delta_obj.get("content")
                        or message_obj.get("content")
                        or ""
                    )
                if delta:
                    parts.append(str(delta))
    return LLMResponse(text="".join(parts), usage=usage, model=model)
