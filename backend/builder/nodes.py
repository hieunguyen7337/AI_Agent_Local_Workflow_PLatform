"""Typed node configurations. Pydantic models for validation + replay override support."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NodeConfig(BaseModel):
    """Base. Do not instantiate directly."""

    id: str
    kind: str
    name: str = ""
    description: str = ""

    model_config = {"extra": "forbid"}


class LLMImageInputConfig(BaseModel):
    """Image state binding for multimodal LLM calls."""

    state_key: str = Field(min_length=1)
    detail: Literal["auto", "low", "high"] = "auto"

    model_config = {"extra": "forbid"}


class LLMNodeConfig(NodeConfig):
    kind: Literal["llm"] = "llm"
    provider: Literal["openrouter", "openai"] = "openrouter"
    model: str
    system_prompt: str
    user_prompt_template: str  # format string over state keys, e.g. "Task: {user_input}\nPlan: {plan}"
    output_state_key: str  # which state field to write the response to
    image_inputs: list[LLMImageInputConfig] = Field(default_factory=list)
    temperature: float = 0.2
    max_tokens: int | None = None
    max_retries: int = 3


class EmbeddingNodeConfig(NodeConfig):
    """Hosted embedding model call. Writes a float vector into workflow state."""

    kind: Literal["embedding"] = "embedding"
    provider: Literal["openrouter"] = "openrouter"
    model: str
    input_template: str = ""
    input_state_key: str | None = None
    image_inputs: list[LLMImageInputConfig] = Field(default_factory=list)
    output_state_key: str = Field(min_length=1)
    dimensions: int | None = Field(default=None, gt=0)
    max_retries: int = 3


class VectorRetrieverNodeConfig(NodeConfig):
    """Local vector search over a SQLite embedding index."""

    kind: Literal["vector_retriever"] = "vector_retriever"
    index_path: str = Field(min_length=1)
    query_embedding_state_key: str = Field(min_length=1)
    output_state_key: str = Field(min_length=1)
    top_k: int = Field(2, gt=0)
    id_output_state_key: str | None = None


class TesterNodeConfig(NodeConfig):
    """LLM-judge stub for M1. Judges `candidate_state_key` against the current fixture's expected."""

    __test__ = False  # tell pytest this isn't a test class

    kind: Literal["tester"] = "tester"
    provider: Literal["openrouter", "openai"] = "openrouter"
    model: str
    system_prompt: str = (
        "You are a strict evaluator. Decide if the candidate output satisfies the expected "
        "outcome. Respond with exactly one word on the first line: PASS or FAIL. "
        "On subsequent lines, give a short reason."
    )
    candidate_state_key: str = "coder_output"
    expected_state_key: str = "_expected"  # injected by eval harness / runner
    test_code_state_key: str = "_test_code"
    execution_mode: Literal["sandbox", "llm_judge"] = "sandbox"
    timeout_s: float = Field(3.0, gt=0.0)
    max_output_bytes: int = Field(12_000, gt=0)
    memory_limit_mb: int | None = Field(256, gt=0)
    temperature: float = 0.0
    max_retries: int = 3


class RetrieverNodeConfig(NodeConfig):
    """Deterministic local-corpus retriever for RAG workflows."""

    kind: Literal["retriever"] = "retriever"
    corpus_path: str
    query_state_key: str = "user_input"
    output_state_key: str = "retrieved_context"
    top_k: int = Field(2, gt=0)


class GateNodeConfig(NodeConfig):
    """Conditional router. Reads tester_verdict from state, routes to pass_target or fail_target."""

    kind: Literal["gate"] = "gate"
    verdict_state_key: str = "tester_verdict"
    pass_target: str  # node id
    fail_target: str  # node id
    termination_conditions: list[Any] = Field(default_factory=list)
    checkpoint_on_any_termination: bool = False


class RouterNodeConfig(NodeConfig):
    """Multi-way router that dispatches on a state token."""

    kind: Literal["router"] = "router"
    route_state_key: str
    routes: dict[str, str]
    default_target: str | None = None


class ApprovalNodeConfig(NodeConfig):
    """Human approval interrupt node."""

    kind: Literal["approval"] = "approval"
    prompt: str
    approval_state_key: str = "approval_decision"
    approved_target: str
    rejected_target: str


class SubgraphNodeConfig(NodeConfig):
    """Reusable workflow node that executes another YAML workflow."""

    kind: Literal["subgraph"] = "subgraph"
    workflow: str
    inputs: dict[str, str] = Field(..., min_length=1)
    outputs: dict[str, str] = Field(..., min_length=1)

    @field_validator("workflow")
    @classmethod
    def _workflow_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("workflow is required")
        return value

    @field_validator("inputs", "outputs")
    @classmethod
    def _mapping_values_required(cls, value: dict[str, str]) -> dict[str, str]:
        for source, target in value.items():
            if not str(source).strip() or not str(target).strip():
                raise ValueError("subgraph mappings require non-empty string keys and values")
        return value


