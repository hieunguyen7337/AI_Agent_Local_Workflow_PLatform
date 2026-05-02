"""Structured local run artifact paths."""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

AEST = timezone(timedelta(hours=10), name="AEST")
AEST_UTC_OFFSET = "+10:00"
RUN_ID_RE = re.compile(
    r"^run_(?:\d{8}T\d{6}Z|"
    r"\d{4}_\d{2}_\d{2}_\d{2}h\d{2}m\d{2}s_AEST)_[a-zA-Z0-9_]+_[0-9a-f]{8}$"
)


def aest_now() -> datetime:
    return datetime.now(AEST)


def format_aest_timestamp(current: datetime | None = None) -> str:
    value = current or aest_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=AEST)
    value = value.astimezone(AEST)
    return value.strftime("%Y_%m_%d_%Hh%Mm%Ss")


def make_run_id(workflow: str, *, now: datetime | None = None) -> str:
    timestamp = format_aest_timestamp(now)
    safe_workflow = re.sub(r"[^a-zA-Z0-9_]+", "_", workflow).strip("_") or "workflow"
    return f"run_{timestamp}_AEST_{safe_workflow}_{uuid.uuid4().hex[:8]}"


def make_eval_id(workflow: str, *, suffix: str = "", now: datetime | None = None) -> str:
    safe_workflow = re.sub(r"[^a-zA-Z0-9_]+", "_", workflow).strip("_") or "workflow"
    safe_suffix = re.sub(r"[^a-zA-Z0-9_]+", "_", suffix).strip("_")
    parts = [format_aest_timestamp(now), "AEST", safe_workflow]
    if safe_suffix:
        parts.append(safe_suffix)
    return "_".join(parts)


def run_date_from_id(run_id: str) -> str | None:
    explicit = re.match(r"^run_(\d{4})_(\d{2})_(\d{2})_\d{2}h\d{2}m\d{2}s_AEST_", run_id)
    if explicit:
        return "".join(explicit.groups())
    parts = run_id.split("_", 3)
    if len(parts) < 2:
        return None
    timestamp = parts[1]
    if len(timestamp) < 8:
        return None
    return timestamp[:8]


def run_dir_for_id(runs_root: Path, workflow: str, run_id: str) -> Path:
    run_date = run_date_from_id(run_id) or datetime.now(timezone.utc).strftime("%Y%m%d")
    return runs_root / "workflows" / workflow / run_date / run_id


def resolve_run_dir(runs_root: Path, run_id: str) -> Path:
    workflows_root = runs_root / "workflows"
    if not workflows_root.exists():
        raise FileNotFoundError(f"run {run_id!r} not found")
    matches = list(workflows_root.glob(f"*/*/{run_id}"))
    for path in matches:
        if path.is_dir():
            return path
    raise FileNotFoundError(f"run {run_id!r} not found")


def iter_run_dirs(runs_root: Path) -> list[Path]:
    workflows_root = runs_root / "workflows"
    if not workflows_root.exists():
        return []
    return [path for path in workflows_root.glob("*/*/run_*") if path.is_dir()]


def artifact_paths(run_dir: Path) -> dict[str, str]:
    return {
        "run_dir": run_dir.as_posix(),
        "telemetry_db": (run_dir / "telemetry.db").as_posix(),
        "checkpoints_db": (run_dir / "checkpoints.db").as_posix(),
        "spans_jsonl": (run_dir / "spans.jsonl").as_posix(),
        "manifest": (run_dir / "run_manifest.json").as_posix(),
        "audit": (run_dir / "audit.json").as_posix(),
        "node_events": (run_dir / "node_events.jsonl").as_posix(),
    }


def write_run_manifest(
    run_dir: Path,
    *,
    workflow: str,
    run_id: str,
    started_ns: int,
    status: str,
    ended_ns: int | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    manifest = {
        "workflow": workflow,
        "run_id": run_id,
        "started_ns": started_ns,
        "ended_ns": ended_ns,
        "status": status,
        "error": error,
        "timezone": "AEST",
        "utc_offset": AEST_UTC_OFFSET,
        "artifacts": artifact_paths(run_dir),
    }
    if extra:
        manifest.update(extra)
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )


def update_run_manifest(run_dir: Path, updates: dict[str, Any]) -> None:
    path = run_dir / "run_manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
    manifest.update(updates)
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
