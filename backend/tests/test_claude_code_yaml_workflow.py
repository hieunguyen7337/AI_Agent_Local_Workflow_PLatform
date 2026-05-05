"""Visible Claude Code-style YAML workflow tests."""
from __future__ import annotations

import json
from pathlib import Path

from backend.builder.api import END, GraphBuilder
from backend.builder.nodes import (
    AgentContextNodeConfig,
    AgentModelNodeConfig,
    AgentResponseParserNodeConfig,
    AgentStartupNodeConfig,
    ApprovalNodeConfig,
    ContextCompactorNodeConfig,
    GateNodeConfig,
    HookRunnerNodeConfig,
    MemoryWriterNodeConfig,
    PermissionGateNodeConfig,
    RouterNodeConfig,
    SubagentContextNodeConfig,
    SubagentJoinNodeConfig,
    SubagentPlanNodeConfig,
    SubagentSpawnNodeConfig,
    SubagentSummarizeNodeConfig,
    ToolExecutorNodeConfig,
)
from backend.graphspec import graph_spec_to_metadata, load_graph_spec
from backend.providers.base import LLMResponse, Usage
from backend.runtime.agent_tools import normalize_tool_request
from backend.runtime.executor import run_graph
from backend.tools.agent_workflow import tool_history_digest

_WORKFLOWS_ROOT = Path(__file__).resolve().parents[2] / "workflows"


class _Replies:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)

    def __call__(self, _provider="openrouter", **kwargs) -> LLMResponse:
        if not self.replies:
            text = '{"final_answer": "done"}'
        else:
            text = self.replies.pop(0)
        return LLMResponse(text=text, usage=Usage(5, 5), model=kwargs.get("model", "minimax/minimax-m2.7"))


class _SubagentPhasedStream:
    """First parent call delegates; child sees isolated briefing; parent finishes."""

    def __init__(self) -> None:
        self._n = 0

    def __call__(self, _provider="openrouter", **kwargs) -> LLMResponse:
        self._n += 1
        messages = kwargs.get("messages") or []
        blob = "\n".join(str(m.get("content", "")) for m in messages)
        if "[Subagent isolated context" in blob:
            return LLMResponse(
                text='{"final_answer": "child_answer"}',
                usage=Usage(2, 2),
                model=kwargs.get("model", "minimax/minimax-m2.7"),
            )
        if self._n == 1:
            return LLMResponse(
                text='{"tool_request": {"name": "subagent", "args": {"prompt": "delegate"}}}',
                usage=Usage(2, 2),
                model=kwargs.get("model", "minimax/minimax-m2.7"),
            )
        return LLMResponse(
            text='{"final_answer": "parent_done"}',
            usage=Usage(2, 2),
            model=kwargs.get("model", "minimax/minimax-m2.7"),
        )


def _subagent_micro_metadata(*, execute_children: bool = False):
    b = GraphBuilder(name="subagent_micro", cost_budget_usd=1.0, latency_budget_ms=60_000)
    b.add_node(SubagentPlanNodeConfig(id="subagent_plan"))
    b.add_node(SubagentContextNodeConfig(id="subagent_context"))
    b.add_node(SubagentSpawnNodeConfig(id="subagent_spawn", execute_children=execute_children))
    b.add_node(SubagentJoinNodeConfig(id="subagent_join"))
    b.add_node(SubagentSummarizeNodeConfig(id="subagent_summarize"))
    b.set_entry("subagent_plan")
    b.add_edge("subagent_plan", "subagent_context")
    b.add_edge("subagent_context", "subagent_spawn")
    b.add_edge("subagent_spawn", "subagent_join")
    b.add_edge("subagent_join", "subagent_summarize")
    b.add_edge("subagent_summarize", END)
    return b.compile()


