"""Subgraph node factory for nested local workflow execution."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from backend.builder.nodes import SubgraphNodeConfig
from backend.runtime.errors import SubgraphError
from backend.runtime.state import WorkflowState


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

        artifacts = dict(state.get("artifacts", {}))
        subgraphs = list(artifacts.get("subgraphs", []))
        lineage["artifact_path"] = lineage_path.as_posix()
        subgraphs.append(lineage)
        artifacts["subgraphs"] = subgraphs
        output_update["artifacts"] = artifacts

        if child_result.status != "ok":
            raise SubgraphError(cfg.id, child_result.run_id, child_result.status, child_result.error)
        return output_update

    _node.__name__ = f"subgraph_{cfg.id}"
    return _node
