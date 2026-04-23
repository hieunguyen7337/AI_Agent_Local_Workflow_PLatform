"""FastAPI route tests for graph telemetry overlays."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.server import routes
from backend.server.app import app
from backend.telemetry.exporter import ensure_schema


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