def _metadata(
    *,
    mode: str = "auto_approve",
    max_iterations: int = 3,
    compaction_threshold_chars: int = 12000,
    escalate_on: list[str] | None = None,
):
    b = GraphBuilder(name="agent_test", cost_budget_usd=1.0, latency_budget_ms=60_000)
    b.add_node(AgentStartupNodeConfig(id="startup", provider_env_vars=["OPENROUTER_API_KEY"]))
    b.add_node(AgentContextNodeConfig(id="context_loader", include_files=[]))
    b.add_node(
        AgentModelNodeConfig(
            id="agent_model",
            model="minimax/minimax-m2.7",
            system_prompt="Return strict JSON.",
            user_prompt_template="{user_input}\n{tool_result_history}",
        )
    )
    b.add_node(AgentResponseParserNodeConfig(id="agent_response_parser"))
    b.add_node(
        RouterNodeConfig(
            id="tool_route",
            route_state_key="agent_route",
            routes={"FINAL": "loop_gate", "TOOL": "permission_gate"},
            default_target="loop_gate",
        )
    )
    b.add_node(
        PermissionGateNodeConfig(
            id="permission_gate",
            mode=mode,
            policy_stack=[{"source": "runtime_default", "precedence": 0}],
            escalate_on=escalate_on if escalate_on is not None else ["network_egress", "shell_with_sudo"],
        )
    )
    b.add_node(
        RouterNodeConfig(
            id="permission_route",
            route_state_key="permission_route",
            routes={"APPROVED": "pre_tool_hooks", "ASK": "tool_approval", "DENIED": "loop_gate"},
            default_target="loop_gate",
        )
    )
    b.add_node(
        ApprovalNodeConfig(
            id="tool_approval",
            prompt="Approve tool?",
            approval_state_key="tool_approval_decision",
            approved_target="pre_tool_hooks",
            rejected_target="loop_gate",
        )
    )
    b.add_node(HookRunnerNodeConfig(id="pre_tool_hooks", trigger="pre_tool", output_state_key="hook_pre_result"))
    b.add_node(ToolExecutorNodeConfig(id="tool_executor", compaction_threshold_chars=compaction_threshold_chars))
    b.add_node(HookRunnerNodeConfig(id="post_tool_hooks", trigger="post_tool", output_state_key="hook_post_result"))
    b.add_node(
        GateNodeConfig(
            id="subagent_gate",
            verdict_state_key="subagent_requested",
            pass_target="subagent_plan",
            fail_target="compaction_gate",
        )
    )
    b.add_node(SubagentPlanNodeConfig(id="subagent_plan"))
    b.add_node(SubagentContextNodeConfig(id="subagent_context"))
    b.add_node(SubagentSpawnNodeConfig(id="subagent_spawn", child_workflow="claude_code_subagent_workflow"))
    b.add_node(SubagentJoinNodeConfig(id="subagent_join"))
    b.add_node(SubagentSummarizeNodeConfig(id="subagent_summarize"))
    b.add_node(GateNodeConfig(id="compaction_gate", verdict_state_key="context_pressure_exceeded", pass_target="context_compactor", fail_target="loop_gate"))
    b.add_node(
        ContextCompactorNodeConfig(
            id="context_compactor",
            pipeline=[
                {"stage": "tool_result_pruning", "max_verbose_turns": 5, "max_chars": 100},
                {"stage": "message_merging", "collapse_same_role": True},
                {"stage": "checkpoint_anchoring", "replace_file_content_with": "diff_refs"},
            ],
        )
    )
    b.add_node(GateNodeConfig(id="loop_gate", verdict_state_key="agent_should_stop", pass_target=END, fail_target="agent_model"))
    b.set_entry("startup")
    b.add_edge("startup", "context_loader")
    b.add_edge("context_loader", "agent_model")
    b.add_edge("agent_model", "agent_response_parser")
    b.add_edge("agent_response_parser", "tool_route")
    b.add_edge("permission_gate", "permission_route")
    b.add_edge("pre_tool_hooks", "tool_executor")
    b.add_edge("tool_executor", "post_tool_hooks")
    b.add_edge("post_tool_hooks", "subagent_gate")
    b.add_edge("subagent_plan", "subagent_context")
    b.add_edge("subagent_context", "subagent_spawn")
    b.add_edge("subagent_spawn", "subagent_join")
    b.add_edge("subagent_join", "subagent_summarize")
    b.add_edge("subagent_summarize", "compaction_gate")
    b.add_edge("context_compactor", "loop_gate")
    b.add_loop("loop_gate", "agent_model", max_iterations=max_iterations)
    return b.compile()


