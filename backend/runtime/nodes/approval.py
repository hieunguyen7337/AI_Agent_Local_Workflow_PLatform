"""Human approval interrupt node."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from opentelemetry.trace import Status, StatusCode

from backend.builder.api import END as BUILDER_END
from backend.builder.nodes import ApprovalNodeConfig
from backend.runtime.errors import PendingApprovalError, RoutingError
from backend.runtime.state import WorkflowState
from backend.telemetry.genai_attrs import node_attrs
from backend.telemetry.tracer import get_tracer


def make_approval_node(
    cfg: ApprovalNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        if _has_decision(state, cfg.approval_state_key):
            return {}
        created_ns = time.time_ns()
        approval = {
            "workflow": graph_name,
            "run_id": run_id,
            "node_id": cfg.id,
            "prompt": cfg.prompt,
            "approval_state_key": cfg.approval_state_key,
            "approved_target": cfg.approved_target,
            "rejected_target": cfg.rejected_target,
            "created_ns": created_ns,
            "state_snapshot": _review_state(state),
        }
        update = {
            "pending_approval": {
                key: value for key, value in approval.items() if key != "state_snapshot"
            }
        }
        attrs = node_attrs(
            run_id=run_id,
            graph_name=graph_name,
            node_id=cfg.id,
            node_kind="approval",
        )
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            _write_approval(run_dir, approval)
            span.set_status(Status(StatusCode.OK))
            raise PendingApprovalError(approval=approval, state_update=update)

    _node.__name__ = f"approval_{cfg.id}"
    return _node


def make_approval_dispatcher(cfg: ApprovalNodeConfig) -> Callable[[WorkflowState], str]:
    def _router(state: WorkflowState) -> str:
        raw = str(state.get(cfg.approval_state_key, "") or "").strip().lower()
        if raw in {"approved", "approve", "pass", "true", "yes"}:
            return _resolve(cfg.approved_target)
        if raw in {"rejected", "reject", "fail", "false", "no"}:
            return _resolve(cfg.rejected_target)
        raise RoutingError(
            f"approval {cfg.id!r} has no decision in state key {cfg.approval_state_key!r}"
        )

    return _router


def _write_approval(run_dir: Path, approval: dict) -> None:
    (run_dir / "approval.json").write_text(json.dumps(approval, indent=2), encoding="utf-8")


def _has_decision(state: WorkflowState, approval_state_key: str) -> bool:
    raw = str(state.get(approval_state_key, "") or "").strip().lower()
    return raw in {
        "approved",
        "approve",
        "pass",
        "true",
        "yes",
        "rejected",
        "reject",
        "fail",
        "false",
        "no",
    }


def _review_state(state: WorkflowState) -> dict:
    excluded = {"messages", "artifacts"}
    return {
        key: value
        for key, value in dict(state).items()
        if key not in excluded and not key.startswith("_")
    }


def _resolve(node_id: str) -> str:
    from langgraph.graph import END as LG_END

    return LG_END if node_id == BUILDER_END else node_id
