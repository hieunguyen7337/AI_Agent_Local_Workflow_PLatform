"""Tester node with sandbox execution primary path and LLM-judge fallback."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

from opentelemetry.trace import Status, StatusCode

from backend.builder.nodes import TesterNodeConfig
from backend.providers import call_provider
from backend.providers.pricing import price_for
from backend.runtime.state import WorkflowState
from backend.telemetry.genai_attrs import (
    WORKFLOW_COST_USD,
    WORKFLOW_LATENCY_MS,
    WORKFLOW_STATUS,
    llm_request_attrs,
    llm_usage_attrs,
    node_attrs,
)
from backend.telemetry.tracer import get_tracer

_RESULT_PREFIX = "__WORKFLOW_TEST_RESULT__"


@dataclass
class SandboxExecutionResult:
    verdict: bool
    status: str  # PASS, FAIL, ERROR, TIMEOUT
    feedback: str
    stdout: str
    stderr: str
    truncated: bool


def make_tester_node(
    cfg: TesterNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    on_cost: Callable[[float], None],
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()
    price = price_for(cfg.provider, cfg.model)

    def _node(state: WorkflowState) -> dict:
        iteration = state.get("iteration_counts", {}).get(cfg.id, 0)
        candidate = str(state.get(cfg.candidate_state_key, "") or "")
        expected = str(state.get(cfg.expected_state_key, "") or "")
        test_code = str(state.get(cfg.test_code_state_key, "") or "")

        attrs: dict[str, object] = {
            **node_attrs(
                run_id=run_id,
                graph_name=graph_name,
                node_id=cfg.id,
                node_kind="tester",
                iteration=iteration,
            )
        }
        use_sandbox = cfg.execution_mode == "sandbox" and bool(test_code.strip())
        if not use_sandbox:
            attrs.update(
                llm_request_attrs(
                    system=cfg.provider,
                    model=cfg.model,
                    temperature=cfg.temperature,
                )
            )

        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            t0 = time.monotonic_ns()
            try:
                if use_sandbox:
                    exec_result = run_sandbox_python(
                        candidate_code=candidate,
                        test_code=test_code,
                        timeout_s=cfg.timeout_s,
                        max_output_bytes=cfg.max_output_bytes,
                        memory_limit_mb=cfg.memory_limit_mb,
                    )
                    latency_ms = (time.monotonic_ns() - t0) / 1_000_000
                    span.set_attribute(WORKFLOW_LATENCY_MS, latency_ms)
                    span.set_attribute(WORKFLOW_STATUS, exec_result.status)
                    span.set_status(Status(StatusCode.OK if exec_result.verdict else StatusCode.ERROR))
                    return {
                        "tester_verdict": exec_result.verdict,
                        "tester_feedback": _format_sandbox_feedback(exec_result),
                        "tester_mode": "sandbox",
                        "cost_usd_accum": state.get("cost_usd_accum", 0.0),
                    }

                # Fallback / explicit llm_judge mode.
                user = (
                    f"EXPECTED OUTCOME:\n{expected}\n\n"
                    f"CANDIDATE OUTPUT:\n{candidate}\n\n"
                    "First line: PASS or FAIL. Remaining lines: short reason."
                )
                resp = call_provider(
                    cfg.provider,
                    model=cfg.model,
                    messages=[
                        {"role": "system", "content": cfg.system_prompt},
                        {"role": "user", "content": user},
                    ],
                    temperature=cfg.temperature,
                    max_retries=cfg.max_retries,
                )
                first_line = resp.text.strip().splitlines()[0].strip().upper() if resp.text.strip() else "FAIL"
                verdict = first_line.startswith("PASS")
                feedback = "\n".join(resp.text.strip().splitlines()[1:]).strip()

                cost = price.cost_usd(resp.usage.input_tokens, resp.usage.output_tokens)
                latency_ms = (time.monotonic_ns() - t0) / 1_000_000
                span.set_attributes(
                    llm_usage_attrs(
                        input_tokens=resp.usage.input_tokens,
                        output_tokens=resp.usage.output_tokens,
                        model=resp.model,
                    )
                )
                span.set_attribute(WORKFLOW_COST_USD, cost)
                span.set_attribute(WORKFLOW_LATENCY_MS, latency_ms)
                span.set_attribute(WORKFLOW_STATUS, "PASS" if verdict else "FAIL")
                span.set_status(Status(StatusCode.OK))

                on_cost(cost)
                return {
                    "tester_verdict": verdict,
                    "tester_feedback": feedback,
                    "tester_mode": "llm_judge",
                    "cost_usd_accum": state.get("cost_usd_accum", 0.0) + cost,
                }
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    _node.__name__ = f"tester_{cfg.id}"
    return _node


def run_sandbox_python(
    *,
    candidate_code: str,
    test_code: str,
    timeout_s: float,
    max_output_bytes: int,
    memory_limit_mb: int | None,
) -> SandboxExecutionResult:
    wrapper = _sandbox_wrapper_source(candidate_code=candidate_code, test_code=test_code)
    with tempfile.TemporaryDirectory(prefix="wf_tester_") as td:
        script_path = os.path.join(td, "sandbox_runner.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(wrapper)

        kwargs: dict = {
            "capture_output": True,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "timeout": timeout_s,
        }
        preexec_fn = _preexec_memory_limit(memory_limit_mb)
        if preexec_fn is not None:
            kwargs["preexec_fn"] = preexec_fn

        try:
            proc = subprocess.run([sys.executable, script_path], **kwargs)
        except subprocess.TimeoutExpired as te:
            out = _truncate(te.stdout or "", max_output_bytes)
            err = _truncate(te.stderr or "", max_output_bytes)
            truncated = _is_truncated(te.stdout or "", max_output_bytes) or _is_truncated(
                te.stderr or "", max_output_bytes
            )
            return SandboxExecutionResult(
                verdict=False,
                status="TIMEOUT",
                feedback=f"Sandbox timed out after {timeout_s:.2f}s.",
                stdout=out,
                stderr=err,
                truncated=truncated,
            )

    stdout_raw = proc.stdout or ""
    stderr_raw = proc.stderr or ""
    stdout = _truncate(stdout_raw, max_output_bytes)
    stderr = _truncate(stderr_raw, max_output_bytes)
    truncated = _is_truncated(stdout_raw, max_output_bytes) or _is_truncated(stderr_raw, max_output_bytes)

    payload = _extract_result_payload(stdout_raw)
    if payload is None:
        return SandboxExecutionResult(
            verdict=False,
            status="ERROR",
            feedback="Sandbox produced no structured result.",
            stdout=stdout,
            stderr=stderr,
            truncated=truncated,
        )

    verdict = bool(payload.get("verdict", False))
    status = str(payload.get("status", "ERROR")).upper()
    feedback = str(payload.get("feedback", "")).strip()
    return SandboxExecutionResult(
        verdict=verdict,
        status=status,
        feedback=feedback,
        stdout=stdout,
        stderr=stderr,
        truncated=truncated,
    )


def _sandbox_wrapper_source(*, candidate_code: str, test_code: str) -> str:
    return f"""
