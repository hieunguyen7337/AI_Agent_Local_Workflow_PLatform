"""Dispatch-and-aggregate workflow tests (M3.2)."""
from __future__ import annotations

from pathlib import Path

import yaml

from backend.evals.harness import run_eval
from backend.providers import openai as oai
from backend.providers.base import LLMResponse, Usage
from backend.runtime.executor import run_graph
from backend.workflows import dispatch_aggregate


class _Replies:
    def __init__(self, replies: list[LLMResponse]):
        self._replies = list(replies)

    def __call__(self, **kwargs) -> LLMResponse:
        if not self._replies:
            raise AssertionError("unexpected extra OpenAI call")
        return self._replies.pop(0)


class _DispatchReplies:
    def __call__(self, **kwargs) -> LLMResponse:
        prompt = kwargs["messages"][1]["content"]
        if "Specialist A notes:" in prompt and "Specialist B notes:" in prompt:
            return LLMResponse(
                text="Local-first workflow tools improve fast feedback and audit trail quality for teams.",
                usage=Usage(1, 1),
                model="gpt-4o-mini",
            )
        if "Write Specialist A notes" in prompt:
            return LLMResponse(
                text="- fast feedback keeps iteration tight",
                usage=Usage(1, 1),
                model="gpt-4o-mini",
            )
        if "Write Specialist B notes" in prompt:
            return LLMResponse(
                text="- audit trail preserves reviewable history",
                usage=Usage(1, 1),
                model="gpt-4o-mini",
            )
        return LLMResponse(
            text="Specialist A: cover fast feedback. Specialist B: cover audit trail.",
            usage=Usage(1, 1),
            model="gpt-4o-mini",
        )


def test_dispatch_aggregate_compile_shape():
    metadata = dispatch_aggregate.build_compiled()
    assert metadata.entry == "dispatcher"
    assert metadata.loops == []
    assert metadata.node_ids() == ["dispatcher", "specialist_a", "specialist_b", "aggregator"]


def test_dispatch_aggregate_runtime_and_eval(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    fx_path = tmp_path / "fixtures.yaml"
    fx_path.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "combined_phrase",
                    "input": "Explain why local-first workflow tools matter in 2-3 sentences. Include the exact phrase 'fast feedback and audit trail'.",
                    "expected": "fast feedback and audit trail",
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(oai, "stream_openai", _DispatchReplies())
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    metadata = dispatch_aggregate.build_compiled()
    run = run_graph(
        metadata,
        user_input="Explain why local-first workflow tools matter in 2-3 sentences. Include the exact phrase 'fast feedback and audit trail'.",
        expected="fast feedback and audit trail",
        runs_root=runs_root,
    )
    assert run.status == "ok"
    assert "fast feedback" in run.final_state.get("specialist_a_notes", "").lower()
    assert "audit trail" in run.final_state.get("specialist_b_notes", "").lower()
    assert "fast feedback and audit trail" in run.final_state.get("final_answer", "").lower()

    monkeypatch.setattr(oai, "stream_openai", _DispatchReplies())
    out = run_eval(
        workflow="dispatch_aggregate",
        n_per_fixture=1,
        fixtures_path=fx_path,
        runs_root=runs_root,
        output_path=tmp_path / "eval.json",
    )
    assert out["overall"]["total_runs"] == 1
    assert out["overall"]["passes"] == 1
