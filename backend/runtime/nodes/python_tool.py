"""Runtime factory for python_tool nodes — calls allowlisted local Python functions."""
from __future__ import annotations

import contextlib
import importlib
import io
import time
from typing import Any, Callable

from opentelemetry.trace import Status, StatusCode

from backend.builder.nodes import PythonToolNodeConfig
from backend.builder.python_tool_allowlist import load_allowlist
from backend.runtime.audit import AuditRecorder, audit_preview
from backend.runtime.cancellation import CancellationController
from backend.runtime.state import WorkflowState
from backend.telemetry.genai_attrs import WORKFLOW_LATENCY_MS, WORKFLOW_STATUS, node_attrs
from backend.telemetry.tracer import get_tracer

_MAX_CAPTURED_BYTES = 4096


def make_python_tool_node(
    cfg: PythonToolNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    cancellation: CancellationController | None = None,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        attrs = node_attrs(
            run_id=run_id,
            graph_name=graph_name,
            node_id=cfg.id,
            node_kind="python_tool",
            iteration=state.get("iteration_counts", {}).get(cfg.id, 0),
        )
        attrs["python_tool.callable_path"] = cfg.callable_path

        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            t0 = time.monotonic_ns()
            try:
                func = _resolve_callable(cfg.callable_path)
                kwargs = {kwarg: state[state_key] for kwarg, state_key in cfg.inputs.items()}
                stdout_buf = io.StringIO()
                stderr_buf = io.StringIO()
                with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                    result = func(**kwargs)
                latency_ms = (time.monotonic_ns() - t0) / 1_000_000
                span.set_attribute(WORKFLOW_LATENCY_MS, latency_ms)
                span.set_attribute(WORKFLOW_STATUS, "ok")
                captured = stdout_buf.getvalue()
                if captured:
                    span.set_attribute("python_tool.stdout", captured[:_MAX_CAPTURED_BYTES])
                captured_err = stderr_buf.getvalue()
                if captured_err:
                    span.set_attribute("python_tool.stderr", captured_err[:_MAX_CAPTURED_BYTES])
                span.set_status(Status(StatusCode.OK))
                if audit is not None:
                    audit.write_event(
                        {
                            "node_id": cfg.id,
                            "node_kind": "python_tool",
                            "status": "ok",
                            "callable_path": cfg.callable_path,
                            "inputs": audit_preview(kwargs),
                            "output_state_key": cfg.output_state_key,
                            "output": audit_preview(result),
                            "stdout": audit_preview(captured),
                            "stderr": audit_preview(captured_err),
                            "latency_ms": latency_ms,
                        }
                    )
                return {cfg.output_state_key: result}
            except Exception as e:
                span.set_attribute(WORKFLOW_STATUS, "error")
                span.set_status(Status(StatusCode.ERROR, str(e)))
                if audit is not None:
                    audit.write_event(
                        {
                            "node_id": cfg.id,
                            "node_kind": "python_tool",
                            "status": "error",
                            "callable_path": cfg.callable_path,
                            "error": f"{type(e).__name__}: {e}",
                        }
                    )
                raise

    _node.__name__ = f"python_tool_{cfg.id}"
    return _node


def _resolve_callable(callable_path: str) -> Any:
    # Defense-in-depth: re-check allowlist at execution time
    if callable_path not in load_allowlist():
        raise PermissionError(
            f"callable_path {callable_path!r} is not in the python_tools.yaml allowlist"
        )
    module_path, _, attr = callable_path.rpartition(".")
    if not module_path:
        raise ValueError(f"callable_path {callable_path!r} must be a dotted module.function path")
    module = importlib.import_module(module_path)
    func = getattr(module, attr)
    if not callable(func):
        raise TypeError(f"{callable_path!r} is not callable")
    return func