class PythonToolNodeConfig(NodeConfig):
    """Local Python function node. Callable must be in python_tools.yaml allowlist."""

    kind: Literal["python_tool"] = "python_tool"
    callable_path: str = Field(min_length=1)
    inputs: dict[str, str] = Field(default_factory=dict)
    output_state_key: str = Field(min_length=1)

    @field_validator("callable_path")
    @classmethod
    def _allowlisted(cls, v: str) -> str:
        from backend.builder.python_tool_allowlist import load_allowlist

        if v not in load_allowlist():
            raise ValueError(
                f"callable_path {v!r} is not in the python_tools.yaml allowlist"
            )
        return v


class McpServerEntry(BaseModel):
    """Single MCP server entry for agent startup checks."""

    model_config = ConfigDict(extra="ignore")

    name: str = ""
    transport: str = ""
    health_check: str = ""


class McpRegistryConfig(BaseModel):
    """MCP registry block on ``agent_startup`` nodes."""

    model_config = ConfigDict(extra="ignore")

    transports: list[str] = Field(default_factory=list)
    capability_registration: str = ""
    context_cost: str = ""
    health_check: str = ""
    servers: list[McpServerEntry] = Field(default_factory=list)


class AgentMemorySource(BaseModel):
    """Declarative memory tier for ``agent_context``."""

    model_config = ConfigDict(extra="ignore")

    tier: str = ""
    store: str = ""


class AgentStartupNodeConfig(NodeConfig):
    kind: Literal["agent_startup"] = "agent_startup"
    model_config = ConfigDict(extra="ignore")

    output_state_key: str = Field(default="runtime_config", min_length=1)
    provider_env_vars: list[str] = Field(default_factory=list)
    policy_config_path: str = ".claude/agent_policy.json"
    mcp_registry: McpRegistryConfig = Field(default_factory=McpRegistryConfig)


class AgentContextNodeConfig(NodeConfig):
    kind: Literal["agent_context"] = "agent_context"
    model_config = ConfigDict(extra="ignore")

    runtime_config_state_key: str = "runtime_config"
    memory_sources: list[AgentMemorySource] = Field(default_factory=list)
    include_files: list[str] = Field(default_factory=list)
    output_state_key: str = Field(default="assembled_context", min_length=1)
    max_file_chars: int = Field(5000, gt=0)


class AgentModelNodeConfig(NodeConfig):
    kind: Literal["agent_model"] = "agent_model"
    model_config = ConfigDict(extra="ignore")

    provider: Literal["openrouter", "openai"] = "openrouter"
    model: str
    system_prompt: str
    user_prompt_template: str
    output_state_key: str = Field(default="agent_model_response", min_length=1)
    temperature: float = 0.2
    max_tokens: int | None = None
    max_retries: int = 3


class AgentResponseParserNodeConfig(NodeConfig):
    kind: Literal["agent_response_parser"] = "agent_response_parser"
    model_config = ConfigDict(extra="ignore")

    input_state_key: str = Field(default="agent_model_response", min_length=1)
    route_state_key: str = Field(default="agent_route", min_length=1)
    tool_request_state_key: str = Field(default="tool_request", min_length=1)
    final_answer_state_key: str = Field(default="final_answer", min_length=1)
    should_stop_state_key: str = Field(default="agent_should_stop", min_length=1)


class PermissionGateNodeConfig(NodeConfig):
    kind: Literal["permission_gate"] = "permission_gate"
    model_config = ConfigDict(extra="ignore")

    mode: str = "default"
    ml_classifier: bool = False
    policy_stack: list[dict[str, Any]] = Field(default_factory=list)
    escalate_on: list[str] = Field(default_factory=list)
    never_restore_on_resume: bool = False
    tool_request_state_key: str = Field(default="tool_request", min_length=1)
    route_state_key: str = Field(default="permission_route", min_length=1)
    decision_state_key: str = Field(default="permission_decision", min_length=1)
    history_state_key: str = Field(default="tool_result_history", min_length=1)
    write_scope: str = ""


class HookSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = ""
    command: str = ""
    timeout_s: float = Field(30.0, gt=0)
    blocking: bool = True
    applies_to: list[str] | None = None


class HookRunnerNodeConfig(NodeConfig):
    kind: Literal["hook_runner"] = "hook_runner"
    model_config = ConfigDict(extra="ignore")

    trigger: str = Field(min_length=1)
    hooks: list[HookSpec] = Field(default_factory=list)
    tool_request_state_key: str = Field(default="tool_request", min_length=1)
    output_state_key: str = Field(min_length=1)


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str


class ToolExecutorNodeConfig(NodeConfig):
    kind: Literal["tool_executor"] = "tool_executor"
    model_config = ConfigDict(extra="ignore")

    tool_request_state_key: str = Field(default="tool_request", min_length=1)
    output_state_key: str = Field(default="tool_result", min_length=1)
    history_state_key: str = Field(default="tool_result_history", min_length=1)
    runtime_config_state_key: str = "runtime_config"
    tools: list[ToolSpec] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    cwd_only: bool = True
    context_pressure_state_key: str = Field(default="context_pressure_exceeded", min_length=1)
    compaction_threshold_chars: int = Field(12_000, gt=0)


