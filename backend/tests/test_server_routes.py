"""FastAPI route tests for graph telemetry overlays."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.server import routes
from backend.server.app import app
from backend.telemetry.exporter import ensure_schema
from backend.providers.base import LLMResponse, Usage


def _write_single_run(runs_root: Path) -> None:
    run_dir = runs_root / "run_api"
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "telemetry.db"
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO runs (run_id, graph_name, started_ns, status, cost_usd, latency_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("run_api", "coder_tester", 123, "ok", 0.01, 10.0, None),
        )
        con.execute(
            "INSERT OR REPLACE INTO spans "
            "(span_id, trace_id, parent_span_id, name, start_ns, end_ns, duration_ms, status, "
            "run_id, graph_name, node_id, node_kind, iteration, model, input_tokens, output_tokens, cost_usd, attributes_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "api_span_1",
                "1" * 32,
                None,
                "node.planner",
                100,
                200,
                100.0,
                "OK",
                "run_api",
                "coder_tester",
                "planner",
                "llm",
                0,
                "m",
                1,
                1,
                0.01,
                json.dumps({}),
            ),
        )


def test_graph_node_metrics_endpoint(tmp_path: Path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    _write_single_run(runs_root)
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)

    client = TestClient(app)
    resp = client.get("/api/graph/coder_tester/node-metrics?limit=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow"] == "coder_tester"
    assert body["limit"] == 50
    assert "metrics" in body
    assert "planner" in body["metrics"]
    assert body["metrics"]["planner"]["invocations"] >= 1


def test_graph_node_metrics_rejects_bad_limit():
    client = TestClient(app)
    resp = client.get("/api/graph/coder_tester/node-metrics?limit=0")
    assert resp.status_code == 400


def test_get_graph_linear_rag_shape():
    client = TestClient(app)
    resp = client.get("/api/graph/linear_rag")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "linear_rag"
    node_ids = {n["id"] for n in body["nodes"]}
    assert {"query_analyser", "retriever", "reranker", "synthesiser"} <= node_ids


def test_get_graph_supervisor_loop_shape():
    client = TestClient(app)
    resp = client.get("/api/graph/supervisor_loop")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "supervisor_loop"
    node_ids = {n["id"] for n in body["nodes"]}
    assert {"supervisor", "dispatch", "researcher", "writer"} <= node_ids
    labels = {(edge["source"], edge["label"], edge["target"]) for edge in body["edges"] if edge["kind"] == "conditional"}
    assert ("dispatch", "RESEARCHER", "researcher") in labels
    assert ("dispatch", "WRITER", "writer") in labels
    dispatch = next(node for node in body["nodes"] if node["id"] == "dispatch")
    assert dispatch["kind"] == "router"
    assert dispatch["metadata"]["routes"]["RESEARCHER"] == "researcher"


def test_get_graph_dispatch_aggregate_shape():
    client = TestClient(app)
    resp = client.get("/api/graph/dispatch_aggregate")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "dispatch_aggregate"
    node_ids = {n["id"] for n in body["nodes"]}
    assert {"dispatcher", "specialist_a", "specialist_b", "aggregator"} <= node_ids
    edges = {(edge["source"], edge["target"], edge["kind"]) for edge in body["edges"]}
    assert ("dispatcher", "specialist_a", "normal") in edges
    assert ("dispatcher", "specialist_b", "normal") in edges
    assert ("specialist_a", "aggregator", "normal") in edges
    assert ("specialist_b", "aggregator", "normal") in edges


def test_get_spec_returns_yaml_and_validated_spec():
    client = TestClient(app)
    resp = client.get("/api/spec/coder_tester")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workflow"] == "coder_tester"
    assert body["source_path"] == "workflows/coder_tester.yaml"
    assert "schema_version: workflow.graph/v1" in body["yaml"]
    assert body["spec"]["name"] == "coder_tester"
    assert body["spec"]["entry"] == "planner"


def test_get_spec_supervisor_loop_includes_router_routes():
    client = TestClient(app)
    resp = client.get("/api/spec/supervisor_loop")
    assert resp.status_code == 200
    body = resp.json()
    dispatch = next(node for node in body["spec"]["nodes"] if node["id"] == "dispatch")
    assert dispatch["kind"] == "router"
    assert dispatch["routes"]["RESEARCHER"] == "researcher"
    assert dispatch["routes"]["FINISH"] == "__end__"


def test_get_spec_missing_workflow_returns_404():
    client = TestClient(app)
    resp = client.get("/api/spec/not_a_workflow")
    assert resp.status_code == 404


def test_propose_mutation_returns_valid_diff(monkeypatch):
    current_yaml = Path("workflows/coder_tester.yaml").read_text(encoding="utf-8")
    proposed_yaml = current_yaml.replace("temperature: 0.2", "temperature: 0.1", 1)

    def _fake_call_provider(**kwargs):
        return LLMResponse(
            text=f"Summary: Lowered planner temperature.\n{proposed_yaml}",
            usage=Usage(1, 1),
            model="gpt-4o-mini",
        )

    monkeypatch.setattr("backend.graphspec.mutation.call_provider", _fake_call_provider)
    before = Path("workflows/coder_tester.yaml").read_text(encoding="utf-8")

    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/propose-mutation",
        json={"goal": "Make planner output more deterministic."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "valid"
    assert body["summary"] == "Lowered planner temperature."
    assert "temperature: 0.1" in body["proposed_yaml"]
    assert "-    temperature: 0.2" in body["diff"]
    assert "+    temperature: 0.1" in body["diff"]
    assert body["validation_errors"] == []
    assert Path("workflows/coder_tester.yaml").read_text(encoding="utf-8") == before


def test_propose_mutation_returns_invalid_validation_errors(monkeypatch):
    def _fake_call_provider(**kwargs):
        return LLMResponse(
            text="schema_version: workflow.graph/v1\nname: broken\nentry: missing\nnodes: []\nbudget:\n  cost_usd: 0.1\n  latency_ms: 1000\n",
            usage=Usage(1, 1),
            model="gpt-4o-mini",
        )

    monkeypatch.setattr("backend.graphspec.mutation.call_provider", _fake_call_provider)

    client = TestClient(app)
    resp = client.post(
        "/api/spec/coder_tester/propose-mutation",
        json={"goal": "Break it for test coverage."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "invalid"
    assert body["proposed_yaml"].startswith("schema_version")
    assert body["validation_errors"]


def test_propose_mutation_requires_goal():
    client = TestClient(app)
    resp = client.post("/api/spec/coder_tester/propose-mutation", json={"goal": ""})
    assert resp.status_code == 422


def test_propose_mutation_missing_workflow_returns_404():
    client = TestClient(app)
    resp = client.post(
        "/api/spec/not_a_workflow/propose-mutation",
        json={"goal": "Reduce cost."},
    )
    assert resp.status_code == 404
