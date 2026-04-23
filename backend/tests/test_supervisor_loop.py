"""Supervisor loop workflow tests (M3.1)."""
from __future__ import annotations

from pathlib import Path

import yaml

from backend.evals.harness import run_eval
from backend.providers import openai as oai
from backend.providers.base import LLMResponse, Usage
from backend.runtime.executor import run_graph
from backend.workflows import supervisor_loop


class _Replies:
    def __init__(self, replies: list[LLMResponse]):
        self._replies = list(replies)

    def __call__(self, **kwargs) -> LLMResponse:
        if not self._replies:
            raise AssertionError("unexpected extra OpenAI call")
        return self._replies.pop(0)


def test_supervisor_loop_compile_shape():
    metadata = supervisor_loop.build_compiled()
    assert metadata.entry == "supervisor"
    assert metadata.node_ids() == ["supervisor", "dispatch", "researcher", "writer"]
    assert {loop.loop_id for loop in metadata.loops} == {
        "researcher->supervisor",
        "writer->supervisor",
    }


def test_supervisor_loop_runtime_and_eval(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    fx_path = tmp_path / "fixtures.yaml"
    fx_path.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "audit_trail",
                    "input": "Explain why workflow telemetry matters in 2-3 sentences. Include the exact phrase 'audit trail'.",
                    "expected": "audit trail",
                }
            ]
        ),
        encoding="utf-8",
    )

    replies = [
        LLMResponse(text="RESEARCHER", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="- Required phrase: audit trail\n- Focus on telemetry and debugging", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="WRITER", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="Workflow telemetry creates an audit trail and makes debugging faster.", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="FINISH", usage=Usage(1, 1), model="gpt-4o-mini"),
    ]
    monkeypatch.setattr(oai, "stream_openai", _Replies(replies))
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    metadata = supervisor_loop.build_compiled()
    run = run_graph(
        metadata,
        user_input="Explain why workflow telemetry matters in 2-3 sentences. Include the exact phrase 'audit trail'.",
        expected="audit trail",
        runs_root=runs_root,
    )
    assert run.status == "ok"
    assert run.final_state.get("supervisor_route") == "FINISH"
    assert "audit trail" in run.final_state.get("final_answer", "").lower()
    assert run.final_state.get("iteration_counts") == {
        "researcher->supervisor": 1,
        "writer->supervisor": 1,
    }

    eval_replies = [
        LLMResponse(text="RESEARCHER", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="- Required phrase: audit trail\n- Focus on telemetry and debugging", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="WRITER", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="Workflow telemetry creates an audit trail and makes debugging faster.", usage=Usage(1, 1), model="gpt-4o-mini"),
        LLMResponse(text="FINISH", usage=Usage(1, 1), model="gpt-4o-mini"),
    ]
    monkeypatch.setattr(oai, "stream_openai", _Replies(eval_replies))
    out = run_eval(
        workflow="supervisor_loop",
        n_per_fixture=1,
        fixtures_path=fx_path,
        runs_root=runs_root,
        output_path=tmp_path / "eval.json",
    )
    assert out["overall"]["total_runs"] == 1
    assert out["overall"]["passes"] == 1
