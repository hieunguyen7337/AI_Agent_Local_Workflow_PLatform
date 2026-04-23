"""OpenAI direct adapter: retry + token accounting via a mocked httpx client."""
from __future__ import annotations

import pytest

from backend.providers import openai as oai
from backend.runtime.errors import CancelledError


class _MockResponse:
    def __init__(self, status_code: int, json_body: dict, text_body: str = ""):
        self.status_code = status_code
        self._json = json_body
        self.text = text_body or str(json_body)

    def json(self) -> dict:
        return self._json


class _MockClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        if not self._responses:
            raise AssertionError("no more mock responses")
        return self._responses.pop(0)

    def stream(self, *args, **kwargs):
        if not self._responses:
            raise AssertionError("no more mock responses")
        return self._responses.pop(0)


class _StreamResponse:
    def __init__(self, status_code: int, lines: list[str], text_body: str = ""):
        self.status_code = status_code
        self._lines = list(lines)
        self.text = text_body
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False

    def iter_lines(self):
        for line in self._lines:
            yield line

    def close(self):
        self.closed = True


def test_success_parses_usage(monkeypatch):
    good = _MockResponse(
        200,
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 8, "completion_tokens": 5},
        },
    )
    monkeypatch.setattr(oai.httpx, "Client", lambda *a, **k: _MockClient([good]))
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    r = oai.call_openai(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert r.text == "ok"
    assert r.usage.input_tokens == 8
    assert r.usage.output_tokens == 5


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(oai.OpenAIError):
        oai.call_openai(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])


def test_retry_then_success(monkeypatch):
    flaky = _MockResponse(500, {"error": "transient"}, "transient")
    good = _MockResponse(
        200,
        {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )
    shared = _MockClient([flaky, good])
    monkeypatch.setattr(oai.httpx, "Client", lambda *a, **k: shared)
    monkeypatch.setattr(oai.time, "sleep", lambda *a, **k: None)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    r = oai.call_openai(
        model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}], max_retries=2
    )
    assert r.text == "ok"


def test_hard_failure_raises_after_retries(monkeypatch):
    bads = [_MockResponse(500, {"error": "x"}, "x") for _ in range(5)]
    shared = _MockClient(bads)
    monkeypatch.setattr(oai.httpx, "Client", lambda *a, **k: shared)
    monkeypatch.setattr(oai.time, "sleep", lambda *a, **k: None)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    with pytest.raises(oai.OpenAIError):
        oai.call_openai(
            model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}], max_retries=3
        )


def test_stream_success_parses_text_and_usage(monkeypatch):
    stream = _StreamResponse(
        200,
        [
            'data: {"choices":[{"delta":{"content":"hi"}}]}',
            'data: {"choices":[{"delta":{"content":"!"}}],"usage":{"prompt_tokens":3,"completion_tokens":1}}',
            "data: [DONE]",
        ],
    )
    monkeypatch.setattr(oai.httpx, "Client", lambda *a, **k: _MockClient([stream]))
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    r = oai.stream_openai(model="gpt-4o-mini", messages=[{"role": "user", "content": "x"}])
    assert r.text == "hi!"
    assert r.usage.input_tokens == 3
    assert r.usage.output_tokens == 1


def test_stream_cancel_raises(monkeypatch):
    stream = _StreamResponse(
        200,
        ['data: {"choices":[{"delta":{"content":"hi"}}]}', "data: [DONE]"],
    )
    monkeypatch.setattr(oai.httpx, "Client", lambda *a, **k: _MockClient([stream]))
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    with pytest.raises(CancelledError):
        oai.stream_openai(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "x"}],
            cancel_check=lambda: True,
        )
    assert stream.closed is True
