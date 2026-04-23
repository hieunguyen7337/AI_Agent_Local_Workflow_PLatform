"""Eval harness: fixture loading + metrics aggregation + end-to-end small run."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.evals.fixtures import load_fixtures
from backend.evals.harness import run_eval
from backend.evals.metrics import confidence_intervals, summarize
from backend.providers import openrouter as orouter
from backend.providers.openrouter import LLMResponse, Usage


class _Replies:
    def __init__(self, replies):
        self._replies = list(replies)

    def __call__(self, **kwargs) -> LLMResponse:
        if not self._replies:
            return LLMResponse(text="PASS", usage=Usage(1, 1), model="m")
        return self._replies.pop(0)


def test_summarize_basic():
    results = [
        {"pass": True, "cost_usd": 0.01, "latency_ms": 100.0},
        {"pass": False, "cost_usd": 0.02, "latency_ms": 200.0},
        {"pass": True, "cost_usd": 0.03, "latency_ms": 300.0},
    ]
    s = summarize(results)
    assert s.total_runs == 3
    assert s.passes == 2
    assert s.pass_rate == pytest.approx(2 / 3)
    assert s.mean_cost_usd == pytest.approx(0.02)
    assert s.mean_latency_ms == pytest.approx(200.0)


def test_summarize_empty_returns_zeros():
    s = summarize([])
    assert s.total_runs == 0
    assert s.pass_rate == 0.0


def test_confidence_intervals_handle_small_n():
    ci_empty = confidence_intervals([])
    assert ci_empty.pass_rate.low == 0.0
    assert ci_empty.pass_rate.high == 0.0

    ci_one = confidence_intervals([{"pass": True, "cost_usd": 0.1, "latency_ms": 50.0}])
    assert 0.0 <= ci_one.pass_rate.low <= 1.0
    assert 0.0 <= ci_one.pass_rate.high <= 1.0
    assert ci_one.mean_cost_usd.low == pytest.approx(0.1)
    assert ci_one.mean_cost_usd.high == pytest.approx(0.1)


def test_load_fixtures(tmp_path: Path):
    fx_path = tmp_path / "fx.yaml"
    fx_path.write_text(
        yaml.safe_dump(
            [
                {"id": "a", "input": "do a", "expected": "result a"},
                {
                    "id": "b",
                    "input": "do b",
                    "expected": "result b",
                    "test_code": "assert True",
                    "meta": {"tag": "x"},
                },
            ]
        )
    )
    fxs = load_fixtures(fx_path)
    assert len(fxs) == 2
    assert fxs[0].id == "a"
    assert fxs[1].meta == {"tag": "x"}
    assert fxs[1].test_code == "assert True"


def test_end_to_end_mini_eval(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    fx_path = tmp_path / "fx.yaml"
    fx_path.write_text(
        yaml.safe_dump([{"id": "trivial", "input": "x", "expected": "y"}])
    )

    # Each run is: planner, coder, tester. N=2 runs => 6 calls.
    replies = []
    for _ in range(2):
        replies += [
            LLMResponse(text="plan", usage=Usage(1, 1), model="m"),
            LLMResponse(text="code", usage=Usage(1, 1), model="m"),
            LLMResponse(text="PASS\nok", usage=Usage(1, 1), model="m"),
        ]
    monkeypatch.setattr(orouter, "call_openrouter", _Replies(replies))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    out = run_eval(
        workflow="coder_tester",
        n_per_fixture=2,
        fixtures_path=fx_path,
        runs_root=runs_root,
        output_path=tmp_path / "eval.json",
    )
    assert out["overall"]["total_runs"] == 2
    assert out["overall"]["passes"] == 2
    assert "overall_ci" in out
    assert out["baseline_comparison"]["status"] in {"no_baseline", "invalid_baseline", "compared"}
    assert (tmp_path / "eval.json").exists()


def test_eval_baseline_update_and_regression(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    fx_path = tmp_path / "fx.yaml"
    fx_path.write_text(yaml.safe_dump([{"id": "trivial", "input": "x", "expected": "y"}]))
    baseline_path = tmp_path / "baseline.json"

    pass_replies = [
        LLMResponse(text="plan", usage=Usage(1, 1), model="m"),
        LLMResponse(text="code", usage=Usage(1, 1), model="m"),
        LLMResponse(text="PASS\nok", usage=Usage(1, 1), model="m"),
    ]
    monkeypatch.setattr(orouter, "call_openrouter", _Replies(pass_replies))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    first = run_eval(
        workflow="coder_tester",
        n_per_fixture=1,
        fixtures_path=fx_path,
        runs_root=runs_root,
        output_path=tmp_path / "eval_first.json",
        baseline_path=baseline_path,
        update_baseline=True,
    )
    assert first["regression_detected"] is False
    assert baseline_path.exists()

    def _mostly_fail(**kwargs):
        user_msg = kwargs["messages"][-1]["content"]
        if "EXPECTED OUTCOME:" in user_msg:
            return LLMResponse(text="FAIL\nneeds work", usage=Usage(1, 1), model="m")
        if user_msg.startswith("Task:"):
            return LLMResponse(text="plan", usage=Usage(1, 1), model="m")
        return LLMResponse(text="code", usage=Usage(1, 1), model="m")

    monkeypatch.setattr(orouter, "call_openrouter", _mostly_fail)
    second = run_eval(
        workflow="coder_tester",
        n_per_fixture=1,
        fixtures_path=fx_path,
        runs_root=runs_root,
        output_path=tmp_path / "eval_second.json",
        baseline_path=baseline_path,
    )
    assert second["baseline_comparison"]["status"] == "compared"
    assert second["regression_detected"] is True
    assert second["baseline_comparison"]["regressions"]


def test_eval_uses_sandbox_when_test_code_present(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    fx_path = tmp_path / "fx.yaml"
    fx_path.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "sandboxed",
                    "input": "write add",
                    "expected": "function add",
                    "test_code": "assert add(2, 3) == 5",
                }
            ]
        )
    )

    calls: list[int] = []

    def _strict_replies(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return LLMResponse(text="plan", usage=Usage(1, 1), model="m")
        if len(calls) == 2:
            return LLMResponse(text="def add(a, b):\n    return a + b", usage=Usage(1, 1), model="m")
        raise AssertionError("tester should not use OpenRouter when test_code is present")

    monkeypatch.setattr(orouter, "call_openrouter", _strict_replies)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    out = run_eval(
        workflow="coder_tester",
        n_per_fixture=1,
        fixtures_path=fx_path,
        runs_root=runs_root,
        output_path=tmp_path / "eval_sandbox.json",
    )
    assert out["overall"]["total_runs"] == 1
    assert out["overall"]["passes"] == 1
    assert len(calls) == 2
