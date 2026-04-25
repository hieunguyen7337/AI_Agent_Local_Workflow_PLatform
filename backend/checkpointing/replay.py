"""Replay a run from a historical checkpoint snapshot into a forked new run."""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from backend.budget.enforcer import BudgetEnforcer
from backend.builder.api import GraphMetadata
from backend.builder.compile import compile_to_langgraph
from backend.graphspec import load_workflow_metadata
from backend.runtime.artifacts import resolve_run_dir, update_run_manifest
from backend.runtime.cancellation import CancellationController
from backend.runtime.errors import BuilderValidationError, ReplayError
from backend.runtime.executor import RunResult, run_graph
from backend.runtime.executor import (
    _approval_dispatch_factory,
    _gate_router_factory,
    _node_factory,
    _router_dispatch_factory,
)
from backend.runtime.state import new_state


@dataclass
class ReplayBoundary:
    replay_from_node: str
    source_checkpoint_id: str
    snapshot_state: dict[str, Any]


@dataclass
class ReplayResult:
    run: RunResult
    source_run_id: str
    replay_run_id: str
    replay_from_node: str
    source_checkpoint_id: str

    @property
    def run_dir(self) -> Path:
        return self.run.run_dir

    @property
    def final_state(self) -> dict:
        return self.run.final_state

    @property
    def status(self) -> str:
        return self.run.status

    @property
    def error(self) -> str | None:
        return self.run.error

    @property
    def cost_usd(self) -> float:
        return self.run.cost_usd

    @property
    def latency_ms(self) -> float:
        return self.run.latency_ms


def apply_metadata_overrides(metadata: GraphMetadata, overrides: dict[str, dict[str, Any]]) -> GraphMetadata:
    """Return metadata with node config overrides applied."""
    nodes = dict(metadata.nodes)
    for node_id, fields in overrides.items():
        if node_id not in nodes:
            raise BuilderValidationError(f"override target node {node_id!r} not in workflow")
        nodes[node_id] = nodes[node_id].model_copy(update=fields)
    return GraphMetadata(
        name=metadata.name,
        cost_budget_usd=metadata.cost_budget_usd,
        latency_budget_ms=metadata.latency_budget_ms,
        nodes=nodes,
        edges=list(metadata.edges),
        loops=list(metadata.loops),
        entry=metadata.entry,
    )


def parse_set_arg(set_args: list[str]) -> dict[str, dict[str, Any]]:
    """Parse ['coder.temperature=0.5', 'tester.max_retries=1'] into overrides dict.

    M1: one level deep only (node_id.field=value).
    """
    out: dict[str, dict[str, Any]] = {}
    for raw in set_args:
        if "=" not in raw:
            raise ValueError(f"--set expects key=value, got {raw!r}")
        key, value = raw.split("=", 1)
        if "." not in key:
            raise ValueError(f"--set expects node.field, got {key!r}")
        node_id, field = key.split(".", 1)
        if "." in field:
            raise ValueError(f"--set supports one level deep only (node.field); got {key!r}")
        out.setdefault(node_id, {})[field] = _coerce(value)
    return out


def _coerce(v: str) -> Any:
    lo = v.lower()
    if lo in ("true", "false"):
        return lo == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


@contextmanager
def _history_app(metadata: GraphMetadata, checkpoint_path: Path, *, run_id: str):
    enforcer = BudgetEnforcer(
        cost_budget_usd=metadata.cost_budget_usd,
        latency_budget_ms=metadata.latency_budget_ms,
    )
    compiled_sg = compile_to_langgraph(
        metadata,
        node_factory=_node_factory(
            enforcer,
            run_id=run_id,
            graph_name=metadata.name,
            run_dir=checkpoint_path.parent,
            runs_root=checkpoint_path.parents[4],
            cancellation=None,
        ),
        gate_router_factory=_gate_router_factory(run_id, metadata.name),
        router_dispatch_factory=_router_dispatch_factory(),
        approval_dispatch_factory=_approval_dispatch_factory(),
    )
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        yield compiled_sg.compile(checkpointer=saver)


