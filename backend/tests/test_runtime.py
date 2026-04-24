"""Runtime integration tests with mocked providers."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from backend.builder.api import (
    END,
    ApprovalNodeConfig,
    GateNodeConfig,
    GraphBuilder,
    LLMNodeConfig,
    TesterNodeConfig,
)
from backend.providers import openai as oai
from backend.providers import openrouter as orouter
from backend.providers.base import LLMResponse, Usage
from backend.runtime.cancellation import CancellationController
from backend.runtime.executor import run_graph


class _Replies:
    """Deterministic scripted replies for OpenRouter calls, keyed by call order."""

    def __init__(self, replies: list[LLMResponse]):
        self._replies = list(replies)

    def __call__(self, **kwargs) -> LLMResponse:
        if not self._replies:
            return LLMResponse(text="", usage=Usage(0, 0), model=kwargs.get("model", "x"))
        return self._replies.pop(0)


@pytest.fixture
def tmp_runs_root(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    root.mkdir()
    return root


def _build(max_iter: int = 3) -> GraphBuilder:
    b = GraphBuilder(name="ct_test", cost_budget_usd=1.0, latency_budget_ms=60_000)
    b.add_node(
        LLMNodeConfig(
            id="coder",
            model="minimax/minimax-m2.7",
            system_prompt="code",
            user_prompt_template="{user_input}",
            output_state_key="coder_output",
        )
    )
    b.add_node(TesterNodeConfig(id="tester", model="minimax/minimax-m2.7"))
    b.add_node(GateNodeConfig(id="gate", pass_target=END, fail_target="coder"))
    b.set_entry("coder")
    b.add_edge("coder", "tester")
    b.add_edge("tester", "gate")
    b.add_loop("gate", "coder", max_iterations=max_iter)
    return b


def test_happy_path_passes_gate(monkeypatch, tmp_runs_root):
    replies = [
        LLMResponse(text="def f(): pass", usage=Usage(5, 3), model="m"),  # coder
        LLMResponse(text="PASS\ngood", usage=Usage(4, 2), model="m"),  # tester
    ]
    monkeypatch.setattr(orouter, "stream_openrouter", _Replies(replies))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    metadata = _build().compile()
    result = run_graph(
        metadata, user_input="write f", expected="a function f", runs_root=tmp_runs_root
    )
    assert result.status == "ok"
    assert result.final_state.get("tester_verdict") is True
    assert result.final_state.get("tester_mode") == "llm_judge"


def test_loop_then_pass(monkeypatch, tmp_runs_root):
    replies = [
        LLMResponse(text="attempt 1", usage=Usage(5, 3), model="m"),  # coder
        LLMResponse(text="FAIL\ntry again", usage=Usage(4, 2), model="m"),  # tester -> fail
        LLMResponse(text="attempt 2", usage=Usage(5, 3), model="m"),  # coder again
        LLMResponse(text="PASS\ngood", usage=Usage(4, 2), model="m"),  # tester -> pass
    ]
    monkeypatch.setattr(orouter, "stream_openrouter", _Replies(replies))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    metadata = _build().compile()
    result = run_graph(
        metadata, user_input="do x", expected="result", runs_root=tmp_runs_root
    )
    assert result.status == "ok"
    assert result.final_state.get("tester_verdict") is True


def test_max_iterations_halts_cleanly(monkeypatch, tmp_runs_root):
    always_fail = [
        LLMResponse(text="c", usage=Usage(1, 1), model="m"),
        LLMResponse(text="FAIL", usage=Usage(1, 1), model="m"),
    ] * 10
    monkeypatch.setattr(orouter, "stream_openrouter", _Replies(always_fail))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    metadata = _build(max_iter=1).compile()
    result = run_graph(
        metadata, user_input="do x", expected="result", runs_root=tmp_runs_root
    )
    assert result.status == "MaxIterationsError"


def test_budget_exceeded_halts(monkeypatch, tmp_runs_root):
    # Each coder/tester call reports huge token counts -> exceeds a tiny budget.
    replies = [
        LLMResponse(text="c", usage=Usage(1_000_000, 1_000_000), model="m"),
        LLMResponse(text="FAIL", usage=Usage(1_000_000, 1_000_000), model="m"),
    ] * 10
    monkeypatch.setattr(orouter, "stream_openrouter", _Replies(replies))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    b = GraphBuilder(name="tiny", cost_budget_usd=0.0001, latency_budget_ms=60_000)
    b.add_node(
        LLMNodeConfig(
            id="coder",
            model="minimax/minimax-m2.7",
            system_prompt="s",
            user_prompt_template="{user_input}",
            output_state_key="coder_output",
        )
    )
    b.add_node(TesterNodeConfig(id="tester", model="minimax/minimax-m2.7"))
    b.add_node(GateNodeConfig(id="gate", pass_target=END, fail_target="coder"))
    b.set_entry("coder")
    b.add_edge("coder", "tester")
    b.add_edge("tester", "gate")
    b.add_loop("gate", "coder", max_iterations=3)

    metadata = b.compile()
    result = run_graph(
        metadata, user_input="x", expected="y", runs_root=tmp_runs_root
    )
    assert result.status == "budget_exceeded"


def test_sandbox_mode_with_test_code_skips_llm_tester(monkeypatch, tmp_runs_root):
    calls: list[int] = []

    def _reply(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return LLMResponse(
                text="def add(a, b):\n    return a + b\n",
                usage=Usage(1, 1),
                model="m",
            )
        raise AssertionError("tester should not call OpenRouter in sandbox mode")

    monkeypatch.setattr(orouter, "stream_openrouter", _reply)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    metadata = _build().compile()
    result = run_graph(
        metadata,
        user_input="write add",
        expected="function add",
        runs_root=tmp_runs_root,
        initial_state_overrides={"_test_code": "assert add(1, 2) == 3"},
    )
    assert result.status == "ok"
    assert result.final_state.get("tester_verdict") is True
    assert result.final_state.get("tester_mode") == "sandbox"
    assert len(calls) == 1


def test_openai_provider_runtime_sets_genai_system(monkeypatch, tmp_runs_root):
    calls: list[int] = []

    def _reply(**kwargs):
        calls.append(1)
        return LLMResponse(text="openai result", usage=Usage(2, 3), model="gpt-4o-mini")

    monkeypatch.setattr(oai, "stream_openai", _reply)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    b = GraphBuilder(name="openai_graph", cost_budget_usd=1.0, latency_budget_ms=60_000)
    b.add_node(
        LLMNodeConfig(
            id="answer",
            provider="openai",
            model="gpt-4o-mini",
            system_prompt="answer",
            user_prompt_template="{user_input}",
            output_state_key="final_answer",
        )
    )
    b.set_entry("answer")
    b.add_edge("answer", END)

    result = run_graph(b.compile(), user_input="hello", runs_root=tmp_runs_root)
    assert result.status == "ok"
    assert result.final_state.get("final_answer") == "openai result"
    assert len(calls) == 1

    with sqlite3.connect(result.run_dir / "telemetry.db") as con:
        attrs_json = con.execute(
            "SELECT attributes_json FROM spans WHERE run_id=? AND node_id=?",
            (result.run_id, "answer"),
        ).fetchone()[0]
    attrs = json.loads(attrs_json)
    assert attrs["gen_ai.system"] == "openai"


def test_cancellation_during_llm_generation_marks_run_cancelled(monkeypatch, tmp_runs_root):
    def _stream_reply(**kwargs):
        cancel_check = kwargs["cancel_check"]
        while not cancel_check():
            time.sleep(0.01)
        raise AssertionError("cancel_check should have interrupted before returning")

    monkeypatch.setattr(orouter, "stream_openrouter", _stream_reply)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    controller = CancellationController()
    timer = threading.Timer(0.05, controller.cancel)
    timer.start()
    try:
        result = run_graph(
            _build().compile(),
            user_input="write f",
            expected="a function",
            runs_root=tmp_runs_root,
            cancellation=controller,
        )
    finally:
        timer.cancel()
    assert result.status == "cancelled"
    assert result.error == "user_cancelled"
    assert result.final_state.get("coder_output", "") == ""


def test_approval_node_halts_with_pending_artifact(tmp_runs_root):
    b = GraphBuilder(name="approval_test", cost_budget_usd=1.0, latency_budget_ms=60_000)
    b.add_node(
        ApprovalNodeConfig(
            id="review",
            prompt="Review before continuing.",
            approved_target=END,
            rejected_target=END,
        )
    )
    b.set_entry("review")

    result = run_graph(b.compile(), user_input="needs review", runs_root=tmp_runs_root)

    assert result.status == "pending_approval"
    assert result.error is None
    approval_path = result.run_dir / "approval.json"
    assert approval_path.exists()
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["workflow"] == "approval_test"
    assert approval["run_id"] == result.run_id
    assert approval["node_id"] == "review"
    assert approval["prompt"] == "Review before continuing."
    assert approval["state_snapshot"]["user_input"] == "needs review"
    assert result.final_state["pending_approval"]["node_id"] == "review"
