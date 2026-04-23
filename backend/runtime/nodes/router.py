"""Router node: passthrough plus conditional dispatch on a state token."""
from __future__ import annotations

from typing import Callable

from opentelemetry.trace import Status, StatusCode

from backend.builder.api import END as BUILDER_END
from backend.builder.nodes import RouterNodeConfig
from backend.runtime.errors import RoutingError
from backend.runtime.state import WorkflowState
from backend.telemetry.genai_attrs import node_attrs
from backend.telemetry.tracer import get_tracer


def make_router_passthrough(
    cfg: RouterNodeConfig,
    *,
    run_id: str,
    graph_name: str,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        attrs = node_attrs(
            run_id=run_id,
            graph_name=graph_name,
            node_id=cfg.id,
            node_kind="router",
        )
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            span.set_status(Status(StatusCode.OK))
            return {}

    _node.__name__ = f"router_pass_{cfg.id}"
    return _node


def make_router_dispatcher(cfg: RouterNodeConfig) -> Callable[[WorkflowState], str]:
    def _router(state: WorkflowState) -> str:
        raw = str(state.get(cfg.route_state_key, "") or "")
        token = raw.strip().splitlines()[0].strip() if raw.strip() else ""
        if token in cfg.routes:
            return _resolve(cfg.routes[token])
        if cfg.default_target is not None:
            return _resolve(cfg.default_target)
        valid = ", ".join(sorted(cfg.routes.keys()))
        raise RoutingError(
            f"router {cfg.id!r} could not resolve route {token!r}; expected one of: {valid}"
        )

    return _router


def _resolve(node_id: str) -> str:
    from langgraph.graph import END as LG_END

    return LG_END if node_id == BUILDER_END else node_id
