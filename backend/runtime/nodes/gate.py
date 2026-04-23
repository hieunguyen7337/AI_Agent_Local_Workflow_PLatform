"""Gate node: registered as a passthrough in LangGraph, routes via conditional edges.

The gate's passthrough increments the loop iteration counter when routing to the
fail_target (which is the loop's back_edge_to). The router reads iteration counts
and raises MaxIterationsError before LangGraph re-enters the loop beyond the cap.
"""
from __future__ import annotations

from typing import Callable

from opentelemetry.trace import Status, StatusCode

from backend.builder.api import END as BUILDER_END
from backend.builder.nodes import GateNodeConfig, LoopConfig
from backend.runtime.errors import MaxIterationsError
from backend.runtime.state import WorkflowState
from backend.telemetry.genai_attrs import WORKFLOW_STATUS, node_attrs
from backend.telemetry.tracer import get_tracer


def make_gate_passthrough(
    cfg: GateNodeConfig,
    loop: LoopConfig | None,
    *,
    run_id: str,
    graph_name: str,
) -> Callable[[WorkflowState], dict]:
    """Increments iteration counter when the gate will route to the loop back-edge."""
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        verdict = state.get(cfg.verdict_state_key, False)
        attrs = node_attrs(
            run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind="gate"
        )
        attrs[WORKFLOW_STATUS] = "PASS" if verdict else "FAIL"
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            span.set_status(Status(StatusCode.OK))
            if not verdict and loop is not None:
                counts = dict(state.get("iteration_counts", {}))
                counts[loop.loop_id] = counts.get(loop.loop_id, 0) + 1
                return {"iteration_counts": counts}
            return {}

    _node.__name__ = f"gate_pass_{cfg.id}"
    return _node


def make_gate_router(
    cfg: GateNodeConfig,
    loop: LoopConfig | None,
) -> Callable[[WorkflowState], str]:
    """Returns the id of the next node. Raises MaxIterationsError if loop cap exceeded."""

    def _router(state: WorkflowState) -> str:
        verdict = state.get(cfg.verdict_state_key, False)
        if verdict:
            return _resolve(cfg.pass_target)
        if loop is not None:
            count = state.get("iteration_counts", {}).get(loop.loop_id, 0)
            if count > loop.max_iterations:
                raise MaxIterationsError(loop.loop_id, loop.max_iterations)
        return _resolve(cfg.fail_target)

    return _router


def _resolve(node_id: str) -> str:
    from langgraph.graph import END as LG_END

    return LG_END if node_id == BUILDER_END else node_id
