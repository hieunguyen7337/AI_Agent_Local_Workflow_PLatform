from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.builder.nodes import EmbeddingNodeConfig, VectorRetrieverNodeConfig
from backend.graphspec import GraphSpec
from backend.providers.base import EmbeddingResponse, Usage
from backend.runtime.nodes.embedding import make_embedding_node
from backend.runtime.nodes.vector_retriever import make_vector_retriever_node


def test_graphspec_accepts_embedding_and_vector_retriever_nodes():
    spec = GraphSpec.model_validate(
        {
            "name": "vector_test",
            "budget": {"cost_usd": 0.1, "latency_ms": 1000},
            "entry": "embed",
            "nodes": [
                {
                    "id": "embed",
                    "kind": "embedding",
                    "provider": "openrouter",
                    "model": "google/gemini-embedding-2-preview",
                    "input_template": "{user_input}",
                    "output_state_key": "query_embedding",
                    "dimensions": 768,
                },
                {
                    "id": "retrieve",
                    "kind": "vector_retriever",
                    "index_path": "index.sqlite",
                    "query_embedding_state_key": "query_embedding",
                    "output_state_key": "retrieved_context",
                    "id_output_state_key": "retrieved_doc_ids",
                    "top_k": 2,
                },
            ],
            "edges": [
                {"from": "embed", "to": "retrieve"},
                {"from": "retrieve", "to": "__end__"},
            ],
        }
    )
    assert [node.kind for node in spec.nodes] == ["embedding", "vector_retriever"]


def test_graphspec_rejects_malformed_embedding_and_vector_retriever_nodes():
    with pytest.raises(ValidationError):
        EmbeddingNodeConfig(
            id="bad",
            model="google/gemini-embedding-2-preview",
            output_state_key="query_embedding",
            dimensions=0,
        )
    with pytest.raises(ValidationError):
        VectorRetrieverNodeConfig(
            id="bad",
            index_path="",
            query_embedding_state_key="query_embedding",
            output_state_key="retrieved_context",
        )


def test_embedding_node_builds_text_and_image_payload(monkeypatch, tmp_path: Path):
    image = tmp_path / "query.jpg"
    image.write_bytes(b"fake-jpeg")
    captured = {}

    def _fake_call(provider, **kwargs):
        captured["provider"] = provider
        captured.update(kwargs)
        return EmbeddingResponse(embedding=[0.1, 0.2], usage=Usage(5, 0), model=kwargs["model"])

    monkeypatch.setattr("backend.runtime.nodes.embedding.call_embedding_provider", _fake_call)
    cfg = EmbeddingNodeConfig(
        id="query_embedding",
        model="google/gemini-embedding-2-preview",
        input_template="brief: {dispatch_brief}",
        image_inputs=[{"state_key": "query_image_path", "detail": "auto"}],
        output_state_key="query_multimodal_embedding",
        dimensions=768,
    )
    costs = []
    node = make_embedding_node(cfg, run_id="r1", graph_name="g", on_cost=costs.append)
    out = node(
        {
            "dispatch_brief": "match person",
            "query_image_path": str(image),
            "iteration_counts": {},
        }
    )

    assert out == {"query_multimodal_embedding": [0.1, 0.2]}
    assert captured["provider"] == "openrouter"
    assert captured["model"] == "google/gemini-embedding-2-preview"
    assert captured["dimensions"] == 768
    content = captured["input_payload"][0]["content"]
    assert content[0] == {"type": "text", "text": "brief: match person"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_vector_retriever_ranks_sqlite_embeddings(tmp_path: Path):
    index = tmp_path / "index.sqlite"
    with sqlite3.connect(index) as con:
        con.execute("CREATE TABLE embeddings (id TEXT PRIMARY KEY, embedding_json TEXT NOT NULL, text TEXT)")
        con.executemany(
            "INSERT INTO embeddings (id, embedding_json, text) VALUES (?, ?, ?)",
            [
                ("a", json.dumps([1.0, 0.0]), "refunds are available for 30 days"),
                ("b", json.dumps([0.0, 1.0]), "support hours are weekdays"),
                ("c", json.dumps([0.8, 0.2]), "refund policy details"),
            ],
        )
    cfg = VectorRetrieverNodeConfig(
        id="vector_retriever",
        index_path=str(index),
        query_embedding_state_key="query_embedding",
        output_state_key="retrieved_context",
        id_output_state_key="retrieved_vector_doc_ids",
        top_k=2,
    )
    node = make_vector_retriever_node(cfg, run_id="r1", graph_name="g")
    out = node({"query_embedding": [1.0, 0.0], "iteration_counts": {}})

    assert out["retrieved_vector_doc_ids"] == ["a", "c"]
    assert "[a] refunds are available for 30 days" in out["retrieved_context"]