def test_loads_canonical_claude_code_yaml_workflow():
    spec = load_graph_spec("claude_code_yaml_workflow")
    metadata = graph_spec_to_metadata(spec)

    assert spec.name == "claude_code_yaml_workflow"
    assert metadata.nodes["agent_response_parser"].kind == "agent_response_parser"
    assert metadata.nodes["startup"].kind == "agent_startup"
    assert metadata.nodes["permission_gate"].kind == "permission_gate"
    assert metadata.nodes["permission_gate"].mode == "default"
    assert metadata.nodes["tool_executor"].tools[0].id == "read_file"
    assert metadata.nodes["context_compactor"].pipeline[0].stage == "rolling_summary"
    assert metadata.nodes["subagent_spawn"].kind == "subagent_spawn"
    assert metadata.nodes["subagent_spawn"].child_workflow == "claude_code_subagent_workflow"
    assert "plan_normalize" in metadata.nodes
    assert "tool_history_digest" in metadata.nodes
    child = load_graph_spec("claude_code_subagent_workflow")
    assert child.name == "claude_code_subagent_workflow"
    assert "tool_history_digest" in graph_spec_to_metadata(child).nodes
    assert graph_spec_to_metadata(child).nodes["permission_gate"].mode == "auto_approve"
    assert metadata.loops[0].back_edge_from == "loop_gate"
    assert metadata.loops[0].back_edge_to == "agent_model"
    assert metadata.loops[0].max_iterations == 50


