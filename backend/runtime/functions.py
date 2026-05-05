"""Workflow-as-function API built on the canonical YAML runtime."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.graphspec import load_workflow_metadata
from backend.runtime.cancellation import CancellationController
from backend.runtime.artifacts import default_runs_root_for_workflow
from backend.runtime.executor import run_graph

DEFAULT_MAX_CONCURRENCY = 50


@dataclass(frozen=True)
class WorkflowFunctionResult:
    workflow: str
    status: str
    final_state: dict[str, Any]
    run_id: str
    run_dir: Path
    error: str | None
    cost_usd: float
    latency_ms: float


@dataclass(frozen=True)
class WorkflowBatchItem:
    input_state: Mapping[str, Any] | str
    id: str | None = None
    expected: str | None = None


@dataclass(frozen=True)
class WorkflowBatchResult:
    id: str | None
    status: str
    final_state: dict[str, Any]
    run_id: str | None
    run_dir: Path | None
    error: str | None
    cost_usd: float
    latency_ms: float


def run_workflow_function(
    workflow_id: str,
    input_state: Mapping[str, Any] | str,
    *,
    expected: str | None = None,
    runs_root: Path | None = None,
    run_id: str | None = None,
    thread_id: str | None = None,
    recursion_limit: int = 50,
    start_at_node: str | None = None,
    cancellation: CancellationController | None = None,
    run_dir_override: Path | None = None,
    use_checkpointer: bool = True,
    write_manifest_files: bool = True,
    write_audit_summary: bool = True,
    audit_context: dict[str, Any] | None = None,
    workflow_specs_root: Path | None = None,
) -> WorkflowFunctionResult:
    """Run a YAML workflow as a reusable local function.

    ``input_state`` may be a full workflow state mapping or a string for
    legacy ``user_input`` compatibility. Additional state keys are forwarded as
    initial state overrides.
    """
    state = {"user_input": input_state} if isinstance(input_state, str) else dict(input_state)
    user_input = str(state.get("user_input", ""))
    if expected is None and "_expected" in state:
        expected = str(state["_expected"])

    initial_overrides = {k: v for k, v in state.items() if k not in {"user_input", "_expected"}}
    if runs_root is None:
        runs_root = default_runs_root_for_workflow(workflow_id)
    metadata = load_workflow_metadata(workflow_id, specs_root=workflow_specs_root)
    result = run_graph(
        metadata,
        user_input=user_input,
        expected=expected,
        runs_root=runs_root,
        run_id=run_id,
        thread_id=thread_id,
        initial_state_overrides=initial_overrides or None,
        recursion_limit=recursion_limit,
        start_at_node=start_at_node,
        cancellation=cancellation,
        run_dir_override=run_dir_override,
        use_checkpointer=use_checkpointer,
        write_manifest_files=write_manifest_files,
        write_audit_summary=write_audit_summary,
        audit_context=audit_context,
    )
    return WorkflowFunctionResult(
        workflow=workflow_id,
        status=result.status,
        final_state=result.final_state,
        run_id=result.run_id,
        run_dir=result.run_dir,
        error=result.error,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )


def run_workflow_batch(
    workflow_id: str,
    items: Sequence[WorkflowBatchItem],
    *,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    runs_root: Path | None = None,
    cancellation: CancellationController | None = None,
    shared_run_dir: Path | None = None,
    use_checkpointer: bool = True,
    write_manifest_files: bool = True,
    write_audit_summary: bool = True,
) -> list[WorkflowBatchResult]:
    """Run multiple workflow-function calls with bounded thread concurrency.

    Results are returned in the same order as ``items``. Item failures are
    captured as error results so later items can still complete.
    """
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")
    batch_runs_root = runs_root if runs_root is not None else default_runs_root_for_workflow(workflow_id)
    results: list[WorkflowBatchResult | None] = [None] * len(items)
    next_index = 0

    def _run(index: int, item: WorkflowBatchItem) -> tuple[int, WorkflowBatchResult]:
        try:
            result = run_workflow_function(
                workflow_id,
                item.input_state,
                expected=item.expected,
                runs_root=batch_runs_root,
                cancellation=cancellation,
                run_dir_override=shared_run_dir,
                use_checkpointer=use_checkpointer,
                write_manifest_files=write_manifest_files,
                write_audit_summary=write_audit_summary,
                audit_context={"row_index": index, "item_id": item.id} if shared_run_dir is not None else None,
            )
            return index, WorkflowBatchResult(
                id=item.id,
                status=result.status,
                final_state=result.final_state,
                run_id=result.run_id,
                run_dir=result.run_dir,
                error=result.error,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
            )
        except Exception as exc:
            return index, WorkflowBatchResult(
                id=item.id,
                status="error",
                final_state={},
                run_id=None,
                run_dir=None,
                error=f"{type(exc).__name__}: {exc}",
                cost_usd=0.0,
                latency_ms=0.0,
            )

    with ThreadPoolExecutor(max_workers=min(max_concurrency, max(len(items), 1))) as pool:
        pending: set[Future[tuple[int, WorkflowBatchResult]]] = set()

        def _submit_next() -> None:
            nonlocal next_index
            if next_index >= len(items):
                return
            if cancellation is not None and cancellation.is_cancelled():
                return
            pending.add(pool.submit(_run, next_index, items[next_index]))
            next_index += 1

        while next_index < len(items) and len(pending) < max_concurrency:
            _submit_next()

        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                index, item_result = future.result()
                results[index] = item_result
                if item_result.status == "cancelled" and cancellation is not None:
                    cancellation.cancel()
            while next_index < len(items) and len(pending) < max_concurrency:
                _submit_next()

    for index, item in enumerate(items):
        if results[index] is None:
            results[index] = WorkflowBatchResult(
                id=item.id,
                status="cancelled",
                final_state={},
                run_id=None,
                run_dir=None,
                error="cancelled before scheduling",
                cost_usd=0.0,
                latency_ms=0.0,
            )
    return [result for result in results if result is not None]
