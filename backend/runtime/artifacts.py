"""Structured local run artifact paths."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

# Primary workflow artifact tree (``runs/workflows/...``). Server and tests may replace
# this module attribute to use a temp directory; keep in sync with API run resolution.
RUNS_ROOT = Path("runs")

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


def claude_code_yaml_workflow_runs_root() -> Path:
    """Dedicated artifact tree for ``claude_code*`` YAML workflows (stress/eval workspace).

    Override with env ``CLAUDE_CODE_RUNS_ROOT``. Defaults to
    ``<repo>/evals/claude_code_yaml_workflow/runs``.
    """
    env = os.environ.get("CLAUDE_CODE_RUNS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return (REPO_ROOT / "evals/claude_code_yaml_workflow/runs").resolve()


def default_runs_root_for_workflow(workflow_id: str) -> Path:
    """Where new runs are written: isolated tree for Claude Code YAML workflows."""
    if workflow_id.startswith("claude_code"):
        return claude_code_yaml_workflow_runs_root()
    return RUNS_ROOT


def run_search_roots_ordered() -> list[Path]:
    """Ordered search paths for locating an existing run by id (API / approvals)."""
    roots: list[Path] = []
    seen: set[str] = set()
    for raw in (RUNS_ROOT, claude_code_yaml_workflow_runs_root()):
        try:
            rp = raw.resolve()
        except OSError:
            continue
        key = str(rp)
        if key not in seen:
            seen.add(key)
            roots.append(rp)
    return roots


def resolve_run_dir(runs_root: Path, run_id: str) -> Path:
    workflows_root = runs_root / "workflows"
    if not workflows_root.exists():
        raise FileNotFoundError(f"run {run_id!r} not found")
    matches = list(workflows_root.glob(f"*/*/{run_id}"))
    for path in matches:
        if path.is_dir():
            return path
    raise FileNotFoundError(f"run {run_id!r} not found")


def resolve_run_dir_any(run_id: str) -> tuple[Path, Path]:
    """Find ``run_id`` under ``runs/`` or the Claude Code YAML eval runs root.

    Returns ``(run_dir, runs_root)`` where ``runs_root`` is the tree root used (parent of
    ``workflows/``), for continuation runs that must land in the same tree.
    """
    last: FileNotFoundError | None = None
    for runs_root in run_search_roots_ordered():
        try:
            return resolve_run_dir(runs_root, run_id), runs_root
        except FileNotFoundError as exc:
            last = exc
    raise FileNotFoundError(f"run {run_id!r} not found") from last


def iter_run_dirs(runs_root: Path) -> list[Path]:
    workflows_root = runs_root / "workflows"
    if not workflows_root.exists():
        return []
    return [path for path in workflows_root.glob("*/*/run_*") if path.is_dir()]


def iter_all_run_dirs() -> list[Path]:
    """All run directories under default ``runs/`` and Claude Code eval runs root."""
    out: list[Path] = []
    for root in run_search_roots_ordered():
        out.extend(iter_run_dirs(root))
    return out


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
