"""Compile a GraphMetadata into a LangGraph StateGraph."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from langgraph.graph import END as LG_END
from langgraph.graph import StateGraph

from backend.runtime.state import WorkflowState

from .api import END as BUILDER_END
from .api import GraphMetadata
from .nodes import ApprovalNodeConfig, GateNodeConfig, LoopConfig, RouterNodeConfig


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
    router_dispatch_factory: Callable[[RouterNodeConfig], Callable[[WorkflowState], str]],
    approval_dispatch_factory: Callable[[ApprovalNodeConfig], Callable[[WorkflowState], str]],
):
    """Build a LangGraph StateGraph."""
    g = StateGraph(WorkflowState)

    for nid, cfg in metadata.nodes.items():
        g.add_node(nid, node_factory(cfg, metadata))

    g.set_entry_point(metadata.entry)

    conditional_ids = {
        nid
        for nid, cfg in metadata.nodes.items()
        if isinstance(cfg, (ApprovalNodeConfig, GateNodeConfig, RouterNodeConfig))
    }
    edges_by_target: dict[str, list[str]] = defaultdict(list)
    for source, target in metadata.edges:
        if source in conditional_ids:
            continue
        edges_by_target[target].append(source)

    for target, sources in edges_by_target.items():
        mapped_target = _map_end(target)
        if len(sources) == 1 or mapped_target == LG_END:
            for source in sources:
                g.add_edge(source, mapped_target)
        else:
            g.add_edge(sources, mapped_target)

    for loop in metadata.loops:
        src_cfg = metadata.nodes.get(loop.back_edge_from)
        if isinstance(src_cfg, (GateNodeConfig, RouterNodeConfig)):
            continue
        g.add_edge(loop.back_edge_from, _map_end(loop.back_edge_to))

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
        elif isinstance(cfg, RouterNodeConfig):
            dispatcher = router_dispatch_factory(cfg)
            route_map = {_map_end(target): _map_end(target) for target in cfg.routes.values()}
            if cfg.default_target is not None:
                route_map[_map_end(cfg.default_target)] = _map_end(cfg.default_target)
            g.add_conditional_edges(nid, dispatcher, route_map)
        elif isinstance(cfg, ApprovalNodeConfig):
            dispatcher = approval_dispatch_factory(cfg)
            g.add_conditional_edges(
                nid,
                dispatcher,
                {
                    _map_end(cfg.approved_target): _map_end(cfg.approved_target),
                    _map_end(cfg.rejected_target): _map_end(cfg.rejected_target),
                },
            )

    return g
