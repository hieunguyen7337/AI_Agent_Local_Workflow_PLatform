"""Pytest hooks and fixtures shared across backend tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LINEAR_RAG_INDEX = _REPO_ROOT / "evals" / "linear_rag" / "vector_index.sqlite"


def _write_minimal_linear_rag_index(path: Path) -> None:
    """Create a tiny SQLite vector index compatible with ``vector_retriever``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    embedding = json.dumps([1.0] + [0.0] * 767)
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS embeddings (id TEXT PRIMARY KEY, embedding_json TEXT, text TEXT)"
        )
        con.execute("DELETE FROM embeddings")
        con.execute(
            "INSERT INTO embeddings (id, embedding_json, text) VALUES (?, ?, ?)",
            (
                "refund_window",
                embedding,
                "Nimbus refunds are available within 30 days.",
            ),
        )
        con.commit()


@pytest.fixture(scope="session", autouse=True)
def _ensure_linear_rag_vector_index() -> None:
    """CI and fresh clones lack ``evals/linear_rag/vector_index.sqlite``; create a minimal one."""
    if _LINEAR_RAG_INDEX.is_file():
        return
    _write_minimal_linear_rag_index(_LINEAR_RAG_INDEX)
