"""SQLite vector retriever node."""
from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from opentelemetry.trace import Status, StatusCode

from backend.builder.nodes import VectorRetrieverNodeConfig
from backend.runtime.audit import AuditRecorder, audit_preview
from backend.runtime.state import WorkflowState
from backend.telemetry.genai_attrs import WORKFLOW_LATENCY_MS, WORKFLOW_STATUS, node_attrs
from backend.telemetry.tracer import get_tracer


def make_vector_retriever_node(
    cfg: VectorRetrieverNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    audit: AuditRecorder | None = None,
):
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        query_embedding = _parse_embedding(state.get(cfg.query_embedding_state_key, []))
        attrs = node_attrs(
            run_id=run_id,
            graph_name=graph_name,
            node_id=cfg.id,
            node_kind="vector_retriever",
            iteration=state.get("iteration_counts", {}).get(cfg.id, 0),
        )
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            t0 = time.monotonic_ns()
            try:
                rows = _load_index(_format_template(cfg.index_path, state))
                scored = [
                    (_cosine_similarity(query_embedding, row["embedding"]), row)
                    for row in rows
                ]
                scored.sort(key=lambda item: (-item[0], item[1]["id"]))
                top = [row for _score, row in scored[: cfg.top_k]]
                ids = [row["id"] for row in top]
                output = _format_context(top)
                latency_ms = (time.monotonic_ns() - t0) / 1_000_000
                span.set_attribute(WORKFLOW_LATENCY_MS, latency_ms)
                span.set_attribute(WORKFLOW_STATUS, "HIT" if top else "MISS")
                span.set_status(Status(StatusCode.OK))
                result = {cfg.output_state_key: output}
                if cfg.id_output_state_key:
                    result[cfg.id_output_state_key] = ids
                if audit is not None:
                    audit.write_event(
                        {
                            "node_id": cfg.id,
                            "node_kind": "vector_retriever",
                            "status": "ok",
                            "index_path": _format_template(cfg.index_path, state),
                            "query_embedding_state_key": cfg.query_embedding_state_key,
                            "query_embedding_dimensions": len(query_embedding),
                            "top_k": cfg.top_k,
                            "output_state_key": cfg.output_state_key,
                            "output": audit_preview(output),
                            "ids": ids,
                            "latency_ms": latency_ms,
                        }
                    )
                return result
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                if audit is not None:
                    audit.write_event({"node_id": cfg.id, "node_kind": "vector_retriever", "status": "error", "error": f"{type(e).__name__}: {e}"})
                raise

    _node.__name__ = f"vector_retriever_{cfg.id}"
    return _node


def _format_template(template: str, state: WorkflowState) -> str:
    class _SafeDict(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(_SafeDict(state))


def _load_index(index_path: str) -> list[dict[str, Any]]:
    path = Path(index_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        raise FileNotFoundError(f"vector index not found: {index_path}")

    with sqlite3.connect(path) as con:
        for query in (
            "SELECT id, embedding_json, text FROM embeddings ORDER BY id",
            "SELECT gallery_id AS id, embedding_json, NULL AS text FROM gallery_embeddings ORDER BY gallery_id",
            "SELECT image_id AS id, embedding_json, NULL AS text FROM image_embeddings ORDER BY image_id",
        ):
            try:
                rows = con.execute(query).fetchall()
                return [
                    {"id": str(row[0]), "embedding": _parse_embedding(row[1]), "text": row[2]}
                    for row in rows
                ]
            except sqlite3.OperationalError:
                continue
    raise ValueError(
        "vector index must contain embeddings(id, embedding_json, text), "
        "gallery_embeddings(gallery_id, embedding_json), or image_embeddings(image_id, embedding_json)"
    )


def _parse_embedding(value: Any) -> list[float]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("embedding value must be a list")
    return [float(x) for x in value]


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"embedding dimension mismatch: query={len(a)} index={len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _format_context(rows: list[dict[str, Any]]) -> list[str] | str:
    if not rows:
        return []
    if any(row.get("text") for row in rows):
        return "\n\n".join(
            f"[{row['id']}] {str(row.get('text') or '').strip()}"
            for row in rows
        )
    return [row["id"] for row in rows]
