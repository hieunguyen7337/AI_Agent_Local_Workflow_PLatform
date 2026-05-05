"""Per-node telemetry aggregation for graph overlays."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from backend.server.node_metrics import compute_node_metrics
from backend.runtime.artifacts import run_dir_for_id
from backend.telemetry.exporter import ensure_schema


def _write_run(
    run_dir: Path,
    *,
    run_id: str,
    graph_name: str,
    started_ns: int,
    spans: list[dict],
) -> None:
    db_path = run_dir / "telemetry.db"
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO runs (run_id, graph_name, started_ns, status, cost_usd, latency_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, graph_name, started_ns, "ok", 0.0, 0.0, None),
        )
        for i, span in enumerate(spans):
            start_ns = started_ns + i * 1_000_000
            end_ns = start_ns + int(float(span["duration_ms"]) * 1_000_000)
            con.execute(
                "INSERT OR REPLACE INTO spans "
                "(span_id, trace_id, parent_span_id, name, start_ns, end_ns, duration_ms, status, "
                "run_id, graph_name, node_id, node_kind, iteration, model, input_tokens, output_tokens, cost_usd, attributes_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{run_id}_s{i}",
                    f"{i:032x}",
                    None,
                    f"node.{span['node_id']}",
                    start_ns,
                    end_ns,
                    float(span["duration_ms"]),
                    span.get("status", "OK"),
                    run_id,
                    graph_name,
                    span["node_id"],
                    span.get("node_kind", "llm"),
                    span.get("iteration", 0),
                    "m",
                    0,
                    0,
                    float(span.get("cost_usd", 0.0)),
                    json.dumps(span.get("attrs", {})),
                ),
            )


def test_compute_node_metrics_aggregate_and_retries(tmp_path: Path):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    run1 = run_dir_for_id(runs_root, "coder_tester", "run_1")
    run2 = run_dir_for_id(runs_root, "coder_tester", "run_2")
    run1.mkdir(parents=True)
    run2.mkdir(parents=True)

    _write_run(
        run1,
        run_id="run_1",
        graph_name="coder_tester",
        started_ns=200,
        spans=[
            {"node_id": "planner", "node_kind": "llm", "duration_ms": 100.0, "cost_usd": 0.01},
            {
                "node_id": "tester",
                "node_kind": "tester",
                "duration_ms": 50.0,
                "cost_usd": 0.02,
                "attrs": {"workflow.status": "FAIL"},
            },
            {
                "node_id": "tester",
                "node_kind": "tester",
                "duration_ms": 80.0,
                "cost_usd": 0.03,
                "attrs": {"workflow.status": "PASS"},
            },
        ],
    )
    _write_run(
        run2,
        run_id="run_2",
        graph_name="coder_tester",
        started_ns=100,
        spans=[
            {"node_id": "planner", "node_kind": "llm", "duration_ms": 60.0, "cost_usd": 0.01},
            {
                "node_id": "tester",
                "node_kind": "tester",
                "duration_ms": 40.0,
                "cost_usd": 0.01,
                "attrs": {"workflow.status": "PASS"},
            },
        ],
    )

    metrics = compute_node_metrics(
        workflow="coder_tester",
        node_ids={"planner", "tester", "gate"},
        limit=50,
        runs_root=runs_root,
    )

    tester = metrics["tester"]
    assert tester["runs_considered"] == 2
    assert tester["invocations"] == 3
    assert tester["failed_invocations"] == 1
    assert tester["fail_pct"] == pytest.approx(33.3333, rel=1e-3)
    assert tester["p95_latency_ms"] == pytest.approx(80.0)
    assert tester["cost_per_run_usd"] == pytest.approx(0.03)
    assert tester["avg_retries_per_run"] == pytest.approx(0.5)
    assert tester["max_retries_in_run"] == 1

    gate = metrics["gate"]
    assert gate["runs_considered"] == 2
    assert gate["invocations"] == 0
    assert gate["fail_pct"] == 0.0
