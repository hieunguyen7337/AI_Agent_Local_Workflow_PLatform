"""Run a compiled graph end-to-end: wires tracer, budget, checkpointer, node factories."""
from __future__ import annotations

import time
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from backend.budget.enforcer import BudgetEnforcer
from backend.builder.api import GraphMetadata
from backend.builder.compile import compile_to_langgraph
from backend.builder.nodes import (
    AgentContextNodeConfig,
    AgentModelNodeConfig,
    AgentResponseParserNodeConfig,
    AgentStartupNodeConfig,
    ApprovalNodeConfig,
    ContextCompactorNodeConfig,
    EmbeddingNodeConfig,
    GateNodeConfig,
    HookRunnerNodeConfig,
    LLMNodeConfig,
    LoopConfig,
    MemoryWriterNodeConfig,
    NodeConfig,
    PermissionGateNodeConfig,
    PythonToolNodeConfig,
    RetrieverNodeConfig,
    RouterNodeConfig,
    SubagentContextNodeConfig,
    SubagentJoinNodeConfig,
    SubagentOrchestratorNodeConfig,
    SubagentPlanNodeConfig,
    SubagentSpawnNodeConfig,
    SubagentSummarizeNodeConfig,
    SubgraphNodeConfig,
    TesterNodeConfig,
    ToolExecutorNodeConfig,
    VectorRetrieverNodeConfig,
)
from backend.runtime.cancellation import CancellationController
from backend.runtime.audit import AuditRecorder
from backend.runtime.artifacts import make_run_id, run_dir_for_id, write_run_manifest
from backend.runtime.errors import (
    BudgetExceededError,
    BuilderValidationError,
    CancelledError,
    MaxIterationsError,
    PendingApprovalError,
    PendingSubgraphApprovalError,
    WorkflowError,
)
from backend.runtime.nodes.agent import (
    make_agent_context_node,
    make_agent_model_node,
    make_agent_response_parser_node,
    make_agent_startup_node,
    make_context_compactor_node,
    make_hook_runner_node,
    make_memory_writer_node,
    make_permission_gate_node,
    make_subagent_context_node,
    make_subagent_join_node,
    make_subagent_orchestrator_node,
    make_subagent_plan_node,
    make_subagent_spawn_node,
    make_subagent_summarize_node,
    make_tool_executor_node,
)
from backend.runtime.nodes.approval import make_approval_dispatcher, make_approval_node
from backend.runtime.nodes.embedding import make_embedding_node
from backend.runtime.nodes.gate import make_gate_passthrough, make_gate_router
from backend.runtime.nodes.llm import make_llm_node
from backend.runtime.nodes.python_tool import make_python_tool_node
from backend.runtime.nodes.retriever import make_retriever_node
from backend.runtime.nodes.router import make_router_dispatcher, make_router_passthrough
from backend.runtime.nodes.subgraph import make_subgraph_node
from backend.runtime.nodes.tester import make_tester_node
from backend.runtime.nodes.vector_retriever import make_vector_retriever_node
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
    status: str  # "ok", "budget_exceeded", "max_iterations", "cancelled", "error"
    error: str | None
    cost_usd: float
    latency_ms: float
    run_dir: Path