import json
import traceback

CANDIDATE = {candidate_code!r}
TEST_CODE = {test_code!r}

result = {{"verdict": False, "status": "ERROR", "feedback": ""}}
ns = {{}}
try:
    exec(CANDIDATE, ns, ns)
    exec(TEST_CODE, ns, ns)
    result["verdict"] = True
    result["status"] = "PASS"
    result["feedback"] = "All sandbox tests passed."
except AssertionError as e:
    result["verdict"] = False
    result["status"] = "FAIL"
    result["feedback"] = str(e) if str(e) else "Assertion failed."
except Exception:
    result["verdict"] = False
    result["status"] = "ERROR"
    result["feedback"] = traceback.format_exc()

print("{_RESULT_PREFIX}" + json.dumps(result, ensure_ascii=False))
"""


def _extract_result_payload(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_RESULT_PREFIX):
            raw = line[len(_RESULT_PREFIX) :]
            try:
                data = json.loads(raw)
            except Exception:
                return None
            return data if isinstance(data, dict) else None
    return None


def _format_sandbox_feedback(result: SandboxExecutionResult) -> str:
    parts = [f"{result.status}: {result.feedback}".strip()]
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    if result.truncated:
        parts.append("(output truncated)")
    return "\n\n".join(parts)


def _preexec_memory_limit(memory_limit_mb: int | None):
    if memory_limit_mb is None or os.name == "nt":
        return None

    def _fn():
        try:
            import resource

            limit = int(memory_limit_mb) * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except Exception:
            return

    return _fn


def _truncate(text: str, max_bytes: int) -> str:
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return text
    clipped = raw[:max_bytes]
    return clipped.decode("utf-8", errors="ignore")


def _is_truncated(text: str, max_bytes: int) -> bool:
    return len(text.encode("utf-8", errors="replace")) > max_bytes
