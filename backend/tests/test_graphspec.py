"""Declarative GraphSpec loading and compatibility tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.builder.api import END
from backend.graphspec import GraphSpec, graph_spec_to_metadata, load_graph_spec
from backend.workflows import coder_tester, dispatch_aggregate, linear_rag, supervisor_loop


def _shape(metadata):
    return {
        "name": metadata.name,
        "budget": (metadata.cost_budget_usd, metadata.latency_budget_ms),
        "entry": metadata.entry,
        "nodes": {node_id: cfg.kind for node_id, cfg in metadata.nodes.items()},
        "edges": sorted(metadata.edges),
        "loops": sorted(
            (loop.back_edge_from, loop.back_edge_to, loop.max_iterations)
            for loop in metadata.loops
        ),
    }


def test_loads_yaml_graph_spec():
    spec = load_graph_spec("coder_tester")
    assert spec.name == "coder_tester"
    assert spec.entry == "planner"
    assert {node.id for node in spec.nodes} == {"planner", "coder", "tester", "gate"}


def test_loads_approval_graph_spec():
    spec = load_graph_spec("approval_review")
    assert spec.name == "approval_review"
    approval = next(node for node in spec.nodes if node.id == "human_review")
    assert approval.kind == "approval"
    assert approval.approved_target == "finalizer"
    assert approval.rejected_target == END


def test_graph_spec_converts_to_metadata():
    metadata = graph_spec_to_metadata(load_graph_spec("linear_rag"))
    assert metadata.name == "linear_rag"
    assert metadata.entry == "query_analyser"
    assert metadata.nodes["retriever"].kind == "retriever"


def test_yaml_workflow_shapes_match_python_builders():
    expected = {
        "coder_tester": coder_tester.build_compiled(),
        "linear_rag": linear_rag.build_compiled(),
        "supervisor_loop": supervisor_loop.build_compiled(),
        "dispatch_aggregate": dispatch_aggregate.build_compiled(),
    }
    for workflow, python_metadata in expected.items():
        yaml_metadata = graph_spec_to_metadata(load_graph_spec(workflow))
        assert _shape(yaml_metadata) == _shape(python_metadata)


def test_graph_spec_rejects_duplicate_node_ids():
    with pytest.raises(ValidationError, match="duplicate node ids"):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "budget": {"cost_usd": 0.1, "latency_ms": 1000},
                "entry": "a",
                "nodes": [
                    {
                        "id": "a",
                        "kind": "llm",
                        "model": "m",
                        "system_prompt": "s",
                        "user_prompt_template": "{user_input}",
                        "output_state_key": "x",
                    },
                    {
                        "id": "a",
                        "kind": "tester",
                        "model": "m",
                    },
                ],
            }
        )


def test_graph_spec_rejects_missing_entry_and_targets():
    with pytest.raises(ValidationError, match="entry node 'missing' does not exist"):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "budget": {"cost_usd": 0.1, "latency_ms": 1000},
                "entry": "missing",
                "nodes": [
                    {
                        "id": "a",
                        "kind": "llm",
                        "model": "m",
                        "system_prompt": "s",
                        "user_prompt_template": "{user_input}",
                        "output_state_key": "x",
                    }
                ],
            }
        )

    with pytest.raises(ValidationError, match="edge target 'missing' does not exist"):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "budget": {"cost_usd": 0.1, "latency_ms": 1000},
                "entry": "a",
                "nodes": [
                    {
                        "id": "a",
                        "kind": "llm",
                        "model": "m",
                        "system_prompt": "s",
                        "user_prompt_template": "{user_input}",
                        "output_state_key": "x",
                    }
                ],
                "edges": [{"from": "a", "to": "missing"}],
            }
        )


def test_graph_spec_rejects_bad_router_and_loop_targets():
    with pytest.raises(ValidationError, match="route 'NEXT' target 'missing' does not exist"):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "budget": {"cost_usd": 0.1, "latency_ms": 1000},
                "entry": "dispatch",
                "nodes": [
                    {
                        "id": "dispatch",
                        "kind": "router",
                        "route_state_key": "route",
                        "routes": {"NEXT": "missing", "FINISH": END},
                    }
                ],
            }
        )

    with pytest.raises(ValidationError, match="loop target 'missing' does not exist"):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "budget": {"cost_usd": 0.1, "latency_ms": 1000},
                "entry": "a",
                "nodes": [
                    {
                        "id": "a",
                        "kind": "llm",
                        "model": "m",
                        "system_prompt": "s",
                        "user_prompt_template": "{user_input}",
                        "output_state_key": "x",
                    }
                ],
                "loops": [{"from": "a", "to": "missing", "max_iterations": 1}],
            }
        )


def test_graph_spec_rejects_bad_approval_targets():
    with pytest.raises(ValidationError, match="approved_target 'missing' does not exist"):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "budget": {"cost_usd": 0.1, "latency_ms": 1000},
                "entry": "review",
                "nodes": [
                    {
                        "id": "review",
                        "kind": "approval",
                        "prompt": "Review this.",
                        "approved_target": "missing",
                        "rejected_target": END,
                    }
                ],
            }
        )


def test_graph_spec_rejects_unsupported_node_kind():
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "budget": {"cost_usd": 0.1, "latency_ms": 1000},
                "entry": "a",
                "nodes": [{"id": "a", "kind": "unknown"}],
            }
        )
