"""Replay a run from its checkpoint with scalar node-config overrides.

Strategy: rebuild the graph via the workflow's builder (so node IDs stay stable),
apply overrides on node configs BEFORE compilation, then reuse the run's thread_id
against the same checkpoint DB. LangGraph resumes from the latest checkpoint.

M1 restriction: overrides are scalar fields on NodeConfig subclasses
(prompt text, model name, temperature, max_retries). Changing state schema is not
supported and will typically break the replay.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from backend.builder.api import GraphBuilder, GraphMetadata
from backend.runtime.errors import BuilderValidationError
from backend.runtime.executor import RunResult, run_graph


def load_workflow(name: str) -> GraphBuilder:
    module = importlib.import_module(f"backend.workflows.{name}")
    if not hasattr(module, "build"):
        raise BuilderValidationError(f"workflow {name!r} has no build() function")
    return module.build()


def apply_overrides(builder: GraphBuilder, overrides: dict[str, dict[str, Any]]) -> None:
    """overrides = {node_id: {field: value, ...}}. Mutates builder in place."""
    for node_id, fields in overrides.items():
        if node_id not in builder._nodes:
            raise BuilderValidationError(f"override target node {node_id!r} not in workflow")
        cfg = builder._nodes[node_id]
        updated = cfg.model_copy(update=fields)
        builder._nodes[node_id] = updated


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


def replay(
    *,
    workflow: str,
    run_id: str,
    user_input: str,
    expected: str | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
    runs_root: Path = Path("runs"),
) -> RunResult:
    """Replay a run against its checkpoint DB with optional node-config overrides.

    Uses the same run_id so the checkpoint DB is reused. LangGraph's SqliteSaver
    resumes from the latest checkpoint for the thread.
    """
    builder = load_workflow(workflow)
    if overrides:
        apply_overrides(builder, overrides)
    metadata: GraphMetadata = builder.compile()

    # Re-use the original run's run_id so run_dir == original run_dir, and thread_id matches.
    return run_graph(
        metadata,
        user_input=user_input,
        expected=expected,
        runs_root=runs_root,
        run_id=run_id,
        thread_id=run_id,
    )
