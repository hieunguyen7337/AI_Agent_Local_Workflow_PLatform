"""Checkpoint store helpers. LangGraph's SqliteSaver owns the heavy lifting."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def list_checkpoints(run_dir: Path) -> list[dict]:
    """Return ordered list of checkpoint metadata for the given run."""
    db = run_dir / "checkpoints.db"
    if not db.exists():
        return []
    out = []
    with sqlite3.connect(db) as con:
        try:
            rows = con.execute(
                "SELECT thread_id, checkpoint_id, checkpoint_ns, type FROM checkpoints "
                "ORDER BY checkpoint_id ASC"
            ).fetchall()
            for r in rows:
                out.append(
                    {"thread_id": r[0], "checkpoint_id": r[1], "checkpoint_ns": r[2], "type": r[3]}
                )
        except sqlite3.OperationalError:
            return []
    return out
