"""Linear RAG workflow tests (M2.1)."""
from __future__ import annotations

from pathlib import Path

import yaml

from backend.providers import openai as oai
from backend.providers.base import EmbeddingResponse, LLMResponse, Usage
from backend.runtime.executor import run_graph
from backend.runtime.nodes.retriever import make_retriever_node
from backend.builder.nodes import RetrieverNodeConfig
from backend.evals.harness import run_eval
from backend.graphspec import load_workflow_metadata


def test_linear_rag_compile_is_acyclic():
    metadata = load_workflow_metadata("linear_rag")
    assert metadata.entry == "query_analyser"
    assert metadata.loops == []
    assert metadata.node_ids() == [
        "query_analyser",
        "query_embedding",
        "vector_retriever",
        "reranker",
        "synthesiser",
    ]


def test_retriever_node_is_deterministic(tmp_path: Path):
    corpus = tmp_path / "corpus.yaml"
    corpus.write_text(
        yaml.safe_dump(
            [
                {"id": "a", "text": "refunds allowed for 30 days"},
                {"id": "b", "text": "support hours are 9 to 5"},
            ]
        ),
        encoding="utf-8",
    )
    cfg = RetrieverNodeConfig(
        id="retriever",
        corpus_path=str(corpus),
        query_state_key="query_analysis",
        output_state_key="retrieved_context",
        top_k=1,
    )
    node = make_retriever_node(cfg, run_id="r1", graph_name="linear_rag")
    out = node({"query_analysis": "refund period", "iteration_counts": {}})
    assert out["retrieved_doc_ids"] == ["a"]
    assert "30 days" in out["retrieved_context"]


def test_linear_rag_runtime_and_eval(monkeypatch, tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    fx_path = tmp_path / "fixtures.yaml"
    fx_path.write_text(
        yaml.safe_dump(
            [
                {
                    "id": "refund",
                    "input": "What is the refund window for Nimbus Cloud subscriptions?",
                    "expected": "30 days",
                }
            ]
        ),
        encoding="utf-8",
    )

    def _reply(**kwargs):
        user = kwargs["messages"][-1]["content"].lower()
        if "retrieved context" in user:
            return LLMResponse(text="Use [refund_window] Nimbus refunds in 30 days.", usage=Usage(1, 1), model="m")
        if "reranked context" in user:
            return LLMResponse(text="The refund window is 30 days.", usage=Usage(1, 1), model="m")
        return LLMResponse(text="refund window nimbus cloud", usage=Usage(1, 1), model="m")

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

    metadata = load_workflow_metadata("linear_rag")
    run = run_graph(
        metadata,
        user_input="What is the refund window for Nimbus Cloud subscriptions?",
        expected="30 days",
        runs_root=runs_root,
    )
    assert run.status == "ok"
    assert "30 days" in run.final_state.get("final_answer", "").lower()

    out = run_eval(
        workflow="linear_rag",
        n_per_fixture=1,
        fixtures_path=fx_path,
        runs_root=runs_root,
        output_path=tmp_path / "eval.json",
    )
    assert out["overall"]["total_runs"] == 1
    assert out["overall"]["passes"] == 1
