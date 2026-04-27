"""Declarative GraphSpec loading and compatibility tests."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.builder.api import END
from backend.graphspec import GraphSpec, graph_spec_to_metadata, load_graph_spec, load_workflow_metadata


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
    assert spec.category == "coding"
    assert spec.tags == ["code-generation", "testing"]
    assert spec.entry == "planner"
    assert {node.id for node in spec.nodes} == {"planner", "coder", "tester", "gate"}


def test_graph_spec_accepts_library_metadata():
    spec = GraphSpec.model_validate(
        {
            "name": "library_item",
            "description": "A searchable workflow.",
            "category": "rag",
            "tags": ["retrieval", "example"],
            "template": True,
            "template_parameters": [
                {
                    "key": "user_input",
                    "description": "Primary user task.",
                    "state_key": "user_input",
                    "example": "Summarize this.",
                }
            ],
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
        }
    )

    assert spec.category == "rag"
    assert spec.tags == ["retrieval", "example"]
    assert spec.template is True
    assert len(spec.template_parameters) == 1
    assert spec.template_parameters[0].key == "user_input"


def test_graph_spec_defaults_library_metadata():
    spec = GraphSpec.model_validate(
        {
            "name": "library_item",
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
        }
    )

    assert spec.category == "general"
    assert spec.tags == []
    assert spec.template is False
    assert spec.template_parameters == []


def test_graph_spec_rejects_bad_template_parameters():
    base = {
        "name": "bad_template",
        "template": True,
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
    }

    with pytest.raises(ValidationError, match="lowercase snake_case"):
        GraphSpec.model_validate(
            {
                **base,
                "template_parameters": [{"key": "Bad-Key", "description": "Nope."}],
            }
        )

    with pytest.raises(ValidationError, match="duplicate template parameter keys"):
        GraphSpec.model_validate(
            {
                **base,
                "template_parameters": [
                    {"key": "user_input", "description": "One."},
                    {"key": "user_input", "description": "Two."},
                ],
            }
        )

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GraphSpec.model_validate(
            {
                **base,
                "template_parameters": [{"key": "user_input", "description": "Task.", "required": True}],
            }
        )

    with pytest.raises(ValidationError, match="template_parameters require template: true"):
        GraphSpec.model_validate(
            {
                **base,
                "template": False,
                "template_parameters": [{"key": "user_input", "description": "Task."}],
            }
        )


def test_graph_spec_rejects_unknown_library_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "author": "not supported",
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
            }
        )


def test_loads_approval_graph_spec():
    spec = load_graph_spec("approval_review")
    assert spec.name == "approval_review"
    approval = next(node for node in spec.nodes if node.id == "human_review")
    assert approval.kind == "approval"
    assert approval.approved_target == "finalizer"
    assert approval.rejected_target == END


def test_loads_subgraph_graph_spec():
    spec = load_graph_spec("rag_subgraph_wrapper")
    assert spec.name == "rag_subgraph_wrapper"
    subgraph = next(node for node in spec.nodes if node.id == "rag_child")
    assert subgraph.kind == "subgraph"
    assert subgraph.workflow == "linear_rag"
    assert subgraph.inputs == {"user_input": "user_input"}
    assert subgraph.outputs == {"final_answer": "rag_answer"}


def test_graph_spec_converts_to_metadata():
    metadata = graph_spec_to_metadata(load_graph_spec("linear_rag"))
    assert metadata.name == "linear_rag"
    assert metadata.entry == "query_analyser"
    assert metadata.nodes["retriever"].kind == "retriever"


def test_missing_workflow_metadata_requires_yaml_spec():
    with pytest.raises(FileNotFoundError, match="workflow spec .*does not exist"):
        load_workflow_metadata("does_not_exist")


def test_canonical_yaml_workflow_shapes():
    expected = {
        "coder_tester": {
            "name": "coder_tester",
            "budget": (0.5, 300000.0),
            "entry": "planner",
            "nodes": {"planner": "llm", "coder": "llm", "tester": "tester", "gate": "gate"},
            "edges": [("coder", "tester"), ("planner", "coder"), ("tester", "gate")],
            "loops": [("gate", "coder", 3)],
        },
        "linear_rag": {
            "name": "linear_rag",
            "budget": (0.5, 240000.0),
            "entry": "query_analyser",
            "nodes": {
                "query_analyser": "llm",
                "retriever": "retriever",
                "reranker": "llm",
                "synthesiser": "llm",
            },
            "edges": [
                ("query_analyser", "retriever"),
                ("reranker", "synthesiser"),
                ("retriever", "reranker"),
                ("synthesiser", END),
            ],
            "loops": [],
        },
        "supervisor_loop": {
            "name": "supervisor_loop",
            "budget": (0.6, 300000.0),
            "entry": "supervisor",
            "nodes": {
                "supervisor": "llm",
                "dispatch": "router",
                "researcher": "llm",
                "writer": "llm",
            },
            "edges": [("supervisor", "dispatch")],
            "loops": [("researcher", "supervisor", 4), ("writer", "supervisor", 4)],
        },
        "dispatch_aggregate": {
            "name": "dispatch_aggregate",
            "budget": (0.6, 300000.0),
            "entry": "dispatcher",
            "nodes": {
                "dispatcher": "llm",
                "specialist_a": "llm",
                "specialist_b": "llm",
                "aggregator": "llm",
            },
            "edges": [
                ("aggregator", END),
                ("dispatcher", "specialist_a"),
                ("dispatcher", "specialist_b"),
                ("specialist_a", "aggregator"),
                ("specialist_b", "aggregator"),
            ],
            "loops": [],
        },
    }
    for workflow, shape in expected.items():
        yaml_metadata = graph_spec_to_metadata(load_graph_spec(workflow))
        assert _shape(yaml_metadata) == shape


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


def test_graph_spec_rejects_empty_subgraph_mappings():
    with pytest.raises(ValidationError, match="Dictionary should have at least 1 item"):
        GraphSpec.model_validate(
            {
                "name": "bad",
                "budget": {"cost_usd": 0.1, "latency_ms": 1000},
                "entry": "child",
                "nodes": [
                    {
                        "id": "child",
                        "kind": "subgraph",
                        "workflow": "linear_rag",
                        "inputs": {},
                        "outputs": {"final_answer": "rag_answer"},
                    }
                ],
            }
        )


def test_load_graph_spec_rejects_missing_subgraph_workflow(tmp_path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "parent.yaml").write_text(
        """
