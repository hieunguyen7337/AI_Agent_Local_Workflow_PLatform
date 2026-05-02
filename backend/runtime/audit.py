"""Human-readable node audit files for workflow runs and dataset evals."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


@dataclass
class AuditRecorder:
    run_dir: Path
    run_id: str
    workflow: str
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def events_path(self) -> Path:
        return self.run_dir / "node_events.jsonl"

    @property
    def summary_path(self) -> Path:
        return self.run_dir / "audit.json"

    def write_event(self, event: dict[str, Any]) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "workflow": self.workflow,
            "recorded_ns": time.time_ns(),
            **self.context,
            **_jsonable(event),
        }
        with _lock_for(self.events_path):
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def write_summary(self, *, status: str, error: str | None, final_state: dict[str, Any]) -> None:
        payload = {
            "run_id": self.run_id,
            "workflow": self.workflow,
            "status": status,
            "error": error,
            "context": self.context,
            "final_state": _jsonable(final_state),
            "node_events": self.events_path.as_posix(),
        }
        with _lock_for(self.summary_path):
            self.summary_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def audit_preview(value: Any, *, max_items: int = 30, max_string: int = 4000) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_string else value[:max_string] + "...<truncated>"
    if isinstance(value, list):
        if len(value) > max_items:
            return [audit_preview(item, max_items=max_items, max_string=max_string) for item in value[:max_items]] + [
                f"...<{len(value) - max_items} more>"
            ]
        return [audit_preview(item, max_items=max_items, max_string=max_string) for item in value]
    if isinstance(value, dict):
        return {str(k): audit_preview(v, max_items=max_items, max_string=max_string) for k, v in value.items()}
    return _jsonable(value)


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)
