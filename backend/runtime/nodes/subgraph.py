"""Subgraph node factory for nested local workflow execution."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from backend.builder.nodes import SubgraphNodeConfig
from backend.runtime.artifacts import update_run_manifest
from backend.runtime.errors import PendingSubgraphApprovalError, SubgraphError
from backend.runtime.state import WorkflowState


def _clean_state(state: WorkflowState) -> dict:
    excluded = {"messages", "artifacts"}
    return {
        key: value
        for key, value in dict(state).items()
        if key not in excluded and not key.startswith("_")
    }


def make_subgraph_node(
    cfg: SubgraphNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
    runs_root: Path,
    on_cost: Callable[[float], None],
) -> Callable[[WorkflowState], dict]:
    def _node(state: WorkflowState) -> dict:
        from backend.graphspec.loader import load_workflow_metadata
        from backend.runtime.executor import run_graph

        # Resume passthrough: parent was re-entered after child approval was decided.
        resume_marker = state.get("_subgraph_resume", {})
        if isinstance(resume_marker, dict) and cfg.id in resume_marker:
            marker = resume_marker[cfg.id]
            child_run_id = marker["child_run_id"]
            output_update = {
                parent_key: marker["outputs"].get(parent_key, "")
                for parent_key in cfg.outputs.values()
            }
            lineage = {
                "parent_run_id": run_id,
                "parent_workflow": graph_name,
                "node_id": cfg.id,
                "child_workflow": cfg.workflow,
                "child_run_id": child_run_id,
                "status": "ok",
                "resumed": True,
                "inputs": cfg.inputs,
                "outputs": cfg.outputs,
                "created_ns": time.time_ns(),
            }
            subgraph_dir = run_dir / "subgraphs"
            subgraph_dir.mkdir(parents=True, exist_ok=True)
            lineage_path = subgraph_dir / f"{cfg.id}_{child_run_id}.json"
            lineage_path.write_text(json.dumps(lineage, indent=2), encoding="utf-8")
            lineage["artifact_path"] = lineage_path.as_posix()

            artifacts = dict(state.get("artifacts", {}))
            subgraphs = list(artifacts.get("subgraphs", []))
            subgraphs.append(lineage)
            artifacts["subgraphs"] = subgraphs
            update_run_manifest(run_dir, {"subgraphs": subgraphs})
            output_update["artifacts"] = artifacts

            # Clear our own marker so downstream nodes don't see it.
            cleared = {k: v for k, v in resume_marker.items() if k != cfg.id}
            output_update["_subgraph_resume"] = cleared
            return output_update

        # Normal execution: launch the child workflow.
        child_metadata = load_workflow_metadata(cfg.workflow)
        child_state = {child_key: state.get(parent_key, "") for parent_key, child_key in cfg.inputs.items()}
        child_user_input = str(child_state.get("user_input", state.get("user_input", "")))
        child_result = run_graph(
            child_metadata,
            user_input=child_user_input,
            runs_root=runs_root,
            initial_state_overrides=child_state,
        )
        on_cost(child_result.cost_usd)

        # Child paused waiting for approval — delegate the pause to the parent.
        if child_result.status == "pending_approval":
            state_snapshot = _clean_state(state)
            pending_artifact = {
                "parent_run_id": run_id,
                "parent_workflow": graph_name,
                "node_id": cfg.id,
                "child_workflow": cfg.workflow,
                "child_run_id": child_result.run_id,
                "child_run_dir": child_result.run_dir.as_posix(),
                "inputs": cfg.inputs,
                "outputs": cfg.outputs,
                "state_snapshot": state_snapshot,
                "created_ns": time.time_ns(),
            }
            pending_path = run_dir / "pending_subgraph_approval.json"
            pending_path.write_text(json.dumps(pending_artifact, indent=2), encoding="utf-8")
            update_run_manifest(run_dir, {"pending_subgraph_approval": pending_artifact})

            # Link child → parent (parent_run.json in the child's run dir).
            lineage = {
                "parent_run_id": run_id,
                "parent_workflow": graph_name,
                "node_id": cfg.id,
                "child_workflow": cfg.workflow,
                "child_run_id": child_result.run_id,
                "status": "pending_approval",
                "inputs": cfg.inputs,
                "outputs": cfg.outputs,
                "created_ns": time.time_ns(),
            }
            child_lineage_path = child_result.run_dir / "parent_run.json"
            child_lineage_path.write_text(json.dumps(lineage, indent=2), encoding="utf-8")
            update_run_manifest(child_result.run_dir, {"parent_run": lineage})

            raise PendingSubgraphApprovalError(
                pending=pending_artifact,
                state_update={"pending_subgraph_approval": pending_artifact},
            )

        # Normal child completion.
        output_update = {
            parent_key: child_result.final_state.get(child_key, "")
            for child_key, parent_key in cfg.outputs.items()
        }
        lineage = {
            "parent_run_id": run_id,
            "parent_workflow": graph_name,
            "node_id": cfg.id,
            "child_workflow": cfg.workflow,
            "child_run_id": child_result.run_id,
            "status": child_result.status,
            "error": child_result.error,
            "cost_usd": child_result.cost_usd,
            "latency_ms": child_result.latency_ms,
            "inputs": cfg.inputs,
            "outputs": cfg.outputs,
            "created_ns": time.time_ns(),
        }
        subgraph_dir = run_dir / "subgraphs"
        subgraph_dir.mkdir(parents=True, exist_ok=True)
        lineage_path = subgraph_dir / f"{cfg.id}_{child_result.run_id}.json"
        lineage_path.write_text(json.dumps(lineage, indent=2), encoding="utf-8")

        child_lineage_path = child_result.run_dir / "parent_run.json"
        child_lineage_path.write_text(json.dumps(lineage, indent=2), encoding="utf-8")
        update_run_manifest(child_result.run_dir, {"parent_run": lineage})

        artifacts = dict(state.get("artifacts", {}))
        subgraphs = list(artifacts.get("subgraphs", []))
        lineage["artifact_path"] = lineage_path.as_posix()
        subgraphs.append(lineage)
        artifacts["subgraphs"] = subgraphs
        update_run_manifest(run_dir, {"subgraphs": subgraphs})
        output_update["artifacts"] = artifacts

        if child_result.status != "ok":
            raise SubgraphError(cfg.id, child_result.run_id, child_result.status, child_result.error)
        return output_update

    _node.__name__ = f"subgraph_{cfg.id}"
    return _node
