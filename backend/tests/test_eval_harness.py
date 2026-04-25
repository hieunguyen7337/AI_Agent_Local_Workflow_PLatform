"""Eval harness: fixture loading + metrics aggregation + end-to-end small run."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.evals.fixtures import load_fixtures
from backend.evals.harness import run_eval
from backend.evals.metrics import confidence_intervals, summarize
from backend.providers import openai as oai
from backend.providers import openrouter as orouter
from backend.providers.openrouter import LLMResponse, Usage
from backend.runtime.artifacts import resolve_run_dir


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
                    "approval_decision": "approved",
                    "approval_comment": "ok",
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
    assert fxs[1].approval_decision == "approved"
    assert fxs[1].approval_comment == "ok"


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
    monkeypatch.setattr(orouter, "stream_openrouter", _Replies(replies))
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
    monkeypatch.setattr(orouter, "stream_openrouter", _Replies(pass_replies))
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

    monkeypatch.setattr(orouter, "stream_openrouter", _mostly_fail)
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

    monkeypatch.setattr(orouter, "stream_openrouter", _strict_replies)
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


def test_approval_eval_approved_and_rejected_paths(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    fx_path = tmp_path / "approval_fixtures.yaml"
    fx_path.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "approved",
                    "input": "include human approved",
                    "expected": "human approved",
                    "approval_decision": "approved",
                },
                {
                    "id": "rejected",
                    "input": "reject this",
                    "expected": "",
                    "approval_decision": "rejected",
                },
            ]
        )
    )

    calls: list[str] = []

    def _openai_reply(**kwargs):
        user = kwargs["messages"][-1]["content"]
        calls.append(user)
        if "include human approved" in user:
            return LLMResponse(text="draft with human approved", usage=Usage(1, 1), model="gpt-4o-mini")
        if "Approved draft:" in user:
            return LLMResponse(text="final human approved", usage=Usage(1, 1), model="gpt-4o-mini")
        if "reject this" in user:
            return LLMResponse(text="draft to reject", usage=Usage(1, 1), model="gpt-4o-mini")
        raise AssertionError(f"unexpected OpenAI call: {user}")

    monkeypatch.setattr(oai, "stream_openai", _openai_reply)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    out = run_eval(
        workflow="approval_review",
        n_per_fixture=1,
        fixtures_path=fx_path,
        runs_root=runs_root,
        output_path=tmp_path / "eval_approval.json",
    )

    assert out["overall"]["total_runs"] == 2
    assert out["overall"]["passes"] == 2
    assert out["approval_path_coverage"] == {"approved": 1, "rejected": 1}
    approved, rejected = out["results"]
    assert approved["source_run_id"] != approved["continuation_run_id"]
    assert approved["run_id"] == approved["continuation_run_id"]
    assert approved["approval_decision"] == "approved"
    assert Path(approved["decision_artifact"]).exists()
    assert Path(approved["approval_resume_artifact"]).exists()
    assert rejected["approval_decision"] == "rejected"
    assert rejected["pass"] is True
    finalizer_calls = [call for call in calls if "Approved draft:" in call]
    assert len(finalizer_calls) == 1


def test_approval_eval_missing_decision_fails_clearly(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    fx_path = tmp_path / "approval_fixtures.yaml"
    fx_path.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "missing_decision",
                    "input": "needs review",
                    "expected": "anything",
                }
            ]
        )
    )

    def _openai_reply(**kwargs):
        return LLMResponse(text="draft", usage=Usage(1, 1), model="gpt-4o-mini")

    monkeypatch.setattr(oai, "stream_openai", _openai_reply)
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    out = run_eval(
        workflow="approval_review",
        n_per_fixture=1,
        fixtures_path=fx_path,
        runs_root=runs_root,
        output_path=tmp_path / "eval_missing_decision.json",
    )

    assert out["overall"]["total_runs"] == 1
    assert out["overall"]["passes"] == 0
    assert out["results"][0]["status"] == "pending_approval"
    assert "approval_decision is required" in out["results"][0]["error"]


def test_subgraph_eval_scores_mapped_output_and_records_lineage(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    fx_path = tmp_path / "subgraph_fixtures.yaml"
    fx_path.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "refund",
                    "input": "What is the refund window?",
                    "expected": "30 days",
                }
            ]
        )
    )

    replies = [
        LLMResponse(text="refund window", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="Nimbus refunds are available within 30 days.", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="The refund window is 30 days.", usage=Usage(1, 1), model="gpt-4o-mini"),
    ]
    monkeypatch.setattr(oai, "stream_openai", _Replies(replies))
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    out = run_eval(
        workflow="rag_subgraph_wrapper",
        n_per_fixture=1,
        fixtures_path=fx_path,
        runs_root=runs_root,
        output_path=tmp_path / "eval_subgraph.json",
    )

    assert out["overall"]["total_runs"] == 1
    assert out["overall"]["passes"] == 1
    result = out["results"][0]
    assert result["status"] == "ok"
    assert result["subgraphs"][0]["child_workflow"] == "linear_rag"
    assert Path(result["subgraphs"][0]["artifact_path"]).exists()
    assert (resolve_run_dir(runs_root, result["subgraphs"][0]["child_run_id"]) / "parent_run.json").exists()
