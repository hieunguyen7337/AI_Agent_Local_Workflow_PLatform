"""GraphBuilder — the one authoring surface for M1.

Usage:

    b = GraphBuilder(name="coder_tester", cost_budget_usd=0.10, latency_budget_ms=120_000)
    b.add_node(LLMNodeConfig(id="planner", ...))
    b.add_node(LLMNodeConfig(id="coder", ...))
    b.add_node(TesterNodeConfig(id="tester", ...))
    b.add_node(GateNodeConfig(id="gate", pass_target=END, fail_target="coder"))
    b.set_entry("planner")
    b.add_edge("planner", "coder")
    b.add_edge("coder", "tester")
    b.add_edge("tester", "gate")
    b.add_loop("gate", "coder", max_iterations=3)  # gate may re-enter coder
    compiled = b.compile()
"""
from __future__ import annotations

from dataclasses import dataclass, field

from backend.runtime.errors import BuilderValidationError

from .nodes import (
    ApprovalNodeConfig,
    EmbeddingNodeConfig,
    GateNodeConfig,
    LLMNodeConfig,
    LoopConfig,
    NodeConfig,
    RetrieverNodeConfig,
    RouterNodeConfig,
    SubgraphNodeConfig,
    TesterNodeConfig,
    VectorRetrieverNodeConfig,
)
from .validation import assert_no_cycle, assert_node_exists

END = "__end__"


@dataclass
class GraphMetadata:
    name: str
    cost_budget_usd: float
    latency_budget_ms: float
    nodes: dict[str, NodeConfig]
    edges: list[tuple[str, str]]
    loops: list[LoopConfig]
    entry: str

    def node_ids(self) -> list[str]:
        return list(self.nodes.keys())

    def mermaid(self) -> str:
        lines = ["flowchart TD", "    START([START])"]
        for nid, cfg in self.nodes.items():
            label = f"{nid}<br/><small>{cfg.kind}</small>"
            lines.append(f'    {nid}["{label}"]')
        lines.append("    END([END])")
        if self.entry:
            lines.append(f"    START --> {self.entry}")
        for s, t in self.edges:
            dst = "END" if t == END else t
            lines.append(f"    {s} --> {dst}")
        loop_pairs = {(lp.back_edge_from, lp.back_edge_to) for lp in self.loops}
        for nid, cfg in self.nodes.items():
            if isinstance(cfg, GateNodeConfig):
                for target, label in ((cfg.pass_target, "pass"), (cfg.fail_target, "fail")):
                    if (nid, target) in loop_pairs:
                        continue
                    dst = "END" if target == END else target
                    lines.append(f"    {nid} -->|{label}| {dst}")
            if isinstance(cfg, ApprovalNodeConfig):
                for target, label in (
                    (cfg.approved_target, "approved"),
                    (cfg.rejected_target, "rejected"),
                ):
                    dst = "END" if target == END else target
                    lines.append(f"    {nid} -->|{label}| {dst}")
            if isinstance(cfg, RouterNodeConfig):
                for label, target in cfg.routes.items():
                    if (nid, target) in loop_pairs:
                        continue
                    dst = "END" if target == END else target
                    lines.append(f"    {nid} -->|{label}| {dst}")
                if cfg.default_target is not None and (nid, cfg.default_target) not in loop_pairs:
                    dst = "END" if cfg.default_target == END else cfg.default_target
                    lines.append(f"    {nid} -->|default| {dst}")
        for lp in self.loops:
            lines.append(f"    {lp.back_edge_from} -. loop (max {lp.max_iterations}) .-> {lp.back_edge_to}")
        return "\n".join(lines)