def test_final_answer_path_bypasses_tool_execution(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", _Replies(['{"final_answer": "finished"}']))

    result = run_graph(_metadata(), user_input="finish", runs_root=tmp_path / "runs")

    assert result.status == "ok"
    assert result.final_state["final_answer"] == "finished"
    assert "runtime_config" in result.final_state
    assert (result.run_dir / "startup_checks.json").exists()
    assert (result.run_dir / "termination.json").exists()
    assert not (result.run_dir / "tool_calls.jsonl").exists()


def test_tool_path_writes_artifacts_and_compacts(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample.txt").write_text("hello world", encoding="utf-8")
    replies = [
        '{"tool_request": {"name": "read_file", "args": {"path": "sample.txt"}}}',
        '{"final_answer": "read sample"}',
    ]
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", _Replies(replies))

    result = run_graph(
        _metadata(compaction_threshold_chars=1),
        user_input="read sample",
        runs_root=tmp_path / "runs",
    )

    assert result.status == "ok"
    assert result.final_state["final_answer"] == "read sample"
    assert (result.run_dir / "agent_messages.jsonl").exists()
    assert (result.run_dir / "tool_calls.jsonl").exists()
    assert (result.run_dir / "permission_decisions.jsonl").exists()
    assert (result.run_dir / "context_compactions.jsonl").exists()
    compaction = (result.run_dir / "context_compactions.jsonl").read_text(encoding="utf-8")
    assert "tool_result_pruning" in compaction


def test_permission_denial_returns_feedback_to_loop(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    replies = [
        '{"tool_request": {"name": "write_file", "args": {"path": "out.txt", "content": "x"}}}',
        '{"final_answer": "could not write"}',
    ]
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", _Replies(replies))

    result = run_graph(_metadata(mode="plan"), user_input="write", runs_root=tmp_path / "runs")

    assert result.status == "ok"
    assert result.final_state["tool_result_history"][0]["status"] == "denied"
    assert result.final_state["final_answer"] == "could not write"
    assert not (tmp_path / "out.txt").exists()


def test_enterprise_hard_block_records_termination_feedback(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    replies = [
        '{"tool_request": {"name": "write_file", "args": {"path": "out.txt", "content": "x"}}}',
        '{"final_answer": "blocked"}',
    ]
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", _Replies(replies))

    result = run_graph(_metadata(mode="enterprise"), user_input="write", runs_root=tmp_path / "runs")

    assert result.status == "ok"
    assert result.final_state["permission_decision"]["hard_block"] is True
    termination = json.loads((result.run_dir / "termination.json").read_text(encoding="utf-8"))
    assert termination["reason"] == "permission_hard_block"


def test_project_rules_can_allow_permission_required_tool(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "agent_policy.json").write_text('{"allow_tools": ["write_file"]}', encoding="utf-8")
    replies = [
        '{"tool_request": {"name": "write_file", "args": {"path": "out.txt", "content": "allowed"}}}',
        '{"final_answer": "wrote"}',
    ]
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", _Replies(replies))

    result = run_graph(_metadata(mode="project_rules"), user_input="write", runs_root=tmp_path / "runs")

    assert result.status == "ok"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "allowed"
    assert result.final_state["permission_decision"]["reason"] == "allowed by project rules"


def test_mcp_call_is_unavailable_and_auditable(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    replies = [
        '{"tool_request": {"name": "mcp_call", "args": {"server": "github", "tool": "search", "arguments": {}}}}',
        '{"final_answer": "mcp unavailable"}',
    ]
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", _Replies(replies))

    result = run_graph(_metadata(mode="auto_approve", escalate_on=[]), user_input="mcp", runs_root=tmp_path / "runs")

    assert result.status == "ok"
    assert result.final_state["tool_result_history"][0]["status"] == "unavailable"
    assert "mcp_call" in (result.run_dir / "tool_calls.jsonl").read_text(encoding="utf-8")


def test_memory_writer_defaults_to_artifact_only(tmp_path: Path):
    b = GraphBuilder(name="memory_test", cost_budget_usd=1.0, latency_budget_ms=60_000)
    b.add_node(MemoryWriterNodeConfig(id="memory_writer", suggestion_state_key="final_answer"))
    b.set_entry("memory_writer")

    result = run_graph(
        b.compile(),
        user_input="memory",
        runs_root=tmp_path / "runs",
        initial_state_overrides={"final_answer": "Remember the build command."},
    )

    assert result.status == "ok"
    artifact = result.run_dir / "memory_suggestions.json"
    assert artifact.exists()
    assert "Remember the build command" in artifact.read_text(encoding="utf-8")


def test_tool_approval_can_pause_and_resume(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    request = '{"tool_request": {"name": "write_file", "args": {"path": "out.txt", "content": "approved"}}}'
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", _Replies([request]))

    pending = run_graph(_metadata(mode="ask"), user_input="write", runs_root=tmp_path / "runs")

    assert pending.status == "pending_approval"
    assert pending.final_state["pending_approval"]["node_id"] == "tool_approval"
    assert not (tmp_path / "out.txt").exists()

    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", _Replies([request, '{"final_answer": "wrote file"}']))
    approved = run_graph(
        _metadata(mode="ask"),
        user_input="write",
        runs_root=tmp_path / "runs",
        initial_state_overrides={"tool_approval_decision": "approved"},
    )

    assert approved.status == "ok"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "approved"
    assert (approved.run_dir / "file_snapshots").exists()


def test_subagent_micro_pipeline_planned_mode(tmp_path: Path):
    result = run_graph(
        _subagent_micro_metadata(execute_children=False),
        user_input="parent",
        runs_root=tmp_path / "runs",
        initial_state_overrides={
            "tool_request": {"name": "subagent", "args": {"prompt": "micro task"}},
            "task_plan": "plan",
            "assembled_context": "ctx",
            "compacted_context": "",
            "tool_result_history": [],
        },
    )
    assert result.status == "ok"
    assert (result.run_dir / "subagent_summary.json").exists()
    assert result.final_state["subagent_joined"][0]["status"] == "planned"
    assert result.final_state["subagent_results"][-1]["status"] == "partial"


def test_subagent_delegation_runs_child_workflow(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", _SubagentPhasedStream())

    result = run_graph(
        _metadata(max_iterations=5),
        user_input="delegate work",
        runs_root=tmp_path / "runs",
    )

    assert result.status == "ok"
    assert result.final_state["final_answer"] == "parent_done"
    assert (result.run_dir / "subagent_runs").is_dir()
    assert any((result.run_dir / "subagent_runs").iterdir())
    assert (result.run_dir / "subagent_summary.json").exists()
    assert result.final_state["subagent_joined"][0]["status"] == "ok"
    assert "child_answer" in result.final_state["subagent_results"][-1]["summary"]


def test_loop_gate_max_iterations_halts(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sample.txt").write_text("hello", encoding="utf-8")
    request = '{"tool_request": {"name": "read_file", "args": {"path": "sample.txt"}}}'
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", _Replies([request, request, request]))

    result = run_graph(_metadata(max_iterations=1), user_input="loop", runs_root=tmp_path / "runs")

    assert result.status == "MaxIterationsError"


def test_loads_ci_claude_code_yaml_workflow():
    spec = load_graph_spec("claude_code_yaml_workflow_ci", specs_root=_WORKFLOWS_ROOT)
    metadata = graph_spec_to_metadata(spec)
    assert spec.name == "claude_code_yaml_workflow_ci"
    assert "plan_approval" not in metadata.nodes
    assert ("plan_normalize", "agent_model") in metadata.edges
    assert ("plan_normalize", "plan_approval") not in metadata.edges
    assert metadata.nodes["permission_gate"].mode == "auto_approve"
    assert metadata.loops[0].max_iterations == 80


def test_normalize_tool_request_rewrites_absolute_path_under_cwd(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir(parents=True)
    (nested / "a.txt").write_text("x", encoding="utf-8")
    abs_path = (tmp_path / "nested" / "a.txt").resolve()
    req = normalize_tool_request({"name": "read_file", "args": {"path": str(abs_path)}})
    assert req["args"]["path"].replace("\\", "/") == "nested/a.txt"


def test_tool_history_digest_respects_total_cap():
    hist = [
        {"tool": "read_file", "path": f"f{i}.txt", "status": "ok", "output": "x" * 400}
        for i in range(30)
    ]
    text = tool_history_digest(hist, max_items=12, max_total_chars=800)
    assert len(text) <= 820
    assert "digest_truncated" in text or len(text) < 801


def test_ci_plan_normalize_cleans_bad_planner_output(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    normalized = "1. Inspect the issue\n2. Apply a minimal fix\n3. Verify behavior\n"
    replies = _Replies(
        [
            '[TOOL_CALL] read_file({"path": "x"})',
            normalized,
            '{"final_answer": "done"}',
        ]
    )
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", replies)
    monkeypatch.setattr("backend.runtime.nodes.llm.stream_provider", replies)
    meta = graph_spec_to_metadata(load_graph_spec("claude_code_yaml_workflow_ci", specs_root=_WORKFLOWS_ROOT))
    result = run_graph(meta, user_input="fix bug", runs_root=tmp_path / "runs", recursion_limit=50)
    assert result.status == "ok"
    assert "[TOOL_CALL]" not in result.final_state["task_plan"]
    assert "1. Inspect the issue" in result.final_state["task_plan"]


def test_ci_challenging_read_search_then_final(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "big.txt").write_text("needle here " + "p" * 1500, encoding="utf-8")
    (tmp_path / "hit.py").write_text("needle in py\n", encoding="utf-8")
    phases = [
        "1. Read big.txt\n2. Search for needle\n3. Summarize\n",
        "1. Read big.txt\n2. Search for needle\n3. Summarize\n",
        '{"tool_request": {"name": "read_file", "args": {"path": "big.txt"}}}',
        '{"tool_request": {"name": "search", "args": {"pattern": "needle", "path": ".", "include": "*.py"}}}',
        '{"final_answer": "found needle"}',
    ]
    replies = _Replies(phases)
    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", replies)
    monkeypatch.setattr("backend.runtime.nodes.llm.stream_provider", replies)
    meta = graph_spec_to_metadata(load_graph_spec("claude_code_yaml_workflow_ci", specs_root=_WORKFLOWS_ROOT))
    result = run_graph(meta, user_input="multi-step", runs_root=tmp_path / "runs", recursion_limit=50)
    assert result.status == "ok"
    assert result.final_state["final_answer"] == "found needle"


def test_ci_agent_llm_prompts_stay_bounded_after_large_read(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "huge.txt").write_text("Z" * 30_000, encoding="utf-8")
    prompts: list[str] = []
    seq = iter(
        [
            "1. Read huge file\n",
            "1. Read huge file\n",
            '{"tool_request": {"name": "read_file", "args": {"path": "huge.txt"}}}',
            '{"final_answer": "ok"}',
        ]
    )

    def spy(_provider="openrouter", **kwargs):
        messages = kwargs.get("messages") or []
        blob = "\n".join(str(m.get("content", "")) for m in messages)
        prompts.append(blob)
        text = next(seq, '{"final_answer": "ok"}')
        return LLMResponse(text=text, usage=Usage(5, 5), model=kwargs.get("model", "minimax/minimax-m2.7"))

    monkeypatch.setattr("backend.runtime.nodes.agent.stream_provider", spy)
    monkeypatch.setattr("backend.runtime.nodes.llm.stream_provider", spy)
    meta = graph_spec_to_metadata(load_graph_spec("claude_code_yaml_workflow_ci", specs_root=_WORKFLOWS_ROOT))
    result = run_graph(meta, user_input="u", runs_root=tmp_path / "runs", recursion_limit=50)
    assert result.status == "ok"
    assert max(len(p) for p in prompts) < 22_000
    assert any("out_len=" in p for p in prompts[2:])