class CompactorPipelineStage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage: str
    model: str | None = None
    preserves_last_n_turns: int | None = None
    max_verbose_turns: int | None = None
    max_chars: int | None = None
    collapse_same_role: bool | None = None
    similarity_threshold: float | None = None
    replace_file_content_with: str | None = None


class ContextCompactorNodeConfig(NodeConfig):
    kind: Literal["context_compactor"] = "context_compactor"
    model_config = ConfigDict(extra="ignore")

    history_state_key: str = Field(default="tool_result_history", min_length=1)
    agent_messages_state_key: str = Field(default="agent_messages", min_length=1)
    output_state_key: str = Field(default="compacted_context", min_length=1)
    preserve_last_n: int = Field(3, gt=0)
    max_chars: int = Field(8000, gt=0)
    context_pressure_state_key: str = Field(default="context_pressure_exceeded", min_length=1)
    pipeline: list[CompactorPipelineStage] = Field(default_factory=list)
    always_preserve: list[str] = Field(default_factory=list)


class SubagentOrchestratorNodeConfig(NodeConfig):
    kind: Literal["subagent_orchestrator"] = "subagent_orchestrator"
    model_config = ConfigDict(extra="ignore")

    tool_request_state_key: str = Field(default="tool_request", min_length=1)
    output_state_key: str = Field(default="subagent_results", min_length=1)
    history_state_key: str = Field(default="tool_result_history", min_length=1)
    topology: str = ""
    context_isolation: str = ""
    return_mode: str = ""
    max_parallel: int = 1
    merge_strategy: str = ""
    token_cost_multiplier: float = 1.0


class SubagentPlanNodeConfig(NodeConfig):
    kind: Literal["subagent_plan"] = "subagent_plan"
    model_config = ConfigDict(extra="ignore")

    tool_request_state_key: str = Field(default="tool_request", min_length=1)
    user_input_state_key: str = Field(default="user_input", min_length=1)
    output_tasks_state_key: str = Field(default="subagent_tasks", min_length=1)


class SubagentContextNodeConfig(NodeConfig):
    kind: Literal["subagent_context"] = "subagent_context"
    model_config = ConfigDict(extra="ignore")

    tasks_state_key: str = Field(default="subagent_tasks", min_length=1)
    user_input_state_key: str = Field(default="user_input", min_length=1)
    task_plan_state_key: str = Field(default="task_plan", min_length=1)
    compacted_context_state_key: str = Field(default="compacted_context", min_length=1)
    assembled_context_state_key: str = Field(default="assembled_context", min_length=1)
    tool_result_history_state_key: str = Field(default="tool_result_history", min_length=1)
    max_context_chars: int = Field(8000, gt=0)
    output_child_inputs_state_key: str = Field(default="subagent_child_inputs", min_length=1)


class SubagentSpawnNodeConfig(NodeConfig):
    kind: Literal["subagent_spawn"] = "subagent_spawn"
    model_config = ConfigDict(extra="ignore")

    child_workflow: str = Field(default="claude_code_subagent_workflow", min_length=1)
    child_inputs_state_key: str = Field(default="subagent_child_inputs", min_length=1)
    execute_children: bool = True
    max_child_recursion: int = Field(30, gt=0)
    output_child_runs_state_key: str = Field(default="subagent_child_runs", min_length=1)
    lineage_dirname: str = "subagent_runs"


class SubagentJoinNodeConfig(NodeConfig):
    kind: Literal["subagent_join"] = "subagent_join"
    model_config = ConfigDict(extra="ignore")

    child_runs_state_key: str = Field(default="subagent_child_runs", min_length=1)
    output_joined_state_key: str = Field(default="subagent_joined", min_length=1)


class SubagentSummarizeNodeConfig(NodeConfig):
    kind: Literal["subagent_summarize"] = "subagent_summarize"
    model_config = ConfigDict(extra="ignore")

    joined_state_key: str = Field(default="subagent_joined", min_length=1)
    output_state_key: str = Field(default="subagent_results", min_length=1)
    history_state_key: str = Field(default="tool_result_history", min_length=1)
    max_summary_chars: int = Field(4000, gt=0)


class MemoryWriterNodeConfig(NodeConfig):
    kind: Literal["memory_writer"] = "memory_writer"
    model_config = ConfigDict(extra="ignore")

    suggestion_state_key: str = Field(min_length=1)
    write_mode: str = "artifact"
    target_path: str = ""
    output_state_key: str = "memory_write_result"


class LoopConfig(BaseModel):
    """Attached to a back-edge. M1 design rule: every loop has max_iterations."""

    loop_id: str
    back_edge_from: str  # node id that decides to loop
    back_edge_to: str  # node id to jump back to
    max_iterations: int = Field(..., gt=0)

    model_config = {"extra": "forbid"}
