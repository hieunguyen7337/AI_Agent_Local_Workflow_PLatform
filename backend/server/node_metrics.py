"""Aggregate per-node telemetry overlays from recent run databases."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import defaultdict
from pathlib import Path

import backend.runtime.artifacts as run_artifacts
from backend.runtime.artifacts import iter_run_dirs, run_search_roots_ordered

CACHE_TTL_SECONDS = 2.0
_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str, int], dict] = {}


def compute_node_metrics(
    *,
    workflow: str,
    node_ids: set[str],
    limit: int = 50,
    runs_root: Path | None = None,
) -> dict[str, dict]:
    if workflow.startswith("claude_code"):
        selected_runs = _select_recent_runs_merged(workflow=workflow, limit=limit)
        cache_key = ("merged", workflow, limit)
    elif runs_root is not None:
        selected_runs = _select_recent_runs(runs_root=runs_root, workflow=workflow, limit=limit)
        cache_key = (str(runs_root.resolve()), workflow, limit)
    else:
        std = run_artifacts.RUNS_ROOT
        selected_runs = _select_recent_runs(runs_root=std, workflow=workflow, limit=limit)
        cache_key = (str(std.resolve()), workflow, limit)
    run_ids = [r["run_id"] for r in selected_runs]
    fingerprint = "|".join(run_ids)
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and cached["fingerprint"] == fingerprint and now - cached["ts"] <= CACHE_TTL_SECONDS:
            return cached["metrics"]

    metrics = _aggregate_metrics(selected_runs=selected_runs, node_ids=node_ids)
    with _CACHE_LOCK:
        _CACHE[cache_key] = {"ts": now, "fingerprint": fingerprint, "metrics": metrics}
    return metrics


def _select_recent_runs(*, runs_root: Path, workflow: str, limit: int) -> list[dict]:
    if not runs_root.exists():
        return []
    rows: list[dict] = []
    for d in iter_run_dirs(runs_root):
        db = d / "telemetry.db"
        if not db.exists():
            continue
        try:
            with sqlite3.connect(db) as con:
                run_rows = con.execute(
                    "SELECT run_id, started_ns FROM runs WHERE graph_name=?",
                    (workflow,),
                ).fetchall()
        except sqlite3.OperationalError:
            continue
        for run_id, started_ns in run_rows:
            rows.append(
                {
                    "run_id": run_id,
                    "started_ns": started_ns or 0,
                    "db_path": db,
                }
            )
    rows.sort(key=lambda r: r["started_ns"], reverse=True)
    return rows[: max(0, limit)]


def _select_recent_runs_merged(*, workflow: str, limit: int) -> list[dict]:
    rows: list[dict] = []
    for runs_root in run_search_roots_ordered():
        rows.extend(_select_recent_runs(runs_root=runs_root, workflow=workflow, limit=limit * 3))
    rows.sort(key=lambda r: r["started_ns"], reverse=True)
    return rows[: max(0, limit)]


def _aggregate_metrics(*, selected_runs: list[dict], node_ids: set[str]) -> dict[str, dict]:
    runs_considered = len(selected_runs)
    observed: set[str] = set(node_ids)
    invocations: dict[str, int] = defaultdict(int)
    failed_invocations: dict[str, int] = defaultdict(int)
    latencies: dict[str, list[float]] = defaultdict(list)
    total_cost: dict[str, float] = defaultdict(float)
    node_run_invocations: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for run in selected_runs:
        run_id = run["run_id"]
        db_path = run["db_path"]
        try:
            with sqlite3.connect(db_path) as con:
                rows = con.execute(
                    "SELECT node_id, status, duration_ms, cost_usd, attributes_json "
                    "FROM spans WHERE run_id=? AND node_id IS NOT NULL",
                    (run_id,),
                ).fetchall()
        except sqlite3.OperationalError:
            continue
        for node_id, status, duration_ms, cost_usd, attributes_json in rows:
            if not node_id:
                continue
            node = str(node_id)
            observed.add(node)
            invocations[node] += 1
            node_run_invocations[node][run_id] += 1
            latencies[node].append(float(duration_ms or 0.0))
            total_cost[node] += float(cost_usd or 0.0)

            attrs = _parse_attrs(attributes_json)
            workflow_status = str(attrs.get("workflow.status", "")).upper()
            if str(status).upper() == "ERROR" or workflow_status == "FAIL":
                failed_invocations[node] += 1

    out: dict[str, dict] = {}
    for node in sorted(observed):
        n_inv = invocations.get(node, 0)
        n_fail = failed_invocations.get(node, 0)
        retries = _retries_per_run(node_run_invocations.get(node, {}), [r["run_id"] for r in selected_runs])
        out[node] = {
            "node_id": node,
            "runs_considered": runs_considered,
            "invocations": n_inv,
            "failed_invocations": n_fail,
            "fail_pct": (100.0 * n_fail / n_inv) if n_inv else 0.0,
            "p95_latency_ms": _p95(latencies.get(node, [])),
            "cost_per_run_usd": (total_cost.get(node, 0.0) / runs_considered) if runs_considered else 0.0,
            "avg_retries_per_run": (sum(retries) / runs_considered) if runs_considered else 0.0,
            "max_retries_in_run": max(retries) if retries else 0,
        }
    return out


def _parse_attrs(attributes_json: str | None) -> dict:
    if not attributes_json:
        return {}
    try:
        raw = json.loads(attributes_json)
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    vals = sorted(values)
    idx = max(0, min(len(vals) - 1, int(round(0.95 * (len(vals) - 1)))))
    return float(vals[idx])


def _retries_per_run(run_counts: dict[str, int], run_ids: list[str]) -> list[int]:
    out: list[int] = []
    for rid in run_ids:
        out.append(max(0, int(run_counts.get(rid, 0)) - 1))
    return out
