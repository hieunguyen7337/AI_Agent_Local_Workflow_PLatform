"""Workflow-as-function API tests."""
from __future__ import annotations

import json
import logging
import inspect
from pathlib import Path

from backend.providers import openai as oai
from backend.providers import openrouter as orouter
from backend.providers.base import EmbeddingResponse, LLMResponse, Usage
from backend.runtime import WorkflowBatchItem, WorkflowFunctionResult, run_workflow_batch, run_workflow_function
from backend.runtime.artifacts import resolve_run_dir


class _Replies:
    def __init__(self, replies: list[LLMResponse]):
        self._replies = list(replies)

    def __call__(self, **kwargs) -> LLMResponse:
        if not self._replies:
            return LLMResponse(text="", usage=Usage(0, 0), model=kwargs.get("model", "x"))
        return self._replies.pop(0)


def test_run_workflow_function_accepts_string_user_input(monkeypatch, tmp_path: Path):
    calls: list[str] = []

    def _reply(**kwargs):
        calls.append(kwargs["messages"][-1]["content"])
        return LLMResponse(text="query", usage=Usage(1, 1), model="gpt-4o-mini")

    monkeypatch.setattr(oai, "stream_openai", _reply)
    monkeypatch.setattr(
        "backend.runtime.nodes.embedding.call_embedding_provider",
        lambda *args, **kwargs: EmbeddingResponse(
            embedding=[1.0] + [0.0] * 767,
            usage=Usage(1, 0),
            model=kwargs["model"],
        ),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    result = run_workflow_function(
        "linear_rag",
        "What is the refund window?",
        runs_root=tmp_path / "runs",
    )

    assert isinstance(result, WorkflowFunctionResult)
    assert result.workflow == "linear_rag"
    assert result.status == "ok"
    assert result.final_state["user_input"] == "What is the refund window?"
    assert result.run_dir.exists()
    assert calls


def test_run_workflow_function_accepts_full_state_overrides(monkeypatch, tmp_path: Path):
    calls: list[int] = []

    def _reply(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return LLMResponse(text="plan", usage=Usage(1, 1), model="m")
        if len(calls) == 2:
            return LLMResponse(
                text="def add(a, b):\n    return a + b\n",
                usage=Usage(1, 1),
                model="m",
            )
        raise AssertionError("tester should use _test_code sandbox from input_state")

    monkeypatch.setattr(orouter, "stream_openrouter", _reply)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")

    result = run_workflow_function(
        "coder_tester",
        {
            "user_input": "write add",
            "_expected": "function add",
            "_test_code": "assert add(2, 3) == 5",
            "artifacts": {"dataset_row": 7},
        },
        runs_root=tmp_path / "runs",
    )

    assert result.status == "ok"
    assert result.final_state["tester_mode"] == "sandbox"
    assert result.final_state["tester_verdict"] is True
    assert result.final_state["artifacts"]["dataset_row"] == 7
    assert len(calls) == 2


def test_run_workflow_function_surfaces_pending_approval(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        oai,
        "stream_openai",
        lambda **kwargs: LLMResponse(text="Draft answer.", usage=Usage(1, 1), model="gpt-4o-mini"),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    result = run_workflow_function(
        "approval_review",
        {"user_input": "Draft a short answer."},
        runs_root=tmp_path / "runs",
    )

    assert result.status == "pending_approval"
    assert result.error is None
    assert result.final_state["pending_approval"]["node_id"] == "human_review"
    assert (result.run_dir / "approval.json").exists()


def test_run_workflow_function_executes_subgraph(monkeypatch, tmp_path: Path):
    replies = [
        LLMResponse(text="refund query", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="refund evidence", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="Refunds are available within 30 days.", usage=Usage(1, 1), model="gpt-4o-mini"),
    ]
    monkeypatch.setattr(oai, "stream_openai", _Replies(replies))
    monkeypatch.setattr(
        "backend.runtime.nodes.embedding.call_embedding_provider",
        lambda *args, **kwargs: EmbeddingResponse(
            embedding=[1.0] + [0.0] * 767,
            usage=Usage(1, 0),
            model=kwargs["model"],
        ),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    runs_root = tmp_path / "runs"

    result = run_workflow_function(
        "rag_subgraph_wrapper",
        {"user_input": "What is the refund window?"},
        runs_root=runs_root,
    )

    assert result.status == "ok"
    assert result.final_state["rag_answer"] == "Refunds are available within 30 days."
    lineage_files = list((result.run_dir / "subgraphs").glob("rag_child_*.json"))
    assert len(lineage_files) == 1
    lineage = json.loads(lineage_files[0].read_text(encoding="utf-8"))
    child_run_id = lineage["child_run_id"]
    assert resolve_run_dir(runs_root, child_run_id).exists()


def test_run_workflow_batch_returns_ordered_item_results(monkeypatch, tmp_path: Path):
    replies = [
        LLMResponse(text="first", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="second", usage=Usage(1, 1), model="gpt-4o-mini"),
    ]
    monkeypatch.setattr(oai, "stream_openai", _Replies(replies))
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    results = run_workflow_batch(
        "simple_llm_template",
        [
            WorkflowBatchItem(id="b", input_state={"user_input": "second"}),
            WorkflowBatchItem(id="a", input_state={"user_input": "first"}),
        ],
        max_concurrency=1,
        runs_root=tmp_path / "runs",
    )

    assert [result.id for result in results] == ["b", "a"]
    assert [result.status for result in results] == ["ok", "ok"]
    assert results[0].run_id != results[1].run_id


def test_run_workflow_batch_default_max_concurrency_is_50(monkeypatch, tmp_path: Path):
    assert inspect.signature(run_workflow_batch).parameters["max_concurrency"].default == 50
    captured: dict = {}

    def _fake_run_workflow_function(workflow_id, input_state, **kwargs):
        captured["workflow_id"] = workflow_id
        captured["input_state"] = input_state
        return WorkflowFunctionResult(
            workflow=workflow_id,
            status="ok",
            final_state={"final_answer": "ok"},
            run_id="run_fake",
            run_dir=tmp_path / "runs" / "run_fake",
            error=None,
            cost_usd=0.0,
            latency_ms=0.0,
        )

    monkeypatch.setattr("backend.runtime.functions.run_workflow_function", _fake_run_workflow_function)
    results = run_workflow_batch(
        "simple_llm_template",
        [WorkflowBatchItem(id="one", input_state={"user_input": "x"})],
    )
    assert results[0].status == "ok"
    assert captured["workflow_id"] == "simple_llm_template"


def test_run_workflow_batch_50_concurrent_runs_keep_span_files_isolated(
    monkeypatch,
    tmp_path: Path,
    caplog,
):
    monkeypatch.setattr(
        oai,
        "stream_openai",
        lambda **kwargs: LLMResponse(text="ok", usage=Usage(1, 1), model="gpt-4o-mini"),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    caplog.set_level(logging.WARNING)

    results = run_workflow_batch(
        "simple_llm_template",
        [
            WorkflowBatchItem(id=str(index), input_state={"user_input": f"item {index}"})
            for index in range(50)
        ],
        runs_root=tmp_path / "runs",
    )

    assert len(results) == 50
    assert {result.status for result in results} == {"ok"}
    assert "Overriding of current TracerProvider is not allowed" not in caplog.text

    for result in results:
        assert result.run_id is not None
        assert result.run_dir is not None
        assert (result.run_dir / "telemetry.db").exists()
        assert (result.run_dir / "spans.jsonl").exists()
        assert (result.run_dir / "run_manifest.json").exists()
        lines = (result.run_dir / "spans.jsonl").read_text(encoding="utf-8").splitlines()
        assert lines
        for line in lines:
            payload = json.loads(line)
            assert payload["attributes"]["workflow.run_id"] == result.run_id


def test_run_workflow_batch_captures_item_failure(monkeypatch, tmp_path: Path):
    replies = [
        LLMResponse(text="ok", usage=Usage(1, 1), model="gpt-4o-mini"),
    ]
    monkeypatch.setattr(oai, "stream_openai", _Replies(replies))
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    results = run_workflow_batch(
        "missing_workflow",
        [
            WorkflowBatchItem(id="bad", input_state={"user_input": "x"}),
        ],
        runs_root=tmp_path / "runs",
    )

    assert results[0].id == "bad"
    assert results[0].status == "error"
    assert "FileNotFoundError" in (results[0].error or "")
