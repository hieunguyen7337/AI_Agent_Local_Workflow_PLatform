"""Checkpoint replay with modified node config."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.checkpointing.replay import apply_overrides, parse_set_arg
from backend.providers import openrouter as orouter
from backend.providers.openrouter import LLMResponse, Usage
from backend.runtime.executor import run_graph
from backend.workflows import coder_tester


class _Replies:
    def __init__(self, replies):
        self._replies = list(replies)

    def __call__(self, **kwargs) -> LLMResponse:
        if not self._replies:
            return LLMResponse(text="PASS", usage=Usage(1, 1), model="m")
        return self._replies.pop(0)


def test_parse_set_arg():
    out = parse_set_arg(["coder.temperature=0.5", "tester.max_retries=1", "coder.model=foo"])
    assert out == {
        "coder": {"temperature": 0.5, "model": "foo"},
        "tester": {"max_retries": 1},
    }


def test_parse_set_arg_coerces_types():
    out = parse_set_arg(["a.b=true", "a.c=42", "a.d=3.14", "a.e=hello"])
    assert out == {"a": {"b": True, "c": 42, "d": 3.14, "e": "hello"}}


def test_apply_overrides_updates_config():
    b = coder_tester.build()
    original_prompt = b._nodes["coder"].user_prompt_template
    apply_overrides(b, {"coder": {"user_prompt_template": "NEW"}})
    assert b._nodes["coder"].user_prompt_template == "NEW"
    assert b._nodes["coder"].user_prompt_template != original_prompt


def test_replay_reuses_run_dir(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()

    # First run
    replies = [
        LLMResponse(text="plan", usage=Usage(1, 1), model="m"),  # planner
        LLMResponse(text="code v1", usage=Usage(1, 1), model="m"),  # coder
        LLMResponse(text="PASS", usage=Usage(1, 1), model="m"),  # tester
    ]
    monkeypatch.setattr(orouter, "call_openrouter", _Replies(replies))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    metadata = coder_tester.build_compiled()
    r1 = run_graph(
        metadata, user_input="write f", expected="a function", runs_root=runs_root
    )
    assert r1.status == "ok"
    assert (runs_root / r1.run_id / "checkpoints.db").exists()

    # Replay with overrides — reuse same run_id / run_dir.
    from backend.checkpointing.replay import replay as do_replay

    replies2 = [
        LLMResponse(text="plan2", usage=Usage(1, 1), model="m"),
        LLMResponse(text="code v2", usage=Usage(1, 1), model="m"),
        LLMResponse(text="PASS", usage=Usage(1, 1), model="m"),
    ]
    monkeypatch.setattr(orouter, "call_openrouter", _Replies(replies2))
    r2 = do_replay(
        workflow="coder_tester",
        run_id=r1.run_id,
        user_input="write f",
        expected="a function",
        overrides={"coder": {"user_prompt_template": "alt"}},
        runs_root=runs_root,
    )
    assert r2.run_id == r1.run_id
    assert r2.run_dir == r1.run_dir
