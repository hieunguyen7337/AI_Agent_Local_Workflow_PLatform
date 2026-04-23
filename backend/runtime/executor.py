"""Run a compiled graph end-to-end: wires tracer, budget, checkpointer, node factories."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from backend.budget.enforcer import BudgetEnforcer
from backend.builder.api import GraphMetadata
from backend.builder.compile import compile_to_langgraph
from backend.builder.nodes import (
    GateNodeConfig,
    LLMNodeConfig,
    LoopConfig,
    NodeConfig,
    RetrieverNodeConfig,
    TesterNodeConfig,
)
from backend.runtime.errors import BudgetExceededError, WorkflowError
from backend.runtime.nodes.gate import make_gate_passthrough, make_gate_router
from backend.runtime.nodes.llm import make_llm_node
from backend.runtime.nodes.retriever import make_retriever_node
from backend.runtime.nodes.tester import make_tester_node
from backend.runtime.state import WorkflowState, new_state
from backend.telemetry.exporter import record_run_end, record_run_start
from backend.telemetry.genai_attrs import (
    WORKFLOW_COST_USD,
    WORKFLOW_GRAPH_NAME,
    WORKFLOW_LATENCY_MS,
    WORKFLOW_RUN_ID,
    WORKFLOW_STATUS,
)
from backend.telemetry.tracer import init_tracer

RUNS_ROOT = Path("runs")


@dataclass
class RunResult:
    run_id: str
    graph_name: str
    final_state: dict
    status: str  # "ok", "budget_exceeded", "max_iterations", "error"
    error: str | None
    cost_usd: float
    latency_ms: float
    run_dir: Path


def _node_factory(enforcer: BudgetEnforcer, *, run_id: str, graph_name: str):
    def _on_cost(usd: float) -> None:
        enforcer.add_cost(usd)

    def _make(cfg: NodeConfig, metadata: GraphMetadata):
        if isinstance(cfg, LLMNodeConfig):
            return make_llm_node(cfg, run_id=run_id, graph_name=graph_name, on_cost=_on_cost)
        if isinstance(cfg, TesterNodeConfig):
            return make_tester_node(cfg, run_id=run_id, graph_name=graph_name, on_cost=_on_cost)
        if isinstance(cfg, RetrieverNodeConfig):
            return make_retriever_node(cfg, run_id=run_id, graph_name=graph_name)
        if isinstance(cfg, GateNodeConfig):
            loop = _find_loop(metadata, cfg.fail_target)
            return make_gate_passthrough(cfg, loop, run_id=run_id, graph_name=graph_name)
        raise TypeError(f"Unknown node kind: {type(cfg).__name__}")

    return _make


def _gate_router_factory(run_id: str, graph_name: str):
    def _make(cfg: GateNodeConfig, loop: LoopConfig | None):
        base = make_gate_router(cfg, loop)

        def _router(state: WorkflowState) -> str:
            return base(state)

        return _router

    return _make


def _find_loop(metadata: GraphMetadata, fail_target: str) -> LoopConfig | None:
    for lp in metadata.loops:
        if lp.back_edge_to == fail_target:
            return lp
    return None


def run_graph(
    metadata: GraphMetadata,
    *,
    user_input: str,
    expected: str | None = None,
    runs_root: Path = RUNS_ROOT,
    run_id: str | None = None,
    thread_id: str | None = None,
    initial_state_overrides: dict[str, Any] | None = None,
    recursion_limit: int = 50,
) -> RunResult:
    run_id = run_id or f"run_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    tracer = init_tracer(run_dir)

    enforcer = BudgetEnforcer(
        cost_budget_usd=metadata.cost_budget_usd,
        latency_budget_ms=metadata.latency_budget_ms,
    )
    enforcer.start()

    started_ns = time.time_ns()
    record_run_start(
        run_dir / "telemetry.db",
        run_id=run_id,
        graph_name=metadata.name,
        started_ns=started_ns,
    )

    compiled_sg = compile_to_langgraph(
        metadata,
        node_factory=_node_factory(enforcer, run_id=run_id, graph_name=metadata.name),
        gate_router_factory=_gate_router_factory(run_id, metadata.name),
    )

    checkpoint_path = run_dir / "checkpoints.db"
    # SqliteSaver.from_conn_string is a context manager; we enter it for the duration.
    with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        app = compiled_sg.compile(checkpointer=saver)

        state: WorkflowState = new_state(user_input)
        if expected is not None:
            state["_expected"] = expected  # consumed by tester
        if initial_state_overrides:
            state.update(initial_state_overrides)

        thread_id = thread_id or run_id
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}

        status = "ok"
        error: str | None = None
        final_state: dict = dict(state)

        root_attrs = {
            WORKFLOW_RUN_ID: run_id,
            WORKFLOW_GRAPH_NAME: metadata.name,
        }
        with tracer.start_as_current_span(f"run.{metadata.name}", attributes=root_attrs) as span:
            try:
                # Budget enforcement is an intentional node-boundary check: after each
                # completed step update, before the next node is dispatched.
                last_state = final_state
                for chunk in app.stream(state, config=config):
                    for _node_id, update in chunk.items():
                        if isinstance(update, dict):
                            last_state.update(update)
                    try:
                        enforcer.check()
                    except BudgetExceededError as be:
                        status = "budget_exceeded"
                        error = str(be)
                        span.set_attribute(WORKFLOW_STATUS, status)
                        raise
                # Read back the checkpointed final state for completeness.
                try:
                    final_snapshot = app.get_state(config)
                    if final_snapshot and final_snapshot.values:
                        last_state.update(final_snapshot.values)
                except Exception:
                    pass
                final_state = last_state
            except BudgetExceededError:
                pass
            except WorkflowError as we:
                status = we.__class__.__name__
                error = str(we)
                span.set_attribute(WORKFLOW_STATUS, status)
            except Exception as e:
                status = "error"
                error = f"{type(e).__name__}: {e}"
                span.set_attribute(WORKFLOW_STATUS, status)

            span.set_attribute(WORKFLOW_COST_USD, enforcer.cost_accum_usd)
            span.set_attribute(WORKFLOW_LATENCY_MS, enforcer.latency_ms())

        ended_ns = time.time_ns()
        record_run_end(
            run_dir / "telemetry.db",
            run_id=run_id,
            ended_ns=ended_ns,
            status=status,
            cost_usd=enforcer.cost_accum_usd,
            latency_ms=enforcer.latency_ms(),
            error=error,
        )

    return RunResult(
        run_id=run_id,
        graph_name=metadata.name,
        final_state=final_state,
        status=status,
        error=error,
        cost_usd=enforcer.cost_accum_usd,
        latency_ms=enforcer.latency_ms(),
        run_dir=run_dir,
    )
