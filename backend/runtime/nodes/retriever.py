"""Deterministic local-corpus retriever node for linear RAG workflows."""
from __future__ import annotations

import re
import time
from pathlib import Path

import yaml
from opentelemetry.trace import Status, StatusCode

from backend.builder.nodes import RetrieverNodeConfig
from backend.runtime.state import WorkflowState
from backend.telemetry.genai_attrs import WORKFLOW_LATENCY_MS, WORKFLOW_STATUS, node_attrs
from backend.telemetry.tracer import get_tracer

_CORPUS_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}


def make_retriever_node(
    cfg: RetrieverNodeConfig,
    *,
    run_id: str,
    graph_name: str,
):
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        query = str(state.get(cfg.query_state_key, "") or "")
        attrs = node_attrs(
            run_id=run_id,
            graph_name=graph_name,
            node_id=cfg.id,
            node_kind="retriever",
            iteration=state.get("iteration_counts", {}).get(cfg.id, 0),
        )
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            t0 = time.monotonic_ns()
            try:
                docs = _load_corpus(cfg.corpus_path)
                top_docs = _retrieve_docs(query=query, docs=docs, top_k=cfg.top_k)
                context = "\n\n".join(f"[{d['id']}] {d['text']}" for d in top_docs)
                latency_ms = (time.monotonic_ns() - t0) / 1_000_000
                span.set_attribute(WORKFLOW_LATENCY_MS, latency_ms)
                span.set_attribute(WORKFLOW_STATUS, "HIT" if top_docs else "MISS")
                span.set_status(Status(StatusCode.OK))
                return {
                    cfg.output_state_key: context,
                    "retrieved_doc_ids": [d["id"] for d in top_docs],
                }
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    _node.__name__ = f"retriever_{cfg.id}"
    return _node


def _load_corpus(path: str) -> list[dict[str, str]]:
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    p = p.resolve()
    mtime = p.stat().st_mtime
    key = str(p)
    cached = _CORPUS_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []
    if not isinstance(raw, list):
        raise ValueError(f"retriever corpus must be a list of docs in {p}")
    docs: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("id", f"doc_{i+1}"))
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        docs.append({"id": doc_id, "text": text})
    _CORPUS_CACHE[key] = (mtime, docs)
    return docs


def _retrieve_docs(*, query: str, docs: list[dict[str, str]], top_k: int) -> list[dict[str, str]]:
    q_tokens = _tokens(query)
    ranked: list[tuple[int, str, dict[str, str]]] = []
    for d in docs:
        score = len(q_tokens & _tokens(d["text"]))
        ranked.append((score, d["id"], d))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    hits = [d for score, _id, d in ranked if score > 0]
    if hits:
        return hits[:top_k]
    return [d for _score, _id, d in ranked[:top_k]]


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))

