"""FastAPI routes: read-only access to topology and runs."""
from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from backend.builder.api import GraphMetadata
from backend.builder.nodes import GateNodeConfig, RouterNodeConfig
from backend.server.node_metrics import compute_node_metrics

router = APIRouter()

RUNS_ROOT = Path("runs")


def load_workflow(name: str) -> GraphMetadata:
    try:
        module = importlib.import_module(f"backend.workflows.{name}")
    except ModuleNotFoundError:
        raise HTTPException(404, f"workflow {name!r} not found")
    return module.build_compiled()


def _topology_dict(metadata: GraphMetadata) -> dict:
    nodes = []
    for nid, cfg in metadata.nodes.items():
        nodes.append(
            {
                "id": nid,
                "kind": cfg.kind,
                "name": cfg.name or nid,
                "description": cfg.description,
            }
        )
    edges: list[dict] = []
    for s, t in metadata.edges:
        # Skip edges from conditional nodes — they are modeled separately.
        src_cfg = metadata.nodes.get(s)
        if isinstance(src_cfg, (GateNodeConfig, RouterNodeConfig)):
            continue
        edges.append({"source": s, "target": t, "kind": "normal"})
    for nid, cfg in metadata.nodes.items():
        if isinstance(cfg, GateNodeConfig):
            edges.append(
                {"source": nid, "target": cfg.pass_target, "kind": "conditional", "label": "pass"}
            )
            edges.append(
                {"source": nid, "target": cfg.fail_target, "kind": "conditional", "label": "fail"}
            )
        if isinstance(cfg, RouterNodeConfig):
            for label, target in cfg.routes.items():
                edges.append(
                    {"source": nid, "target": target, "kind": "conditional", "label": label}
                )
            if cfg.default_target is not None:
                edges.append(
                    {
                        "source": nid,
                        "target": cfg.default_target,
                        "kind": "conditional",
                        "label": "default",
                    }
                )
    loops = [
        {
            "loop_id": lp.loop_id,
            "from": lp.back_edge_from,
            "to": lp.back_edge_to,
            "max_iterations": lp.max_iterations,
        }
        for lp in metadata.loops
    ]
    return {
        "name": metadata.name,
        "entry": metadata.entry,
        "cost_budget_usd": metadata.cost_budget_usd,
        "latency_budget_ms": metadata.latency_budget_ms,
        "nodes": nodes,
        "edges": edges,
        "loops": loops,
    }


@router.get("/api/graph/{workflow}")
def get_graph(workflow: str) -> dict:
    metadata = load_workflow(workflow)
    return _topology_dict(metadata)


@router.get("/api/graph/{workflow}/node-metrics")
def get_graph_node_metrics(workflow: str, limit: int = 50) -> dict:
    if limit <= 0:
        raise HTTPException(400, "limit must be > 0")
    metadata = load_workflow(workflow)
    metrics = compute_node_metrics(
        runs_root=RUNS_ROOT,
        workflow=workflow,
        node_ids=set(metadata.nodes.keys()),
        limit=min(limit, 500),
    )
    runs_considered = max((m["runs_considered"] for m in metrics.values()), default=0)
    return {
        "workflow": workflow,
        "limit": min(limit, 500),
        "runs_considered": runs_considered,
        "metrics": metrics,
    }


@router.get("/api/runs")
def list_runs(limit: int = 50) -> list[dict]:
    return list_runs_data(runs_root=RUNS_ROOT, limit=limit)


def list_runs_data(*, runs_root: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Aggregate all runs across per-run SQLite databases under runs/."""
    if not runs_root.exists():
        return []
    out: list[dict[str, Any]] = []
    for d in runs_root.iterdir():
        if not d.is_dir():
            continue
        db = d / "telemetry.db"
        if not db.exists():
            continue
        try:
            with sqlite3.connect(db) as con:
                rows = con.execute(
                    "SELECT run_id, graph_name, started_ns, ended_ns, status, cost_usd, latency_ms, error "
                    "FROM runs"
                ).fetchall()
                for r in rows:
                    out.append(
                        {
                            "run_id": r[0],
                            "graph_name": r[1],
                            "started_ns": r[2],
                            "ended_ns": r[3],
                            "status": r[4],
                            "cost_usd": r[5],
                            "latency_ms": r[6],
                            "error": r[7],
                        }
                    )
        except sqlite3.OperationalError:
            continue
    out.sort(key=lambda r: r.get("started_ns") or 0, reverse=True)
    return out[:limit]


@router.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run = get_run_data(runs_root=RUNS_ROOT, run_id=run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    return run


def get_run_data(*, runs_root: Path, run_id: str) -> dict[str, Any] | None:
    run_dir = runs_root / run_id
    db = run_dir / "telemetry.db"
    if not db.exists():
        return None
    with sqlite3.connect(db) as con:
        run_row = con.execute(
            "SELECT run_id, graph_name, started_ns, ended_ns, status, cost_usd, latency_ms, error "
            "FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if not run_row:
            return None
        span_rows = con.execute(
            "SELECT span_id, name, start_ns, end_ns, duration_ms, status, node_id, node_kind, "
            "iteration, model, input_tokens, output_tokens, cost_usd, attributes_json "
            "FROM spans WHERE run_id=? ORDER BY start_ns ASC",
            (run_id,),
        ).fetchall()

    spans = []
    for s in span_rows:
        try:
            attrs = json.loads(s[13])
        except Exception:
            attrs = {}
        spans.append(
            {
                "span_id": s[0],
                "name": s[1],
                "start_ns": s[2],
                "end_ns": s[3],
                "duration_ms": s[4],
                "status": s[5],
                "node_id": s[6],
                "node_kind": s[7],
                "iteration": s[8],
                "model": s[9],
                "input_tokens": s[10],
                "output_tokens": s[11],
                "cost_usd": s[12],
                "attributes": attrs,
            }
        )

    return {
        "run_id": run_row[0],
        "graph_name": run_row[1],
        "started_ns": run_row[2],
        "ended_ns": run_row[3],
        "status": run_row[4],
        "cost_usd": run_row[5],
        "latency_ms": run_row[6],
        "error": run_row[7],
        "spans": spans,
    }
