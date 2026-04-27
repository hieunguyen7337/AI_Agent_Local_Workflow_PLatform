"""Pydantic models for the declarative workflow source of truth."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.builder.api import END
from backend.builder.nodes import (
    ApprovalNodeConfig,
    GateNodeConfig,
    LLMNodeConfig,
    RetrieverNodeConfig,
    RouterNodeConfig,
    SubgraphNodeConfig,
    TesterNodeConfig,
)

GraphNodeSpec = Annotated[
    LLMNodeConfig
    | TesterNodeConfig
    | RetrieverNodeConfig
    | GateNodeConfig
    | RouterNodeConfig
    | ApprovalNodeConfig
    | SubgraphNodeConfig,
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


class GraphSpec(BaseModel):
    schema_version: Literal["workflow.graph/v1"] = "workflow.graph/v1"
    name: str
    description: str = ""
    category: str = "general"
    tags: list[str] = Field(default_factory=list)
    template: bool = False
    budget: BudgetSpec
    entry: str
    nodes: list[GraphNodeSpec]
    edges: list[EdgeSpec] = Field(default_factory=list)
    loops: list[LoopSpec] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _validate_graph(self) -> "GraphSpec":
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
