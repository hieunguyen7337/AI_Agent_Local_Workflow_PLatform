"""Pydantic models for the declarative workflow source of truth."""
from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.builder.api import END
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
    MemoryWriterNodeConfig,
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

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

GraphNodeSpec = Annotated[
    LLMNodeConfig
    | EmbeddingNodeConfig
    | TesterNodeConfig
    | RetrieverNodeConfig
    | VectorRetrieverNodeConfig
    | GateNodeConfig
    | RouterNodeConfig
    | ApprovalNodeConfig
    | SubgraphNodeConfig
    | PythonToolNodeConfig
    | AgentContextNodeConfig
    | AgentStartupNodeConfig
    | AgentModelNodeConfig
    | AgentResponseParserNodeConfig
    | PermissionGateNodeConfig
    | HookRunnerNodeConfig
    | ToolExecutorNodeConfig
    | ContextCompactorNodeConfig
    | MemoryWriterNodeConfig
    | SubagentOrchestratorNodeConfig
    | SubagentPlanNodeConfig
    | SubagentContextNodeConfig
    | SubagentSpawnNodeConfig
    | SubagentJoinNodeConfig
    | SubagentSummarizeNodeConfig,
    Field(discriminator="kind"),
]


class BudgetSpec(BaseModel):
    cost_usd: float = Field(gt=0)
    latency_ms: float = Field(gt=0)

    model_config = ConfigDict(extra="forbid")


class EdgeSpec(BaseModel):
    source: str = Field(alias="from")
    target: str = Field(alias="to")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LoopSpec(BaseModel):
    source: str = Field(alias="from")
    target: str = Field(alias="to")
    max_iterations: int = Field(gt=0)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TemplateParameterSpec(BaseModel):
    key: str
    description: str
    state_key: str | None = None
    example: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        if not _SNAKE_CASE_RE.fullmatch(value):
            raise ValueError("template parameter key must be lowercase snake_case and start with a letter")
        return value


class GraphSpec(BaseModel):
    schema_version: Literal["workflow.graph/v1"] = "workflow.graph/v1"
    name: str
    description: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    template: bool = False
    template_parameters: list[TemplateParameterSpec] = Field(default_factory=list)
    budget: BudgetSpec
    entry: str
    nodes: list[GraphNodeSpec]
    edges: list[EdgeSpec] = Field(default_factory=list)
    loops: list[LoopSpec] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_graph(self) -> "GraphSpec":
        parameter_keys = [parameter.key for parameter in self.template_parameters]
        duplicate_parameter_keys = sorted(
            {parameter_key for parameter_key in parameter_keys if parameter_keys.count(parameter_key) > 1}
        )
        if duplicate_parameter_keys:
            raise ValueError(f"duplicate template parameter keys: {', '.join(duplicate_parameter_keys)}")
        if self.template_parameters and not self.template:
            raise ValueError("template_parameters require template: true")

        node_ids: list[str] = [node.id for node in self.nodes]
        duplicates = sorted({node_id for node_id in node_ids if node_ids.count(node_id) > 1})
        if duplicates:
            raise ValueError(f"duplicate node ids: {', '.join(duplicates)}")

        known = set(node_ids)
        if self.entry not in known:
            raise ValueError(f"entry node {self.entry!r} does not exist")

        for edge in self.edges:
            if edge.source not in known:
                raise ValueError(f"edge source {edge.source!r} does not exist")
            if edge.target != END and edge.target not in known:
                raise ValueError(f"edge target {edge.target!r} does not exist")

        for node in self.nodes:
            if isinstance(node, GateNodeConfig):
                if node.pass_target != END and node.pass_target not in known:
                    raise ValueError(
                        f"gate {node.id!r} pass_target {node.pass_target!r} does not exist"
                    )
                if node.fail_target != END and node.fail_target not in known:
                    raise ValueError(
                        f"gate {node.id!r} fail_target {node.fail_target!r} does not exist"
                    )
            if isinstance(node, RouterNodeConfig):
                if not node.routes:
                    raise ValueError(f"router {node.id!r} requires at least one route")
                for label, target in node.routes.items():
                    if target != END and target not in known:
                        raise ValueError(
                            f"router {node.id!r} route {label!r} target {target!r} does not exist"
                        )
                if (
                    node.default_target is not None
                    and node.default_target != END
                    and node.default_target not in known
                ):
                    raise ValueError(
                        f"router {node.id!r} default_target {node.default_target!r} does not exist"
                    )
            if isinstance(node, ApprovalNodeConfig):
                if node.approved_target != END and node.approved_target not in known:
                    raise ValueError(
                        f"approval {node.id!r} approved_target {node.approved_target!r} does not exist"
                    )
                if node.rejected_target != END and node.rejected_target not in known:
                    raise ValueError(
                        f"approval {node.id!r} rejected_target {node.rejected_target!r} does not exist"
                    )

        for loop in self.loops:
            if loop.source not in known:
                raise ValueError(f"loop source {loop.source!r} does not exist")
            if loop.target not in known:
                raise ValueError(f"loop target {loop.target!r} does not exist")

        return self