def _node_factory(
    enforcer: BudgetEnforcer,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
    runs_root: Path,
    cancellation: CancellationController | None,
    audit: AuditRecorder | None = None,
):
    def _on_cost(usd: float) -> None:
        enforcer.add_cost(usd)

    def _make(cfg: NodeConfig, metadata: GraphMetadata):
        base = None
        if isinstance(cfg, AgentStartupNodeConfig):
            base = make_agent_startup_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                run_dir=run_dir,
                audit=audit,
            )
        elif isinstance(cfg, AgentContextNodeConfig):
            base = make_agent_context_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                audit=audit,
            )
        elif isinstance(cfg, AgentModelNodeConfig):
            base = make_agent_model_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                run_dir=run_dir,
                on_cost=_on_cost,
                cancellation=cancellation,
                audit=audit,
            )
        elif isinstance(cfg, AgentResponseParserNodeConfig):
            base = make_agent_response_parser_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                audit=audit,
            )
        elif isinstance(cfg, PermissionGateNodeConfig):
            base = make_permission_gate_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                run_dir=run_dir,
                audit=audit,
            )
        elif isinstance(cfg, HookRunnerNodeConfig):
            base = make_hook_runner_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                audit=audit,
            )
        elif isinstance(cfg, ToolExecutorNodeConfig):
            base = make_tool_executor_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                run_dir=run_dir,
                audit=audit,
            )
        elif isinstance(cfg, ContextCompactorNodeConfig):
            base = make_context_compactor_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                run_dir=run_dir,
                audit=audit,
            )
        elif isinstance(cfg, SubagentOrchestratorNodeConfig):
            base = make_subagent_orchestrator_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                audit=audit,
            )
        elif isinstance(cfg, SubagentPlanNodeConfig):
            base = make_subagent_plan_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                audit=audit,
            )
        elif isinstance(cfg, SubagentContextNodeConfig):
            base = make_subagent_context_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                audit=audit,
            )
        elif isinstance(cfg, SubagentSpawnNodeConfig):
            base = make_subagent_spawn_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                run_dir=run_dir,
                runs_root=runs_root,
                on_cost=_on_cost,
                audit=audit,
            )
        elif isinstance(cfg, SubagentJoinNodeConfig):
            base = make_subagent_join_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                audit=audit,
            )
        elif isinstance(cfg, SubagentSummarizeNodeConfig):
            base = make_subagent_summarize_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                run_dir=run_dir,
                audit=audit,
            )
        elif isinstance(cfg, MemoryWriterNodeConfig):
            base = make_memory_writer_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                run_dir=run_dir,
                audit=audit,
            )
        elif isinstance(cfg, ApprovalNodeConfig):
            return make_approval_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                run_dir=run_dir,
            )
        elif isinstance(cfg, LLMNodeConfig):
            base = make_llm_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                on_cost=_on_cost,
                cancellation=cancellation,
                audit=audit,
            )
        elif isinstance(cfg, EmbeddingNodeConfig):
            base = make_embedding_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                on_cost=_on_cost,
                cancellation=cancellation,
                audit=audit,
            )
        elif isinstance(cfg, TesterNodeConfig):
            base = make_tester_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                on_cost=_on_cost,
                cancellation=cancellation,
            )
        elif isinstance(cfg, RetrieverNodeConfig):
            base = make_retriever_node(cfg, run_id=run_id, graph_name=graph_name, audit=audit)
        elif isinstance(cfg, VectorRetrieverNodeConfig):
            base = make_vector_retriever_node(cfg, run_id=run_id, graph_name=graph_name, audit=audit)
        elif isinstance(cfg, GateNodeConfig):
            loop = _find_loop(metadata, cfg.fail_target)
            return make_gate_passthrough(cfg, loop, run_id=run_id, graph_name=graph_name)
        elif isinstance(cfg, RouterNodeConfig):
            return make_router_passthrough(cfg, run_id=run_id, graph_name=graph_name)
        elif isinstance(cfg, SubgraphNodeConfig):
            base = make_subgraph_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                run_dir=run_dir,
                runs_root=runs_root,
                on_cost=_on_cost,
            )
        elif isinstance(cfg, PythonToolNodeConfig):
            base = make_python_tool_node(
                cfg,
                run_id=run_id,
                graph_name=graph_name,
                cancellation=cancellation,
                audit=audit,
            )
        else:
            raise TypeError(f"Unknown node kind: {type(cfg).__name__}")

        loop = _find_loop_from_source(metadata, cfg.id)
        if loop is not None:
            return _wrap_with_loop_counter(base, loop)
        return base

    return _make


def _gate_router_factory(run_id: str, graph_name: str):
    def _make(cfg: GateNodeConfig, loop: LoopConfig | None):
        base = make_gate_router(cfg, loop)

        def _router(state: WorkflowState) -> str:
            return base(state)

        return _router

    return _make


def _router_dispatch_factory():
    def _make(cfg: RouterNodeConfig):
        return make_router_dispatcher(cfg)

    return _make


def _approval_dispatch_factory():
    def _make(cfg: ApprovalNodeConfig):
        return make_approval_dispatcher(cfg)

    return _make


def _find_loop(metadata: GraphMetadata, fail_target: str) -> LoopConfig | None:
    for lp in metadata.loops:
        if lp.back_edge_to == fail_target:
            return lp
    return None


def _find_loop_from_source(metadata: GraphMetadata, source: str) -> LoopConfig | None:
    for lp in metadata.loops:
        if lp.back_edge_from == source:
            return lp
    return None