def resolve_replay_boundary(
    metadata: GraphMetadata,
    *,
    source_run_id: str,
    at: str | None,
    runs_root: Path,
) -> ReplayBoundary:
    try:
        source_run_dir = resolve_run_dir(runs_root, source_run_id)
    except FileNotFoundError as exc:
        raise ReplayError(f"run {source_run_id!r} has no checkpoints.db") from exc
    checkpoint_path = source_run_dir / "checkpoints.db"
    if not checkpoint_path.exists():
        raise ReplayError(f"run {source_run_id!r} has no checkpoints.db")

    if at is not None and at not in metadata.node_ids():
        raise ReplayError(f"replay node {at!r} does not exist in workflow {metadata.name!r}")

    with _history_app(metadata, checkpoint_path, run_id=source_run_id) as app:
        history = list(app.get_state_history({"configurable": {"thread_id": source_run_id}}))

    if not history:
        raise ReplayError(f"run {source_run_id!r} has no checkpoint history")

    if at is None:
        for snapshot in reversed(history):
            values = snapshot.values if isinstance(snapshot.values, dict) else None
            if values and values.get("user_input"):
                checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
                return ReplayBoundary(
                    replay_from_node=metadata.entry,
                    source_checkpoint_id=checkpoint_id,
                    snapshot_state=dict(values),
                )
        raise ReplayError(f"run {source_run_id!r} has no populated checkpoint snapshot to replay")

    for snapshot in history:
        next_nodes = tuple(snapshot.next or ())
        if at in next_nodes:
            values = snapshot.values if isinstance(snapshot.values, dict) else None
            checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
            return ReplayBoundary(
                replay_from_node=at,
                source_checkpoint_id=checkpoint_id,
                snapshot_state=dict(values or {}),
            )

    raise ReplayError(
        f"run {source_run_id!r} has no checkpoint boundary that would execute node {at!r}"
    )


def migrate_snapshot_state(
    snapshot_state: dict[str, Any],
    *,
    source_run_id: str,
    replay_from_node: str,
    user_input: str,
    user_input_locked: bool,
) -> dict[str, Any]:
    migrated = dict(new_state(user_input))
    legacy: dict[str, Any] = {}

    for key, value in snapshot_state.items():
        if key == "user_input" and user_input_locked:
            continue
        if key in migrated:
            migrated[key] = value
        else:
            legacy[key] = value

    artifacts = dict(migrated.get("artifacts", {}) or {})
    if legacy:
        artifacts["_legacy_state"] = legacy
    migrated["artifacts"] = artifacts

    return migrated


def replay(
    *,
    workflow: str,
    run_id: str,
    user_input: str | None = None,
    expected: str | None = None,
    at: str | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
    runs_root: Path = Path("runs"),
    cancellation: CancellationController | None = None,
) -> ReplayResult:
    """Replay a run from a migrated snapshot into a new forked run."""
    metadata: GraphMetadata = load_workflow_metadata(workflow)
    if overrides:
        metadata = apply_metadata_overrides(metadata, overrides)
    boundary = resolve_replay_boundary(
        metadata,
        source_run_id=run_id,
        at=at,
        runs_root=runs_root,
    )

    effective_user_input = user_input or str(boundary.snapshot_state.get("user_input", "") or "")
    if not effective_user_input:
        raise ReplayError("replay could not determine user_input; provide --input explicitly")
    effective_expected = expected
    if effective_expected is None and "_expected" in boundary.snapshot_state:
        effective_expected = str(boundary.snapshot_state["_expected"])

    migrated_state = migrate_snapshot_state(
        boundary.snapshot_state,
        source_run_id=run_id,
        replay_from_node=boundary.replay_from_node,
        user_input=effective_user_input,
        user_input_locked=user_input is not None,
    )
    run = run_graph(
        metadata,
        user_input=effective_user_input,
        expected=effective_expected,
        runs_root=runs_root,
        initial_state_overrides=migrated_state,
        start_at_node=boundary.replay_from_node,
        cancellation=cancellation,
    )
    replay_meta = {
        "workflow": workflow,
        "source_run_id": run_id,
        "replay_run_id": run.run_id,
        "replay_from_node": boundary.replay_from_node,
        "source_checkpoint_id": boundary.source_checkpoint_id,
        "overrides": overrides or {},
    }
    (run.run_dir / "replay.json").write_text(json.dumps(replay_meta, indent=2), encoding="utf-8")
    update_run_manifest(run.run_dir, {"replay": replay_meta})
    return ReplayResult(
        run=run,
        source_run_id=run_id,
        replay_run_id=run.run_id,
        replay_from_node=boundary.replay_from_node,
        source_checkpoint_id=boundary.source_checkpoint_id,
    )
