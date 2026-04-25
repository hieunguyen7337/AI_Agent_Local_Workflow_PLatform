"""Approval decision and continuation helpers."""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backend.graphspec import load_workflow_metadata
from backend.builder.api import GraphMetadata
from backend.runtime.artifacts import resolve_run_dir, update_run_manifest
from backend.runtime.executor import RunResult, run_graph


DecisionValue = Literal["approved", "rejected"]


class ApprovalDecisionError(ValueError):
    pass


class ApprovalAlreadyDecidedError(ApprovalDecisionError):
    pass


class ApprovalNotFoundError(FileNotFoundError):
    pass


@dataclass
class ApprovalDecisionResult:
    source_run_id: str
    status: str
    decision: DecisionValue
    decision_artifact: Path
    continuation_run_id: str
    continuation_status: str
    continuation_run_dir: Path
    continuation_error: str | None
    continuation_final_state: dict
    continuation_cost_usd: float
    continuation_latency_ms: float


def decide_approval(
    *,
    run_id: str,
    decision: DecisionValue,
    reviewer: str | None = None,
    comment: str | None = None,
    runs_root: Path = Path("runs"),
    metadata: GraphMetadata | None = None,
) -> ApprovalDecisionResult:
    try:
        source_run_dir = resolve_run_dir(runs_root, run_id)
    except FileNotFoundError as exc:
        raise ApprovalNotFoundError(f"pending approval for run {run_id!r} not found") from exc
    approval_path = source_run_dir / "approval.json"
    decision_path = source_run_dir / "approval_decision.json"
    if not approval_path.exists():
        raise ApprovalNotFoundError(f"pending approval for run {run_id!r} not found")
    if decision_path.exists():
        raise ApprovalAlreadyDecidedError(f"approval for run {run_id!r} already has a decision")

    run_status = _load_run_status(source_run_dir)
    if run_status != "pending_approval":
        raise ApprovalDecisionError(f"run {run_id!r} is not pending approval")

    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    workflow = str(approval["workflow"])
    node_id = str(approval["node_id"])
    approval_state_key = str(approval["approval_state_key"])
    state_snapshot = dict(approval.get("state_snapshot") or {})
    state_snapshot[approval_state_key] = decision
    state_snapshot["pending_approval"] = {}

    user_input = str(state_snapshot.get("user_input", "") or "")
    if not user_input:
        raise ApprovalDecisionError("pending approval state snapshot is missing user_input")

    metadata = metadata or load_workflow_metadata(workflow)
    continuation = run_graph(
        metadata,
        user_input=user_input,
        runs_root=runs_root,
        initial_state_overrides=state_snapshot,
        start_at_node=node_id,
    )

    resume_artifact = {
        "source_run_id": run_id,
        "continuation_run_id": continuation.run_id,
        "workflow": workflow,
        "approval_node_id": node_id,
        "decision": decision,
        "approval_artifact": approval_path.as_posix(),
        "created_ns": time.time_ns(),
    }
    (continuation.run_dir / "approval_resume.json").write_text(
        json.dumps(resume_artifact, indent=2),
        encoding="utf-8",
    )
    update_run_manifest(continuation.run_dir, {"approval_resume": resume_artifact})

    decision_artifact = {
        "source_run_id": run_id,
        "workflow": workflow,
        "approval_node_id": node_id,
        "decision": decision,
        "reviewer": reviewer or "local-user",
        "comment": comment or "",
        "created_ns": time.time_ns(),
        "approval": approval,
        "continuation_run_id": continuation.run_id,
        "continuation_status": continuation.status,
        "continuation_error": continuation.error,
        "continuation_run_dir": continuation.run_dir.as_posix(),
    }
    decision_path.write_text(json.dumps(decision_artifact, indent=2), encoding="utf-8")
    update_run_manifest(source_run_dir, {"approval_decision": decision_artifact})

    return ApprovalDecisionResult(
        source_run_id=run_id,
        status="resumed",
        decision=decision,
        decision_artifact=decision_path,
        continuation_run_id=continuation.run_id,
        continuation_status=continuation.status,
        continuation_run_dir=continuation.run_dir,
        continuation_error=continuation.error,
        continuation_final_state=continuation.final_state,
        continuation_cost_usd=continuation.cost_usd,
        continuation_latency_ms=continuation.latency_ms,
    )


def _load_run_status(run_dir: Path) -> str | None:
    db = run_dir / "telemetry.db"
    if not db.exists():
        return None
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT status FROM runs LIMIT 1").fetchone()
    return str(row[0]) if row else None
