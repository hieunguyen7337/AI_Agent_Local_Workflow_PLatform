"""Load YAML GraphSpec files and adapt them to runtime metadata."""
from __future__ import annotations

from pathlib import Path

import yaml

from backend.builder.api import GraphBuilder, GraphMetadata
from backend.builder.nodes import ApprovalNodeConfig, SubgraphNodeConfig
from .models import BudgetSpec, EdgeSpec, GraphSpec, LoopSpec

WORKFLOW_SPECS_ROOT = Path("workflows")


def graph_spec_path(workflow_id: str, *, specs_root: Path = WORKFLOW_SPECS_ROOT) -> Path:
    return specs_root / f"{workflow_id}.yaml"


def load_graph_spec_source(
    workflow_id: str, *, specs_root: Path = WORKFLOW_SPECS_ROOT
) -> tuple[GraphSpec, str, Path]:
    path = graph_spec_path(workflow_id, specs_root=specs_root)
    if not path.exists():
        raise FileNotFoundError(f"workflow spec {path} does not exist")
    text = path.read_text(encoding="utf-8")
    payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        raise ValueError(f"workflow spec {path} must contain a YAML mapping")
    spec = GraphSpec.model_validate(payload)
    _validate_subgraph_references(workflow_id, spec, specs_root=specs_root)
    return spec, text, path


def load_graph_spec(workflow_id: str, *, specs_root: Path = WORKFLOW_SPECS_ROOT) -> GraphSpec:
    spec, _text, _path = load_graph_spec_source(workflow_id, specs_root=specs_root)
    return spec


def _load_raw_graph_spec(workflow_id: str, *, specs_root: Path) -> GraphSpec:
    path = graph_spec_path(workflow_id, specs_root=specs_root)
    if not path.exists():
        raise FileNotFoundError(f"workflow spec {path} does not exist")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"workflow spec {path} must contain a YAML mapping")
    return GraphSpec.model_validate(payload)


def _validate_subgraph_references(
    workflow_id: str,
    spec: GraphSpec,
    *,
    specs_root: Path,
) -> None:
    visiting: list[str] = [workflow_id]
    _validate_subgraph_tree(workflow_id, spec, specs_root=specs_root, visiting=visiting)


def _validate_subgraph_tree(
    workflow_id: str,
    spec: GraphSpec,
    *,
    specs_root: Path,
    visiting: list[str],
) -> None:
    for node in spec.nodes:
        if not isinstance(node, SubgraphNodeConfig):
            continue
        if node.workflow == workflow_id:
            raise ValueError(f"subgraph {node.id!r} cannot reference its own workflow")
        if node.workflow in visiting:
            cycle = " -> ".join([*visiting, node.workflow])
            raise ValueError(f"subgraph reference cycle detected: {cycle}")
        if not graph_spec_path(node.workflow, specs_root=specs_root).exists():
            raise FileNotFoundError(f"subgraph workflow {node.workflow!r} does not exist")

        child = _load_raw_graph_spec(node.workflow, specs_root=specs_root)
        approval_nodes = [child_node.id for child_node in child.nodes if isinstance(child_node, ApprovalNodeConfig)]
        if approval_nodes:
            raise ValueError(
                f"subgraph {node.id!r} references workflow {node.workflow!r} with approval nodes; "
                "nested approval subgraphs are not supported in v1"
            )
        _validate_subgraph_tree(
            node.workflow,
            child,
            specs_root=specs_root,
            visiting=[*visiting, node.workflow],
        )


def graph_spec_to_metadata(spec: GraphSpec) -> GraphMetadata:
    builder = GraphBuilder(
        name=spec.name,
        cost_budget_usd=spec.budget.cost_usd,
        latency_budget_ms=spec.budget.latency_ms,
    )
    for node in spec.nodes:
        builder.add_node(node)
    builder.set_entry(spec.entry)
    for edge in spec.edges:
        builder.add_edge(edge.source, edge.target)
    for loop in spec.loops:
        builder.add_loop(loop.source, loop.target, max_iterations=loop.max_iterations)
    return builder.compile()


def load_workflow_metadata(workflow_id: str, *, specs_root: Path = WORKFLOW_SPECS_ROOT) -> GraphMetadata:
    return graph_spec_to_metadata(load_graph_spec(workflow_id, specs_root=specs_root))


def builder_to_graph_spec(builder: GraphBuilder) -> GraphSpec:
    metadata = builder.compile()
    return GraphSpec(
        name=metadata.name,
        budget=BudgetSpec(
            cost_usd=metadata.cost_budget_usd,
            latency_ms=metadata.latency_budget_ms,
        ),
        entry=metadata.entry,
        nodes=list(metadata.nodes.values()),
        edges=[EdgeSpec(source=source, target=target) for source, target in metadata.edges],
        loops=[
            LoopSpec(
                source=loop.back_edge_from,
                target=loop.back_edge_to,
                max_iterations=loop.max_iterations,
            )
            for loop in metadata.loops
        ],
    )
