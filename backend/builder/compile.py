"""Compile a GraphMetadata into a LangGraph StateGraph.

Strategy:
- Each node config becomes a LangGraph node via a factory in backend.runtime.nodes.
- Plain edges become `add_edge`.
- Gate nodes become `add_conditional_edges` on the preceding node (the gate itself
  does no state mutation — it just routes). We model the gate as: predecessor node
  produces tester_verdict in state; gate's add_conditional_edges dispatches on that.
- Loops are represented by the gate's fail_target pointing at an earlier node. We
  enforce max_iterations in the gate's router by consulting state["iteration_counts"].
"""
from __future__ import annotations

from typing import Callable

from langgraph.graph import END as LG_END
from langgraph.graph import StateGraph

from backend.runtime.errors import MaxIterationsError
from backend.runtime.state import WorkflowState

from .api import END as BUILDER_END
from .api import GraphMetadata
from .nodes import GateNodeConfig, LLMNodeConfig, LoopConfig, TesterNodeConfig


def _map_end(node_id: str) -> str:
    return LG_END if node_id == BUILDER_END else node_id


def _find_loop_for_gate(loops: list[LoopConfig], gate_fail_target: str) -> LoopConfig | None:
    """Gate's fail_target points at the loop's back_edge_to."""
    for lp in loops:
        if lp.back_edge_to == gate_fail_target:
            return lp
    return None


def compile_to_langgraph(
    metadata: GraphMetadata,
    *,
    node_factory: Callable[..., Callable[[WorkflowState], WorkflowState]],
    gate_router_factory: Callable[[GateNodeConfig, LoopConfig | None], Callable[[WorkflowState], str]],
):
    """Build a LangGraph StateGraph.

    node_factory(cfg, metadata) -> callable that reads WorkflowState and returns partial updates.
    gate_router_factory(gate_cfg, loop_cfg_or_none) -> callable that reads WorkflowState and returns target node id.
    """
    g = StateGraph(WorkflowState)

    for nid, cfg in metadata.nodes.items():
        # Every node (including gate) is built through the factory. For gates the factory
        # returns a passthrough that increments the loop counter when routing back.
        g.add_node(nid, node_factory(cfg, metadata))

    g.set_entry_point(metadata.entry)

    # Forward edges — all except those emanating from gate nodes.
    gate_ids = {nid for nid, cfg in metadata.nodes.items() if isinstance(cfg, GateNodeConfig)}
    for s, t in metadata.edges:
        if s in gate_ids:
            continue  # handled by conditional edges below
        g.add_edge(s, _map_end(t))

    # Conditional edges from gate nodes.
    for nid, cfg in metadata.nodes.items():
        if isinstance(cfg, GateNodeConfig):
            loop_cfg = _find_loop_for_gate(metadata.loops, cfg.fail_target)
            router = gate_router_factory(cfg, loop_cfg)
            g.add_conditional_edges(
                nid,
                router,
                {
                    _map_end(cfg.pass_target): _map_end(cfg.pass_target),
                    _map_end(cfg.fail_target): _map_end(cfg.fail_target),
                },
            )

    return g


