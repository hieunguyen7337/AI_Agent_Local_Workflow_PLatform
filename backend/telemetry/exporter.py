"""Custom OTEL span exporter: writes to SQLite (indexed rows) + JSONL (full fidelity)."""
from __future__ import annotations

import json
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path
from typing import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from .genai_attrs import (
    GEN_AI_REQUEST_MODEL,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    WORKFLOW_COST_USD,
    WORKFLOW_GRAPH_NAME,
    WORKFLOW_ITERATION,
    WORKFLOW_LATENCY_MS,
    WORKFLOW_NODE_ID,
    WORKFLOW_NODE_KIND,
    WORKFLOW_RUN_ID,
    WORKFLOW_STATUS,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    span_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_span_id TEXT,
    name TEXT NOT NULL,
    start_ns INTEGER NOT NULL,
    end_ns INTEGER NOT NULL,
    duration_ms REAL NOT NULL,
    status TEXT,
    run_id TEXT,
    graph_name TEXT,
    node_id TEXT,
    node_kind TEXT,
    iteration INTEGER,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    attributes_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_spans_run_id ON spans(run_id);
CREATE INDEX IF NOT EXISTS idx_spans_node_id ON spans(node_id);
CREATE INDEX IF NOT EXISTS idx_spans_start ON spans(start_ns);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    graph_name TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    ended_ns INTEGER,
    status TEXT NOT NULL,
    cost_usd REAL DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_ns DESC);
"""


def ensure_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        con.executescript(SCHEMA)


class SqliteJsonlExporter(SpanExporter):
    def __init__(self, db_path: Path, jsonl_path: Path):
        self.db_path = db_path
        self.jsonl_path = jsonl_path
        self._lock = threading.Lock()
        ensure_schema(db_path)
        jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        with self._lock:
            with sqlite3.connect(self.db_path) as con, self.jsonl_path.open("a", encoding="utf-8") as jf:
                for span in spans:
                    attrs = dict(span.attributes or {})
                    start_ns = span.start_time or 0
                    end_ns = span.end_time or 0
                    duration_ms = max(0.0, (end_ns - start_ns) / 1_000_000)
                    row = (
                        format(span.context.span_id, "016x"),
                        format(span.context.trace_id, "032x"),
                        format(span.parent.span_id, "016x") if span.parent else None,
                        span.name,
                        start_ns,
                        end_ns,
                        duration_ms,
                        str(span.status.status_code.name) if span.status else None,
                        attrs.get(WORKFLOW_RUN_ID),
                        attrs.get(WORKFLOW_GRAPH_NAME),
                        attrs.get(WORKFLOW_NODE_ID),
                        attrs.get(WORKFLOW_NODE_KIND),
                        attrs.get(WORKFLOW_ITERATION),
                        attrs.get(GEN_AI_REQUEST_MODEL),
                        attrs.get(GEN_AI_USAGE_INPUT_TOKENS),
                        attrs.get(GEN_AI_USAGE_OUTPUT_TOKENS),
                        attrs.get(WORKFLOW_COST_USD),
                        json.dumps(_jsonable(attrs)),
                    )
                    con.execute(
                        "INSERT OR REPLACE INTO spans "
                        "(span_id, trace_id, parent_span_id, name, start_ns, end_ns, duration_ms, "
                        "status, run_id, graph_name, node_id, node_kind, iteration, "
                        "model, input_tokens, output_tokens, cost_usd, attributes_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        row,
                    )
                    jf.write(
                        json.dumps(
                            {
                                "span_id": row[0],
                                "trace_id": row[1],
                                "parent_span_id": row[2],
                                "name": row[3],
                                "start_ns": start_ns,
                                "end_ns": end_ns,
                                "duration_ms": duration_ms,
                                "attributes": _jsonable(attrs),
                            }
                        )
                        + "\n"
                    )
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class RoutingSpanExporter(SpanExporter):
    """Route spans to the run-local exporter registered for workflow.run_id."""

    def __init__(self):
        self._lock = threading.Lock()
        self._exporters: dict[str, SqliteJsonlExporter] = {}

    def register_run(self, *, run_id: str, run_dir: Path) -> None:
        with self._lock:
            if run_id not in self._exporters:
                self._exporters[run_id] = SqliteJsonlExporter(
                    db_path=run_dir / "telemetry.db",
                    jsonl_path=run_dir / "spans.jsonl",
                )

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        grouped: dict[str, list[ReadableSpan]] = defaultdict(list)
        for span in spans:
            attrs = dict(span.attributes or {})
            run_id = attrs.get(WORKFLOW_RUN_ID)
            if isinstance(run_id, str) and run_id:
                grouped[run_id].append(span)

        with self._lock:
            exports = [(self._exporters.get(run_id), run_spans) for run_id, run_spans in grouped.items()]

        for exporter, run_spans in exports:
            if exporter is not None:
                result = exporter.export(run_spans)
                if result is not SpanExportResult.SUCCESS:
                    return result
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        with self._lock:
            exporters = list(self._exporters.values())
            self._exporters.clear()
        for exporter in exporters:
            exporter.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        with self._lock:
            exporters = list(self._exporters.values())
        return all(exporter.force_flush(timeout_millis) for exporter in exporters)


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def record_run_start(db_path: Path, *, run_id: str, graph_name: str, started_ns: int) -> None:
    with sqlite3.connect(db_path) as con:
        con.execute(
            "INSERT OR REPLACE INTO runs (run_id, graph_name, started_ns, status) VALUES (?, ?, ?, ?)",
            (run_id, graph_name, started_ns, "running"),
        )


def record_run_end(
    db_path: Path,
    *,
    run_id: str,
    ended_ns: int,
    status: str,
    cost_usd: float,
    latency_ms: float,
    error: str | None = None,
) -> None:
    with sqlite3.connect(db_path) as con:
        con.execute(
            "UPDATE runs SET ended_ns=?, status=?, cost_usd=?, latency_ms=?, error=? WHERE run_id=?",
            (ended_ns, status, cost_usd, latency_ms, error, run_id),
        )