@dataclass
class GraphBuilder:
    name: str
    cost_budget_usd: float | None = None
    latency_budget_ms: float | None = None
    _nodes: dict[str, NodeConfig] = field(default_factory=dict)
    _edges: list[tuple[str, str]] = field(default_factory=list)
    _loops: list[LoopConfig] = field(default_factory=list)
    _entry: str | None = None

    def add_node(self, config: NodeConfig) -> str:
        if config.id in self._nodes:
            raise BuilderValidationError(f"node id {config.id!r} already exists")
        if config.id == END:
            raise BuilderValidationError(f"node id {END!r} is reserved")
        self._nodes[config.id] = config
        return config.id

    def set_entry(self, node_id: str) -> None:
        if node_id not in self._nodes:
            raise BuilderValidationError(f"entry node {node_id!r} does not exist")
        self._entry = node_id

    def add_edge(self, source: str, target: str) -> None:
        """Regular forward edge. Rejects cycles. Use add_loop() for back-edges."""
        if target != END:
            assert_node_exists(set(self._nodes.keys()), target, f"add_edge({source!r}, {target!r})")
        assert_node_exists(set(self._nodes.keys()), source, f"add_edge({source!r}, {target!r})")
        if target != END:
            assert_no_cycle(self._edges, source, target)
        self._edges.append((source, target))

    def add_loop(self, back_edge_from: str, back_edge_to: str, *, max_iterations: int) -> None:
        """Only way to create a back-edge. max_iterations is required."""
        assert_node_exists(set(self._nodes.keys()), back_edge_from, "add_loop.from")
        assert_node_exists(set(self._nodes.keys()), back_edge_to, "add_loop.to")
        if max_iterations <= 0:
            raise BuilderValidationError("max_iterations must be > 0")
        loop_id = f"{back_edge_from}->{back_edge_to}"
        self._loops.append(
            LoopConfig(
                loop_id=loop_id,
                back_edge_from=back_edge_from,
                back_edge_to=back_edge_to,
                max_iterations=max_iterations,
            )
        )

    def compile(self) -> GraphMetadata:
        if self.cost_budget_usd is None or self.latency_budget_ms is None:
            raise BuilderValidationError(
                "GraphBuilder requires cost_budget_usd and latency_budget_ms to compile"
            )
        if self._entry is None:
            raise BuilderValidationError("GraphBuilder requires an entry node (set_entry)")
        self._validate_conditionals()
        self._validate_loops()
        return GraphMetadata(
            name=self.name,
            cost_budget_usd=self.cost_budget_usd,
            latency_budget_ms=self.latency_budget_ms,
            nodes=dict(self._nodes),
            edges=list(self._edges),
            loops=list(self._loops),
            entry=self._entry,
        )

    def _validate_conditionals(self) -> None:
        node_ids = set(self._nodes.keys())
        for cfg in self._nodes.values():
            if isinstance(cfg, GateNodeConfig):
                if cfg.pass_target != END and cfg.pass_target not in node_ids:
                    raise BuilderValidationError(
                        f"gate {cfg.id!r} pass_target {cfg.pass_target!r} does not exist"
                    )
                if cfg.fail_target != END and cfg.fail_target not in node_ids:
                    raise BuilderValidationError(
                        f"gate {cfg.id!r} fail_target {cfg.fail_target!r} does not exist"
                    )
            if isinstance(cfg, ApprovalNodeConfig):
                if cfg.approved_target != END and cfg.approved_target not in node_ids:
                    raise BuilderValidationError(
                        f"approval {cfg.id!r} approved_target {cfg.approved_target!r} does not exist"
                    )
                if cfg.rejected_target != END and cfg.rejected_target not in node_ids:
                    raise BuilderValidationError(
                        f"approval {cfg.id!r} rejected_target {cfg.rejected_target!r} does not exist"
                    )
            if isinstance(cfg, RouterNodeConfig):
                if not cfg.routes:
                    raise BuilderValidationError(f"router {cfg.id!r} requires at least one route")
                for label, target in cfg.routes.items():
                    if target != END and target not in node_ids:
                        raise BuilderValidationError(
                            f"router {cfg.id!r} route {label!r} target {target!r} does not exist"
                        )
                if cfg.default_target is not None and cfg.default_target != END and cfg.default_target not in node_ids:
                    raise BuilderValidationError(
                        f"router {cfg.id!r} default_target {cfg.default_target!r} does not exist"
                    )

    def _validate_loops(self) -> None:
        loop_sources: dict[str, list[LoopConfig]] = {}
        for loop in self._loops:
            loop_sources.setdefault(loop.back_edge_from, []).append(loop)

        for source, loops in loop_sources.items():
            cfg = self._nodes[source]
            if isinstance(cfg, (GateNodeConfig, RouterNodeConfig)):
                continue
            if len(loops) > 1:
                raise BuilderValidationError(
                    f"node {source!r} has multiple loop back-edges; only one is supported"
                )
            forward_edges = [edge for edge in self._edges if edge[0] == source]
            if forward_edges:
                raise BuilderValidationError(
                    f"node {source!r} cannot have both regular outgoing edges and a loop back-edge"
                )


__all__ = [
    "END",
    "GraphBuilder",
    "GraphMetadata",
    "ApprovalNodeConfig",
    "EmbeddingNodeConfig",
    "LLMNodeConfig",
    "RetrieverNodeConfig",
    "VectorRetrieverNodeConfig",
    "TesterNodeConfig",
    "GateNodeConfig",
    "RouterNodeConfig",
    "SubgraphNodeConfig",
    "LoopConfig",
]
