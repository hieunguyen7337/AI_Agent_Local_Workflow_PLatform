"""Visible Claude-style coding-agent loop node implementations."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from opentelemetry.trace import Status, StatusCode

from backend.builder.nodes import (
    AgentContextNodeConfig,
    AgentModelNodeConfig,
    AgentResponseParserNodeConfig,
    AgentStartupNodeConfig,
    ContextCompactorNodeConfig,
    HookRunnerNodeConfig,
    MemoryWriterNodeConfig,
    PermissionGateNodeConfig,
    SubagentContextNodeConfig,
    SubagentJoinNodeConfig,
    SubagentOrchestratorNodeConfig,
    SubagentPlanNodeConfig,
    SubagentSpawnNodeConfig,
    SubagentSummarizeNodeConfig,
    ToolExecutorNodeConfig,
)
from backend.providers import stream_provider
from backend.providers.pricing import price_for
from backend.runtime.agent_tools import (
    TOOLS,
    execute_tool,
    normalize_tool_request,
    path_within_cwd,
    shell_looks_irreversible,
    tool_metadata,
)
from backend.runtime.audit import AuditRecorder, audit_preview
from backend.runtime.artifacts import make_run_id
from backend.runtime.cancellation import CancellationController
from backend.runtime.errors import CancelledError, WorkflowError
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


def make_agent_startup_node(
    cfg: AgentStartupNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            provider_env = {name: bool(os.environ.get(name)) for name in cfg.provider_env_vars}
            policy = _load_optional_json(Path(cfg.policy_config_path))
            mcp_servers = [
                {
                    "name": server.name,
                    "transport": server.transport,
                    "healthy": False,
                    "status": "not_connected",
                    "health_check": server.health_check,
                }
                for server in cfg.mcp_registry.servers
            ]
            runtime_config = {
                "provider_env": provider_env,
                "tool_registry": {name: asdict(tool) for name, tool in TOOLS.items()},
                "policy": policy,
                "mcp_registry": {
                    "transports": cfg.mcp_registry.transports,
                    "capability_registration": cfg.mcp_registry.capability_registration,
                    "context_cost": cfg.mcp_registry.context_cost,
                    "health_check": cfg.mcp_registry.health_check,
                    "servers": mcp_servers,
                },
            }
            checks = {
                "provider_env": provider_env,
                "tool_registry_ok": bool(TOOLS),
                "policy_config_path": cfg.policy_config_path,
                "policy_config_present": Path(cfg.policy_config_path).is_file(),
                "mcp_server_count": len(mcp_servers),
            }
            (run_dir / "startup_checks.json").write_text(
                json.dumps({"checks": checks, "runtime_config": runtime_config}, indent=2),
                encoding="utf-8",
            )
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "ok", "checks": checks})
            return {cfg.output_state_key: runtime_config}

    _node.__name__ = f"agent_startup_{cfg.id}"
    return _node


def make_agent_context_node(
    cfg: AgentContextNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            parts = [f"Workspace: {Path.cwd().as_posix()}", f"User input: {state.get('user_input', '')}"]
            runtime_config = state.get(cfg.runtime_config_state_key, {})
            if runtime_config:
                parts.append(f"Runtime config: {json.dumps(audit_preview(runtime_config, max_string=1200), ensure_ascii=False)}")
            for source in cfg.memory_sources:
                memory_text = _read_memory_source(source.store)
                if memory_text:
                    if len(memory_text) > cfg.max_file_chars:
                        memory_text = memory_text[: cfg.max_file_chars] + "\n...<truncated>"
                    parts.append(f"\n# memory:{source.tier}:{source.store}\n{memory_text}")
            for file_name in cfg.include_files:
                path = Path(file_name)
                if path.is_file():
                    text = path.read_text(encoding="utf-8", errors="replace")
                    if len(text) > cfg.max_file_chars:
                        text = text[: cfg.max_file_chars] + "\n...<truncated>"
                    parts.append(f"\n# {file_name}\n{text}")
            context = "\n\n".join(parts)
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "ok", "output": audit_preview(context)})
            return {cfg.output_state_key: context, "agent_messages": [], "tool_result_history": []}

    _node.__name__ = f"agent_context_{cfg.id}"
    return _node


def make_agent_model_node(
    cfg: AgentModelNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
    on_cost: Callable[[float], None],
    cancellation: CancellationController | None = None,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()
    price = price_for(cfg.provider, cfg.model)

    def _node(state: WorkflowState) -> dict:
        iteration = state.get("iteration_counts", {}).get("loop_gate->agent_model", 0)
        attrs: dict[str, object] = {
            **node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind, iteration=iteration),
            **llm_request_attrs(system=cfg.provider, model=cfg.model, temperature=cfg.temperature, max_tokens=cfg.max_tokens),
        }
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            t0 = time.monotonic_ns()
            try:
                user_text = _format_template(cfg.user_prompt_template, state)
                messages = [{"role": "system", "content": cfg.system_prompt}, {"role": "user", "content": user_text}]
                resp = stream_provider(
                    cfg.provider,
                    model=cfg.model,
                    messages=messages,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                    max_retries=cfg.max_retries,
                    cancel_check=cancellation.is_cancelled if cancellation else None,
                )
                cost = price.cost_usd(resp.usage.input_tokens, resp.usage.output_tokens)
                latency_ms = (time.monotonic_ns() - t0) / 1_000_000
                on_cost(cost)
                span.set_attributes(llm_usage_attrs(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens, model=resp.model))
                span.set_attribute(WORKFLOW_COST_USD, cost)
                span.set_attribute(WORKFLOW_LATENCY_MS, latency_ms)
                span.set_status(Status(StatusCode.OK))

                agent_messages = list(state.get("agent_messages", []))
                agent_messages.extend(
                    [
                        {"role": "user", "content": user_text, "node_id": cfg.id},
                        {"role": "assistant", "content": resp.text, "node_id": cfg.id, "model": resp.model},
                    ]
                )
                _append_jsonl(run_dir / "agent_messages.jsonl", agent_messages[-2])
                _append_jsonl(run_dir / "agent_messages.jsonl", agent_messages[-1])
                if audit is not None:
                    audit.write_event(
                        {
                            "node_id": cfg.id,
                            "node_kind": cfg.kind,
                            "status": "ok",
                            "model": resp.model,
                            "output": audit_preview(resp.text),
                            "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens},
                            "cost_usd": cost,
                            "latency_ms": latency_ms,
                        }
                    )
                return {cfg.output_state_key: resp.text, "agent_messages": agent_messages}
            except CancelledError as e:
                span.set_attribute(WORKFLOW_STATUS, "cancelled")
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            except Exception as e:
                if cancellation is not None and cancellation.is_cancelled():
                    cancelled = CancelledError("user_cancelled")
                    span.set_attribute(WORKFLOW_STATUS, "cancelled")
                    span.set_status(Status(StatusCode.ERROR, str(cancelled)))
                    raise cancelled from e
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    _node.__name__ = f"agent_model_{cfg.id}"
    return _node


def make_agent_response_parser_node(
    cfg: AgentResponseParserNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        raw = str(state.get(cfg.input_state_key, "") or "")
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            payload = _parse_json_payload(raw)
            tool_request: dict[str, Any] = {}
            final_answer = ""
            route = "FINAL"
            if isinstance(payload, dict):
                maybe_tool = payload.get("tool_request") or payload.get("tool")
                if maybe_tool:
                    tool_request = normalize_tool_request(maybe_tool if isinstance(maybe_tool, dict) else payload)
                    route = "TOOL"
                elif str(payload.get("action", "")).lower() in {"tool", "tool_use"}:
                    tool_request = normalize_tool_request(payload)
                    route = "TOOL"
                else:
                    final_answer = str(payload.get("final_answer") or payload.get("answer") or raw)
            else:
                final_answer = raw

            update = {
                cfg.route_state_key: route,
                cfg.tool_request_state_key: tool_request,
                cfg.final_answer_state_key: final_answer,
                cfg.should_stop_state_key: route == "FINAL",
                "subagent_requested": tool_request.get("name") == "subagent",
            }
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "ok", "route": route, "tool_request": tool_request})
            return update

    _node.__name__ = f"agent_response_parser_{cfg.id}"
    return _node


def make_permission_gate_node(
    cfg: PermissionGateNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        request = normalize_tool_request(state.get(cfg.tool_request_state_key, {}))
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            runtime_config = state.get("runtime_config", {})
            decision = _permission_decision(request, cfg.mode, runtime_config=runtime_config, escalate_on=cfg.escalate_on)
            route = decision["route"]
            history = list(state.get(cfg.history_state_key, []))
            if route == "DENIED":
                history.append({"tool": request.get("name", ""), "status": "denied", "error": decision["reason"]})
            _append_jsonl(run_dir / "permission_decisions.jsonl", {"node_id": cfg.id, "request": request, "decision": decision})
            span.set_attribute(WORKFLOW_STATUS, route)
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "ok", "decision": decision})
            return {
                cfg.route_state_key: route,
                cfg.decision_state_key: decision,
                cfg.history_state_key: history,
                "agent_should_stop": False,
            }

    _node.__name__ = f"permission_gate_{cfg.id}"
    return _node


def make_hook_runner_node(
    cfg: HookRunnerNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        request = normalize_tool_request(state.get(cfg.tool_request_state_key, {}))
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            results: list[dict[str, Any]] = []
            for hook in cfg.hooks:
                if hook.applies_to and request.get("name") not in hook.applies_to:
                    continue
                completed = subprocess.run(
                    hook.command,
                    cwd=Path.cwd(),
                    shell=True,
                    text=True,
                    capture_output=True,
                    timeout=hook.timeout_s,
                )
                result = {
                    "id": hook.id,
                    "trigger": cfg.trigger,
                    "command": hook.command,
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                }
                results.append(result)
                if hook.blocking and completed.returncode != 0:
                    raise WorkflowError(f"blocking hook {hook.id!r} failed with exit code {completed.returncode}")
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "ok", "hook_count": len(results)})
            return {cfg.output_state_key: {"trigger": cfg.trigger, "results": results}}

    _node.__name__ = f"hook_runner_{cfg.id}"
    return _node


def make_tool_executor_node(
    cfg: ToolExecutorNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        request = normalize_tool_request(state.get(cfg.tool_request_state_key, {}))
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            allowed_tools = [tool.id for tool in cfg.tools] if cfg.tools else list(cfg.allowed_tools)
            if not allowed_tools:
                allowed_tools = list(TOOLS.keys())
            result = execute_tool(request, run_dir=run_dir, allowed_tools=allowed_tools)
            history = [*list(state.get(cfg.history_state_key, [])), result]
            pressure = len(json.dumps(history, ensure_ascii=False)) > cfg.compaction_threshold_chars
            subagent_requested = request.get("name") == "subagent"
            _append_jsonl(run_dir / "tool_calls.jsonl", {"node_id": cfg.id, "request": request, "result": result})
            span.set_attribute(WORKFLOW_STATUS, str(result.get("status", "")))
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": result.get("status", ""), "tool": request.get("name")})
            return {
                cfg.output_state_key: result,
                cfg.history_state_key: history,
                cfg.context_pressure_state_key: pressure,
                "subagent_requested": subagent_requested,
                "agent_should_stop": False,
                "tool_approval_decision": "",
            }

    _node.__name__ = f"tool_executor_{cfg.id}"
    return _node


def make_context_compactor_node(
    cfg: ContextCompactorNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        history = list(state.get(cfg.history_state_key, []))
        agent_messages = list(state.get(cfg.agent_messages_state_key, []))
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            stages: list[dict[str, Any]] = []
            working_history = list(history)
            working_messages = list(agent_messages)
            for stage in cfg.pipeline:
                if stage.stage == "tool_result_pruning":
                    limit = stage.max_verbose_turns or cfg.preserve_last_n
                    max_chars = stage.max_chars or 4000
                    working_history = [_truncate_tool_result(item, max_chars=max_chars) for item in working_history[-limit:]]
                    stages.append({"stage": stage.stage, "status": "applied", "max_verbose_turns": limit})
                elif stage.stage == "message_merging":
                    if stage.collapse_same_role:
                        working_messages = _merge_same_role_messages(working_messages)
                        stages.append({"stage": stage.stage, "status": "applied", "message_count": len(working_messages)})
                    else:
                        stages.append({"stage": stage.stage, "status": "skipped", "reason": "collapse_same_role disabled"})
                elif stage.stage == "checkpoint_anchoring":
                    working_history = [_anchor_checkpoint_refs(item) for item in working_history]
                    stages.append({"stage": stage.stage, "status": "applied"})
                elif stage.stage == "rolling_summary":
                    stages.append({"stage": stage.stage, "status": "skipped", "reason": "model summarization disabled in deterministic mode"})
                elif stage.stage == "semantic_dedup":
                    stages.append({"stage": stage.stage, "status": "skipped", "reason": "semantic model disabled in deterministic mode"})
            preserved = working_history[-cfg.preserve_last_n :]
            summary = {
                "dropped_tool_result_count": max(0, len(history) - len(preserved)),
                "preserved_tool_results": preserved,
                "always_preserve": cfg.always_preserve,
                "stages": stages,
            }
            compacted = json.dumps(summary, ensure_ascii=False)
            if len(compacted) > cfg.max_chars:
                compacted = compacted[: cfg.max_chars] + "...<truncated>"
            _append_jsonl(run_dir / "context_compactions.jsonl", {"node_id": cfg.id, **summary})
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "ok", "dropped": summary["dropped_tool_result_count"]})
            return {
                cfg.output_state_key: compacted,
                cfg.history_state_key: preserved,
                cfg.agent_messages_state_key: working_messages,
                cfg.context_pressure_state_key: False,
                "agent_should_stop": False,
            }

    _node.__name__ = f"context_compactor_{cfg.id}"
    return _node


def make_subagent_orchestrator_node(
    cfg: SubagentOrchestratorNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        request = normalize_tool_request(state.get(cfg.tool_request_state_key, {}))
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            result = {
                "tool": "subagent",
                "status": "unsupported",
                "summary": "Subagent orchestration is represented in the graph but disabled in v1.",
                "request": request,
                "topology": cfg.topology,
                "context_isolation": cfg.context_isolation,
                "return_mode": cfg.return_mode,
                "max_parallel": cfg.max_parallel,
                "merge_strategy": cfg.merge_strategy,
                "token_cost_multiplier": cfg.token_cost_multiplier,
            }
            subagent_results = [*list(state.get(cfg.output_state_key, [])), result]
            history = [*list(state.get(cfg.history_state_key, [])), result]
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "unsupported"})
            return {
                cfg.output_state_key: subagent_results,
                cfg.history_state_key: history,
                "subagent_requested": False,
                "agent_should_stop": False,
            }

    _node.__name__ = f"subagent_orchestrator_{cfg.id}"
    return _node


def make_subagent_plan_node(
    cfg: SubagentPlanNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            request = normalize_tool_request(state.get(cfg.tool_request_state_key, {}))
            raw_args = request.get("args")
            args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
            fallback = str(state.get(cfg.user_input_state_key, "") or "").strip()
            tasks: list[dict[str, Any]] = []
            raw_tasks = args.get("tasks")
            if isinstance(raw_tasks, list) and raw_tasks:
                for i, item in enumerate(raw_tasks):
                    if not isinstance(item, dict):
                        continue
                    prompt = str(
                        item.get("prompt") or item.get("task") or item.get("description") or ""
                    ).strip()
                    if not prompt:
                        continue
                    tasks.append(
                        {
                            "task_id": str(item.get("task_id", i)),
                            "prompt": prompt,
                            "model_override": item.get("model") or item.get("model_override"),
                        }
                    )
            if not tasks:
                prompt = str(
                    args.get("prompt") or args.get("task") or args.get("description") or fallback
                ).strip()
                if prompt:
                    tasks.append({"task_id": "0", "prompt": prompt, "model_override": args.get("model") or args.get("model_override")})
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "ok", "task_count": len(tasks)})
            return {cfg.output_tasks_state_key: tasks}

    _node.__name__ = f"subagent_plan_{cfg.id}"
    return _node


def make_subagent_context_node(
    cfg: SubagentContextNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            raw_tasks = state.get(cfg.tasks_state_key, [])
            tasks = raw_tasks if isinstance(raw_tasks, list) else []
            parent_user = str(state.get(cfg.user_input_state_key, "") or "")
            plan = str(state.get(cfg.task_plan_state_key, "") or "")
            compacted = str(state.get(cfg.compacted_context_state_key, "") or "")
            assembled = str(state.get(cfg.assembled_context_state_key, "") or "")
            history = state.get(cfg.tool_result_history_state_key, [])
            history_text = json.dumps(history, ensure_ascii=False) if history else "[]"
            if len(history_text) > cfg.max_context_chars:
                history_text = history_text[: cfg.max_context_chars] + "...<truncated>"
            if len(compacted) > cfg.max_context_chars:
                compacted = compacted[: cfg.max_context_chars] + "\n...<truncated>"
            if len(assembled) > cfg.max_context_chars:
                assembled = assembled[: cfg.max_context_chars] + "\n...<truncated>"
            if len(plan) > min(cfg.max_context_chars, 4000):
                plan = plan[:4000] + "\n...<truncated>"

            child_inputs: list[dict[str, Any]] = []
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                tid = str(task.get("task_id", "0"))
                prompt = str(task.get("prompt", "")).strip()
                mo = task.get("model_override")
                bundle = "\n".join(
                    [
                        "[Subagent isolated context — do not assume hidden parent tool transcripts]",
                        f"Parent workflow: {graph_name}",
                        f"Parent run id: {run_id}",
                        f"Subagent task id: {tid}",
                        "",
                        "## Delegated task",
                        prompt,
                        "",
                        "## Parent user goal (reference)",
                        parent_user or "(none)",
                        "",
                        "## Parent approved plan (truncated)",
                        plan or "(none)",
                        "",
                        "## Parent assembled context (truncated)",
                        assembled or "(none)",
                        "",
                        "## Parent compacted context (truncated)",
                        compacted or "(none)",
                        "",
                        "## Parent tool result history (truncated JSON)",
                        history_text,
                        "",
                        "## Model override (optional)",
                        json.dumps(mo, ensure_ascii=False) if mo is not None else "(inherit child workflow default)",
                    ]
                )
                child_inputs.append(
                    {
                        "_task_id": tid,
                        "user_input": bundle,
                        "task_plan": f"(Delegated subagent task {tid}; full briefing is in user_input.)",
                        "assembled_context": "",
                        "compacted_context": "",
                        "tool_result_history": [],
                        "subagent_results": [],
                    }
                )
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "ok", "child_count": len(child_inputs)})
            return {cfg.output_child_inputs_state_key: child_inputs}

    _node.__name__ = f"subagent_context_{cfg.id}"
    return _node


def make_subagent_spawn_node(
    cfg: SubagentSpawnNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
    runs_root: Path,
    on_cost: Callable[[float], None],
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        from backend.graphspec.loader import load_workflow_metadata
        from backend.runtime.executor import run_graph

        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            raw_inputs = state.get(cfg.child_inputs_state_key, [])
            inputs_list = raw_inputs if isinstance(raw_inputs, list) else []
            lineage_root = run_dir / cfg.lineage_dirname
            lineage_root.mkdir(parents=True, exist_ok=True)
            records: list[dict[str, Any]] = []

            for idx, child_input in enumerate(inputs_list):
                if not isinstance(child_input, dict):
                    continue
                task_id = str(child_input.get("_task_id", idx))
                child_user = str(child_input.get("user_input", "") or "").strip()

                if not cfg.execute_children:
                    records.append(
                        {
                            "task_id": task_id,
                            "child_run_id": None,
                            "child_run_dir": None,
                            "status": "planned",
                            "error": None,
                            "final_answer": "",
                            "cost_usd": 0.0,
                            "executed": False,
                        }
                    )
                    continue

                child_metadata = load_workflow_metadata(cfg.child_workflow)
                child_run_id = make_run_id(cfg.child_workflow)
                child_run_dir = lineage_root / child_run_id
                child_run_dir.mkdir(parents=True, exist_ok=True)
                parent_link = {
                    "parent_run_id": run_id,
                    "parent_workflow": graph_name,
                    "relationship": "subagent_child",
                    "subagent_node_id": cfg.id,
                    "task_id": task_id,
                    "child_workflow": cfg.child_workflow,
                    "child_run_id": child_run_id,
                }
                (child_run_dir / "parent_run.json").write_text(
                    json.dumps(parent_link, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                overrides = {k: v for k, v in child_input.items() if not str(k).startswith("_")}
                try:
                    child_result = run_graph(
                        child_metadata,
                        user_input=child_user or "(empty subagent user_input)",
                        runs_root=runs_root,
                        run_id=child_run_id,
                        initial_state_overrides=overrides,
                        recursion_limit=cfg.max_child_recursion,
                        run_dir_override=child_run_dir,
                        audit_context={
                            "subagent_parent_run_id": run_id,
                            "subagent_parent_workflow": graph_name,
                            "subagent_task_id": task_id,
                        },
                    )
                    on_cost(child_result.cost_usd)
                    final_answer = str(child_result.final_state.get("final_answer", "") or "")
                    records.append(
                        {
                            "task_id": task_id,
                            "child_run_id": child_result.run_id,
                            "child_run_dir": child_result.run_dir.as_posix(),
                            "status": child_result.status,
                            "error": child_result.error,
                            "final_answer": final_answer,
                            "cost_usd": child_result.cost_usd,
                            "executed": True,
                        }
                    )
                except Exception as exc:
                    records.append(
                        {
                            "task_id": task_id,
                            "child_run_id": child_run_id,
                            "child_run_dir": child_run_dir.as_posix(),
                            "status": "error",
                            "error": f"{type(exc).__name__}: {exc}",
                            "final_answer": "",
                            "cost_usd": 0.0,
                            "executed": True,
                        }
                    )

            _append_jsonl(
                run_dir / "subagent_lineage.jsonl",
                {"node_id": cfg.id, "records": records},
            )
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "ok", "spawned": len(records)})
            return {
                cfg.output_child_runs_state_key: records,
                "agent_should_stop": False,
            }

    _node.__name__ = f"subagent_spawn_{cfg.id}"
    return _node


def make_subagent_join_node(
    cfg: SubagentJoinNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            raw = state.get(cfg.child_runs_state_key, [])
            joined: list[dict[str, Any]] = []
            for item in raw if isinstance(raw, list) else []:
                if not isinstance(item, dict):
                    continue
                joined.append(
                    {
                        "task_id": item.get("task_id"),
                        "child_run_id": item.get("child_run_id"),
                        "child_run_dir": item.get("child_run_dir"),
                        "status": item.get("status"),
                        "error": item.get("error"),
                        "final_answer": (str(item.get("final_answer") or ""))[:2000],
                        "cost_usd": float(item.get("cost_usd") or 0.0),
                        "executed": bool(item.get("executed", True)),
                    }
                )
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": "ok", "joined": len(joined)})
            return {cfg.output_joined_state_key: joined, "agent_should_stop": False}

    _node.__name__ = f"subagent_join_{cfg.id}"
    return _node


def make_subagent_summarize_node(
    cfg: SubagentSummarizeNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            raw = state.get(cfg.joined_state_key, [])
            joined = raw if isinstance(raw, list) else []
            status = (
                "ok"
                if joined and all(str(j.get("status")) == "ok" for j in joined if isinstance(j, dict))
                else ("partial" if joined else "ok")
            )
            lines = [f"Subagent delegation ({len(joined)} task(s)) aggregate={status}"]
            for j in joined:
                if not isinstance(j, dict):
                    continue
                excerpt = str(j.get("final_answer") or "")[:500]
                lines.append(
                    f"- task {j.get('task_id')}: status={j.get('status')} cost_usd={j.get('cost_usd', 0)} | {excerpt}"
                )
            summary_text = "\n".join(lines)[: cfg.max_summary_chars]
            entry: dict[str, Any] = {
                "tool": "subagent",
                "status": status,
                "summary": summary_text,
                "children": joined,
                "topology": "fan_out",
                "return_mode": "summary_only",
            }
            prior = list(state.get(cfg.output_state_key, []))
            if not isinstance(prior, list):
                prior = []
            subagent_results = [*prior, entry]
            history = [*list(state.get(cfg.history_state_key, [])), entry]
            (run_dir / "subagent_summary.json").write_text(
                json.dumps({"node_id": cfg.id, "entry": entry}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, "status": status})
            return {
                cfg.output_state_key: subagent_results,
                cfg.history_state_key: history,
                "subagent_requested": False,
                "agent_should_stop": False,
                "tool_approval_decision": "",
            }

    _node.__name__ = f"subagent_summarize_{cfg.id}"
    return _node


def make_memory_writer_node(
    cfg: MemoryWriterNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    run_dir: Path,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    tracer = get_tracer()

    def _node(state: WorkflowState) -> dict:
        suggestion = str(state.get(cfg.suggestion_state_key, "") or "").strip()
        attrs = node_attrs(run_id=run_id, graph_name=graph_name, node_id=cfg.id, node_kind=cfg.kind)
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            payload = {
                "workflow": graph_name,
                "run_id": run_id,
                "source_state_key": cfg.suggestion_state_key,
                "suggestion": suggestion,
                "write_mode": cfg.write_mode,
                "target_path": cfg.target_path,
            }
            artifact_path = run_dir / "memory_suggestions.json"
            artifact_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            result = {"status": "artifact_written", "artifact_path": artifact_path.as_posix()}
            if cfg.write_mode == "direct" and suggestion:
                target = Path(cfg.target_path).expanduser()
                target.parent.mkdir(parents=True, exist_ok=True)
                existing = target.read_text(encoding="utf-8", errors="replace") if target.exists() else ""
                target.write_text((existing.rstrip() + "\n" + suggestion + "\n").lstrip(), encoding="utf-8")
                result = {**result, "status": "direct_written", "target_path": target.as_posix()}
            span.set_status(Status(StatusCode.OK))
            if audit is not None:
                audit.write_event({"node_id": cfg.id, "node_kind": cfg.kind, **result})
            return {cfg.output_state_key: result}

    _node.__name__ = f"memory_writer_{cfg.id}"
    return _node


def _permission_decision(
    request: dict[str, Any],
    mode: str,
    *,
    runtime_config: Any,
    escalate_on: list[str],
) -> dict[str, Any]:
    name = request.get("name", "")
    metadata = tool_metadata(name)
    if metadata is None:
        return {"route": "DENIED", "reason": f"unknown tool {name!r}", "mode": mode}
    policy = runtime_config.get("policy", {}) if isinstance(runtime_config, dict) else {}
    deny_tools = set(policy.get("deny_tools", [])) if isinstance(policy, dict) else set()
    allow_tools = set(policy.get("allow_tools", [])) if isinstance(policy, dict) else set()
    if name in deny_tools:
        return {"route": "DENIED", "reason": f"tool {name!r} blocked by local policy", "mode": mode, "hard_block": True}
    if mode == "enterprise" and metadata.permission_required:
        return {"route": "DENIED", "reason": "enterprise mode hard-blocks permission-required tools without local allow policy", "mode": mode, "hard_block": True}
    if metadata.writes_files:
        path = str(request.get("args", {}).get("path", ""))
        if not path_within_cwd(path):
            return {"route": "DENIED", "reason": "file write outside current workspace", "mode": mode}
    if mode == "project_rules" and name in allow_tools:
        return {"route": "APPROVED", "reason": "allowed by project rules", "mode": mode}
    if mode == "project_rules" and metadata.permission_required:
        return {"route": "ASK", "reason": "project rules require approval by default", "mode": mode}
    if mode == "plan" and not metadata.read_only:
        return {"route": "DENIED", "reason": "plan mode is read-only", "mode": mode}
    if mode in {"default", "ask"} and metadata.permission_required:
        return {"route": "ASK", "reason": "tool requires human approval", "mode": mode}
    if mode == "auto_edit" and metadata.shell:
        return {"route": "ASK", "reason": "shell requires human approval in auto_edit", "mode": mode}
    if mode == "auto_edit" and name == "mcp_call":
        return {"route": "ASK", "reason": "mcp_call requires approval in auto_edit", "mode": mode}
    if mode == "sandbox" and metadata.shell:
        return {"route": "APPROVED", "reason": "shell approved for isolated sandbox mode", "mode": mode, "sandboxed": True}
    command = str(request.get("args", {}).get("command", ""))
    irreversible_shell = metadata.shell and shell_looks_irreversible(command)
    if mode == "auto_approve" and irreversible_shell:
        return {"route": "ASK", "reason": "irreversible shell command requires human approval", "mode": mode}
    if "shell_with_sudo" in escalate_on and "sudo" in command.lower():
        return {"route": "ASK", "reason": "shell_with_sudo escalation policy matched", "mode": mode}
    if "irreversible_fs_write" in escalate_on and metadata.writes_files and not metadata.checkpoint_before_write:
        return {"route": "ASK", "reason": "irreversible_fs_write escalation policy matched", "mode": mode}
    if "network_egress" in escalate_on and name == "mcp_call":
        return {"route": "ASK", "reason": "network_egress escalation policy matched", "mode": mode}
    return {"route": "APPROVED", "reason": "allowed by policy", "mode": mode}


def _read_memory_source(store: str) -> str:
    if store == "appstate":
        return ""
    path = Path(store).expanduser()
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def _load_optional_json(path: Path) -> dict[str, Any]:
    try:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _truncate_tool_result(item: Any, *, max_chars: int) -> Any:
    if not isinstance(item, dict):
        return item
    cloned = dict(item)
    output = cloned.get("output")
    if isinstance(output, str) and len(output) > max_chars:
        cloned["output"] = output[:max_chars] + "...<truncated>"
    if isinstance(output, dict):
        cloned["output"] = {
            key: (value[:max_chars] + "...<truncated>" if isinstance(value, str) and len(value) > max_chars else value)
            for key, value in output.items()
        }
    return cloned


def _merge_same_role_messages(messages: list[Any]) -> list[Any]:
    merged: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))
        if merged and merged[-1].get("role") == role:
            merged[-1]["content"] = str(merged[-1].get("content", "")) + "\n" + content
        else:
            merged.append(dict(message))
    return merged


def _anchor_checkpoint_refs(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    cloned = dict(item)
    if cloned.get("tool") == "write_file" and cloned.get("diff_path"):
        cloned["output"] = {"diff_ref": cloned.get("diff_path"), "snapshot_ref": cloned.get("snapshot_path")}
    return cloned


def _format_template(template: str, state: WorkflowState) -> str:
    class _SafeDict(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(_SafeDict(state))


def _parse_json_payload(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"recorded_ns": time.time_ns(), **_jsonable(payload)}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
