"""WebSocket live update tests."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.server import app as app_module
from backend.server import routes
from backend.runtime.artifacts import resolve_run_dir, run_dir_for_id
from backend.telemetry.exporter import ensure_schema


def _upsert_run(
    runs_root: Path,
    *,
    run_id: str,
    graph_name: str,
    started_ns: int,
    status: str,
    ended_ns: int | None = None,
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
    error: str | None = None,
) -> None:
    run_dir = run_dir_for_id(runs_root, graph_name, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "telemetry.db"
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO runs "
            "(run_id, graph_name, started_ns, ended_ns, status, cost_usd, latency_ms, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, graph_name, started_ns, ended_ns, status, cost_usd, latency_ms, error),
        )


def _insert_span(
    runs_root: Path,
    *,
    run_id: str,
    span_id: str,
    start_ns: int,
    end_ns: int,
    node_id: str = "planner",
) -> None:
    db_path = resolve_run_dir(runs_root, run_id) / "telemetry.db"
    ensure_schema(db_path)
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT graph_name FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        graph_name = row[0] if row else "coder_tester"
        con.execute(
            "INSERT OR REPLACE INTO spans "
            "(span_id, trace_id, parent_span_id, name, start_ns, end_ns, duration_ms, status, "
            "run_id, graph_name, node_id, node_kind, iteration, model, input_tokens, output_tokens, cost_usd, attributes_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                span_id,
                span_id.rjust(32, "0"),
                None,
                f"node.{node_id}",
                start_ns,
                end_ns,
                (end_ns - start_ns) / 1_000_000,
                "OK",
                run_id,
                graph_name,
                node_id,
                "llm",
                0,
                "m",
                0,
                0,
                0.0,
                json.dumps({}),
            ),
        )


def _recv_until(ws, predicate, max_messages: int = 100):
    for _ in range(max_messages):
        msg = ws.receive_json()
        if predicate(msg):
            return msg
    raise AssertionError("timed out waiting for matching websocket message")


def test_ws_live_run_events_and_metrics(tmp_path: Path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)
    app_module.live_service.runs_root = runs_root
    app_module.live_service.poll_interval_s = 0.05
    app_module.live_service.heartbeat_interval_s = 0.1

    with TestClient(app_module.app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"action": "subscribe", "workflow": "coder_tester"})
            _recv_until(
                ws,
                lambda e: e.get("type") == "heartbeat"
                and e.get("data", {}).get("subscribed_workflow") == "coder_tester",
            )

            _upsert_run(
                runs_root,
                run_id="run_ws",
                graph_name="coder_tester",
                started_ns=10,
                status="running",
            )
            started = _recv_until(
                ws,
                lambda e: e.get("type") == "run_started" and e.get("run_id") == "run_ws",
            )
            assert started["workflow"] == "coder_tester"
            assert started["data"]["run"]["status"] == "running"

            _insert_span(
                runs_root,
                run_id="run_ws",
                span_id="span_1",
                start_ns=100,
                end_ns=200,
            )
            updated = _recv_until(
                ws,
                lambda e: e.get("type") == "run_updated" and e.get("run_id") == "run_ws",
            )
            assert updated["data"]["detail"]["span_count"] >= 1

            _upsert_run(
                runs_root,
                run_id="run_ws",
                graph_name="coder_tester",
                started_ns=10,
                ended_ns=500,
                status="ok",
                cost_usd=0.02,
                latency_ms=111.0,
            )
            finished = _recv_until(
                ws,
                lambda e: e.get("type") == "run_finished" and e.get("run_id") == "run_ws",
            )
            assert finished["data"]["run"]["status"] == "ok"

            metrics = _recv_until(
                ws,
                lambda e: e.get("type") == "node_metrics_updated"
                and e.get("workflow") == "coder_tester",
            )
            assert "metrics" in metrics["data"]


def test_ws_live_no_duplicate_run_events_and_workflow_scoped_metrics(tmp_path: Path, monkeypatch):
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    _upsert_run(
        runs_root,
        run_id="run_steady",
        graph_name="coder_tester",
        started_ns=10,
        status="ok",
        ended_ns=20,
    )

    monkeypatch.setattr(routes, "RUNS_ROOT", runs_root)
    app_module.live_service.runs_root = runs_root
    app_module.live_service.poll_interval_s = 0.05
    app_module.live_service.heartbeat_interval_s = 0.1

    with TestClient(app_module.app) as client:
        with client.websocket_connect("/ws/live") as ws:
            ws.send_json({"action": "subscribe", "workflow": "coder_tester"})
            _recv_until(
                ws,
                lambda e: e.get("type") == "run_started" and e.get("run_id") == "run_steady",
            )
            _recv_until(
                ws,
                lambda e: e.get("type") == "node_metrics_updated"
                and e.get("workflow") == "coder_tester",
            )

            steady_events = [ws.receive_json() for _ in range(8)]
            for evt in steady_events:
                assert not (
                    evt.get("run_id") == "run_steady"
                    and evt.get("type") in {"run_started", "run_updated", "run_finished"}
                )

            _upsert_run(
                runs_root,
                run_id="run_other",
                graph_name="linear_rag",
                started_ns=30,
                status="ok",
                ended_ns=40,
            )
            other_events = [ws.receive_json() for _ in range(10)]
            for evt in other_events:
                assert not (
                    evt.get("type") == "node_metrics_updated" and evt.get("workflow") == "linear_rag"
                )
