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
from backend.runtime.artifacts import resolve_run_dir, resolve_run_dir_any, update_run_manifest
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
    parent_run_id: str | None = None
    parent_workflow: str | None = None
    parent_subgraph_node_id: str | None = None
    parent_continuation_run_id: str | None = None
    parent_continuation_status: str | None = None
    parent_continuation_run_dir: Path | None = None
    parent_continuation_error: str | None = None
    parent_continuation_final_state: dict | None = None


def decide_approval(
    *,
    run_id: str,
    decision: DecisionValue,
    reviewer: str | None = None,
    comment: str | None = None,
    runs_root: Path | None = None,
    metadata: GraphMetadata | None = None,
) -> ApprovalDecisionResult:
    try:
        if runs_root is None:
            source_run_dir, runs_root = resolve_run_dir_any(run_id)
        else:
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

    # Check whether this child run was launched by a parent subgraph node.
    parent_run_id: str | None = None
    parent_workflow: str | None = None
    parent_subgraph_node_id: str | None = None
    parent_continuation: RunResult | None = None

    parent_run_path = source_run_dir / "parent_run.json"
    if parent_run_path.exists() and continuation.status == "ok":
        try:
            parent_run_info = json.loads(parent_run_path.read_text(encoding="utf-8"))
            parent_run_id = str(parent_run_info.get("parent_run_id") or "")
            parent_workflow = str(parent_run_info.get("parent_workflow") or "")
            parent_subgraph_node_id = str(parent_run_info.get("node_id") or "")
            outputs_map: dict[str, str] = parent_run_info.get("outputs") or {}

            if parent_run_id and parent_workflow and parent_subgraph_node_id:
                try:
                    parent_run_dir = resolve_run_dir(runs_root, parent_run_id)
                except FileNotFoundError:
                    parent_run_dir = resolve_run_dir_any(parent_run_id)[0]
                pending_path = parent_run_dir / "pending_subgraph_approval.json"
                if pending_path.exists():
                    pending = json.loads(pending_path.read_text(encoding="utf-8"))
                    state_snapshot_parent = dict(pending.get("state_snapshot") or {})

                    # Map child continuation outputs back into parent state keys.
                    parent_outputs = {
                        parent_key: continuation.final_state.get(child_key, "")
                        for child_key, parent_key in outputs_map.items()
                    }
                    parent_overrides = {
                        **state_snapshot_parent,
                        **parent_outputs,
                        "_subgraph_resume": {
                            parent_subgraph_node_id: {
                                "child_run_id": continuation.run_id,
                                "outputs": parent_outputs,
                            }
                        },
                        "pending_subgraph_approval": {},
                    }
                    parent_metadata = load_workflow_metadata(parent_workflow)
                    parent_continuation = run_graph(
                        parent_metadata,
                        user_input=str(state_snapshot_parent.get("user_input", "") or ""),
                        runs_root=runs_root,
                        initial_state_overrides=parent_overrides,
                        start_at_node=parent_subgraph_node_id,
                    )

                    # Write subgraph_resume.json into the parent continuation run dir.
                    subgraph_resume_artifact = {
                        "source_parent_run_id": parent_run_id,
                        "parent_workflow": parent_workflow,
                        "subgraph_node_id": parent_subgraph_node_id,
                        "child_run_id": run_id,
                        "child_continuation_run_id": continuation.run_id,
                        "decision": decision,
                        "created_ns": time.time_ns(),
                    }
                    (parent_continuation.run_dir / "subgraph_resume.json").write_text(
                        json.dumps(subgraph_resume_artifact, indent=2),
                        encoding="utf-8",
                    )
                    update_run_manifest(
                        parent_continuation.run_dir,
                        {"subgraph_resume": subgraph_resume_artifact},
                    )

                    # Write subgraph_decision.json into the source parent run dir.
                    subgraph_decision_artifact = {
                        "parent_run_id": parent_run_id,
                        "parent_workflow": parent_workflow,
                        "subgraph_node_id": parent_subgraph_node_id,
                        "child_run_id": run_id,
                        "child_continuation_run_id": continuation.run_id,
                        "decision": decision,
                        "parent_continuation_run_id": parent_continuation.run_id,
                        "parent_continuation_status": parent_continuation.status,
                        "parent_continuation_error": parent_continuation.error,
                        "created_ns": time.time_ns(),
                    }
                    (parent_run_dir / "subgraph_decision.json").write_text(
                        json.dumps(subgraph_decision_artifact, indent=2),
                        encoding="utf-8",
                    )
                    update_run_manifest(
                        parent_run_dir,
                        {"subgraph_decision": subgraph_decision_artifact},
                    )
        except (OSError, json.JSONDecodeError, FileNotFoundError):
            # Best-effort: if parent context is unreadable, skip parent fork.
            parent_continuation = None

    decision_artifact_data = {
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
    if parent_continuation is not None:
        decision_artifact_data["parent_continuation_run_id"] = parent_continuation.run_id
    decision_path.write_text(json.dumps(decision_artifact_data, indent=2), encoding="utf-8")
    update_run_manifest(source_run_dir, {"approval_decision": decision_artifact_data})

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
        parent_run_id=parent_run_id or None,
        parent_workflow=parent_workflow or None,
        parent_subgraph_node_id=parent_subgraph_node_id or None,
        parent_continuation_run_id=parent_continuation.run_id if parent_continuation else None,
        parent_continuation_status=parent_continuation.status if parent_continuation else None,
        parent_continuation_run_dir=parent_continuation.run_dir if parent_continuation else None,
        parent_continuation_error=parent_continuation.error if parent_continuation else None,
        parent_continuation_final_state=parent_continuation.final_state if parent_continuation else None,
    )


def _load_run_status(run_dir: Path) -> str | None:
    db = run_dir / "telemetry.db"
    if not db.exists():
        return None
    with sqlite3.connect(db) as con:
        row = con.execute("SELECT status FROM runs LIMIT 1").fetchone()
    return str(row[0]) if row else None