def _wrap_with_loop_counter(base_node, loop: LoopConfig):
    def _node(state: WorkflowState) -> dict:
        update = dict(base_node(state) or {})
        counts = dict(state.get("iteration_counts", {}))
        next_count = counts.get(loop.loop_id, 0) + 1
        if next_count > loop.max_iterations:
            raise MaxIterationsError(loop.loop_id, loop.max_iterations)
        counts[loop.loop_id] = next_count
        update["iteration_counts"] = counts
        return update

    _node.__name__ = getattr(base_node, "__name__", "loop_wrapped_node")
    return _node


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
    start_at_node: str | None = None,
    cancellation: CancellationController | None = None,
    run_dir_override: Path | None = None,
    use_checkpointer: bool = True,
    write_manifest_files: bool = True,
    write_audit_summary: bool = True,
    audit_context: dict[str, Any] | None = None,
) -> RunResult:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    run_id = run_id or make_run_id(metadata.name)
    run_dir = run_dir_override or run_dir_for_id(runs_root, metadata.name, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    audit = AuditRecorder(run_dir=run_dir, run_id=run_id, workflow=metadata.name, context=audit_context or {})

    tracer = init_tracer(run_dir, run_id=run_id)

    enforcer = BudgetEnforcer(
        cost_budget_usd=metadata.cost_budget_usd,
        latency_budget_ms=metadata.latency_budget_ms,
    )
    enforcer.start()

    started_ns = time.time_ns()
    if write_manifest_files:
        write_run_manifest(
            run_dir,
            workflow=metadata.name,
            run_id=run_id,
            started_ns=started_ns,
            status="running",
        )
    record_run_start(
        run_dir / "telemetry.db",
        run_id=run_id,
        graph_name=metadata.name,
        started_ns=started_ns,
    )

    if start_at_node is not None:
        if start_at_node not in metadata.node_ids():
            raise BuilderValidationError(f"start_at_node {start_at_node!r} not in workflow")
        metadata = replace(metadata, entry=start_at_node)

    compiled_sg = compile_to_langgraph(
        metadata,
        node_factory=_node_factory(
            enforcer,
            run_id=run_id,
            graph_name=metadata.name,
            run_dir=run_dir,
            runs_root=runs_root,
            cancellation=cancellation,
            audit=audit,
        ),
        gate_router_factory=_gate_router_factory(run_id, metadata.name),
        router_dispatch_factory=_router_dispatch_factory(),
        approval_dispatch_factory=_approval_dispatch_factory(),
    )

    checkpoint_path = run_dir / "checkpoints.db"
    saver_context = SqliteSaver.from_conn_string(str(checkpoint_path)) if use_checkpointer else None

    if saver_context is None:
        app = compiled_sg.compile()
        return _run_compiled_app(
            app,
            metadata=metadata,
            run_id=run_id,
            run_dir=run_dir,
            user_input=user_input,
            expected=expected,
            initial_state_overrides=initial_state_overrides,
            thread_id=thread_id,
            recursion_limit=recursion_limit,
            cancellation=cancellation,
            enforcer=enforcer,
            started_ns=started_ns,
            write_manifest_files=write_manifest_files,
            write_audit_summary=write_audit_summary,
            audit=audit,
        )

    # SqliteSaver.from_conn_string is a context manager; we enter it for the duration.
    with saver_context as saver:
        app = compiled_sg.compile(checkpointer=saver)
        return _run_compiled_app(
            app,
            metadata=metadata,
            run_id=run_id,
            run_dir=run_dir,
            user_input=user_input,
            expected=expected,
            initial_state_overrides=initial_state_overrides,
            thread_id=thread_id,
            recursion_limit=recursion_limit,
            cancellation=cancellation,
            enforcer=enforcer,
            started_ns=started_ns,
            write_manifest_files=write_manifest_files,
            write_audit_summary=write_audit_summary,
            audit=audit,
        )


def _run_compiled_app(
    app,
    *,
    metadata: GraphMetadata,
    run_id: str,
    run_dir: Path,
    user_input: str,
    expected: str | None,
    initial_state_overrides: dict[str, Any] | None,
    thread_id: str | None,
    recursion_limit: int,
    cancellation: CancellationController | None,
    enforcer: BudgetEnforcer,
    started_ns: int,
    write_manifest_files: bool,
    write_audit_summary: bool,
    audit: AuditRecorder,
) -> RunResult:
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

    tracer = init_tracer(run_dir, run_id=run_id)
    root_attrs = {
        WORKFLOW_RUN_ID: run_id,
        WORKFLOW_GRAPH_NAME: metadata.name,
    }
    with tracer.start_as_current_span(f"run.{metadata.name}", attributes=root_attrs) as span:
        try:
            # Budget enforcement is an intentional node-boundary check: after each
            # completed step update, before the next node is dispatched.
            last_state = final_state
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            for chunk in app.stream(state, config=config):
                for _node_id, update in chunk.items():
                    if isinstance(update, dict):
                        last_state.update(update)
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
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
        except CancelledError as ce:
            status = "cancelled"
            error = str(ce)
            span.set_attribute(WORKFLOW_STATUS, status)
        except PendingApprovalError as pae:
            status = "pending_approval"
            error = None
            last_state.update(pae.state_update)
            final_state = last_state
            span.set_attribute(WORKFLOW_STATUS, status)
        except PendingSubgraphApprovalError as psae:
            status = "pending_approval"
            error = None
            last_state.update(psae.state_update)
            final_state = last_state
            span.set_attribute(WORKFLOW_STATUS, status)
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
    if write_audit_summary:
        audit.write_summary(status=status, error=error, final_state=final_state)
    if write_manifest_files:
        write_run_manifest(
            run_dir,
            workflow=metadata.name,
            run_id=run_id,
            started_ns=started_ns,
            ended_ns=ended_ns,
            status=status,
            error=error,
        )
    _write_termination_artifact(run_dir, status=status, error=error, final_state=final_state)

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


def _write_termination_artifact(run_dir: Path, *, status: str, error: str | None, final_state: dict) -> None:
    reason = status
    if status == "ok" and final_state.get("agent_should_stop"):
        reason = "final_answer"
    permission_decision = final_state.get("permission_decision")
    if isinstance(permission_decision, dict) and permission_decision.get("hard_block"):
        reason = "permission_hard_block"
    payload = {
        "status": status,
        "reason": reason,
        "error": error,
        "checkpoint_flushed": True,
    }
    (run_dir / "termination.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