name: parent
budget:
  cost_usd: 0.1
  latency_ms: 1000
entry: child
nodes:
  - id: child
    kind: subgraph
    workflow: missing_child
    inputs:
      user_input: user_input
    outputs:
      final_answer: answer
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match="subgraph workflow 'missing_child' does not exist"):
        load_graph_spec("parent", specs_root=specs_root)


def test_load_graph_spec_rejects_subgraph_cycles(tmp_path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    for name, child in (("a", "b"), ("b", "a")):
        (specs_root / f"{name}.yaml").write_text(
            f"""
name: {name}
budget:
  cost_usd: 0.1
  latency_ms: 1000
entry: child
nodes:
  - id: child
    kind: subgraph
    workflow: {child}
    inputs:
      user_input: user_input
    outputs:
      final_answer: answer
""".strip(),
            encoding="utf-8",
        )
    with pytest.raises(ValueError, match="subgraph reference cycle detected: a -> b -> a"):
        load_graph_spec("a", specs_root=specs_root)


def test_load_graph_spec_accepts_approval_subgraph_targets(tmp_path):
    specs_root = tmp_path / "workflows"
    specs_root.mkdir()
    (specs_root / "parent.yaml").write_text(
        """
name: parent
budget:
  cost_usd: 0.1
  latency_ms: 1000
entry: child
nodes:
  - id: child
    kind: subgraph
    workflow: child_wf
    inputs:
      user_input: user_input
    outputs:
      final_answer: answer
""".strip(),
        encoding="utf-8",
    )
    (specs_root / "child_wf.yaml").write_text(
        f"""
name: child_wf
budget:
  cost_usd: 0.1
  latency_ms: 1000
entry: review
nodes:
  - id: review
    kind: approval
    prompt: Review.
    approved_target: {END}
    rejected_target: {END}
""".strip(),
        encoding="utf-8",
    )
    spec = load_graph_spec("parent", specs_root=specs_root)
    assert spec.name == "parent"
    subgraph_node = next(node for node in spec.nodes if node.id == "child")
    assert subgraph_node.workflow == "child_wf"


def test_load_approval_subgraph_wrapper():
    spec = load_graph_spec("approval_subgraph_wrapper")
    assert spec.name == "approval_subgraph_wrapper"
    subgraph_node = next(node for node in spec.nodes if node.id == "review_child")
    assert subgraph_node.kind == "subgraph"
    assert subgraph_node.workflow == "approval_review"
