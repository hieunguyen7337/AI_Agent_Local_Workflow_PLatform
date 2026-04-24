"""Compile-time validation rules."""
from __future__ import annotations

import pytest

from backend.builder.api import (
    END,
    ApprovalNodeConfig,
    GateNodeConfig,
    GraphBuilder,
    LLMNodeConfig,
    RouterNodeConfig,
    TesterNodeConfig,
)
from backend.builder.compile import compile_to_langgraph
from backend.runtime.errors import BuilderValidationError
from backend.runtime.state import WorkflowState


def _basic_builder() -> GraphBuilder:
    return GraphBuilder(name="t", cost_budget_usd=0.1, latency_budget_ms=10_000)


def _llm(node_id: str) -> LLMNodeConfig:
    return LLMNodeConfig(
        id=node_id,
        model="minimax/minimax-m2.7",
        system_prompt="sp",
        user_prompt_template="up",
        output_state_key=f"{node_id}_out",
    )


def test_compile_without_budget_raises():
    b = GraphBuilder(name="t", latency_budget_ms=1000)  # missing cost
    b.add_node(_llm("a"))
    b.set_entry("a")
    b.add_edge("a", END)
    with pytest.raises(BuilderValidationError):
        b.compile()


def test_compile_without_entry_raises():
    b = _basic_builder()
    b.add_node(_llm("a"))
    with pytest.raises(BuilderValidationError):
        b.compile()


def test_add_edge_rejects_cycle():
    b = _basic_builder()
    b.add_node(_llm("a"))
    b.add_node(_llm("b"))
    b.set_entry("a")
    b.add_edge("a", "b")
    with pytest.raises(BuilderValidationError):
        b.add_edge("b", "a")  # would create cycle


def test_add_loop_allows_back_edge_with_max_iterations():
    b = _basic_builder()
    b.add_node(_llm("a"))
    b.add_node(_llm("b"))
    b.set_entry("a")
    b.add_edge("a", "b")
    b.add_loop("b", "a", max_iterations=3)  # should not raise
    m = b.compile()
    assert len(m.loops) == 1
    assert m.loops[0].max_iterations == 3


def test_add_loop_rejects_zero_max_iterations():
    b = _basic_builder()
    b.add_node(_llm("a"))
    b.add_node(_llm("b"))
    b.set_entry("a")
    b.add_edge("a", "b")
    with pytest.raises(BuilderValidationError):
        b.add_loop("b", "a", max_iterations=0)


def test_add_edge_rejects_unknown_node():
    b = _basic_builder()
    b.add_node(_llm("a"))
    b.set_entry("a")
    with pytest.raises(BuilderValidationError):
        b.add_edge("a", "nope")


def test_duplicate_node_id_rejected():
    b = _basic_builder()
    b.add_node(_llm("a"))
    with pytest.raises(BuilderValidationError):
        b.add_node(_llm("a"))


def test_gate_target_validation():
    b = _basic_builder()
    b.add_node(_llm("coder"))
    b.add_node(TesterNodeConfig(id="tester", model="minimax/minimax-m2.7"))
    b.add_node(GateNodeConfig(id="gate", pass_target=END, fail_target="coder"))
    b.set_entry("coder")
    b.add_edge("coder", "tester")
    b.add_edge("tester", "gate")
    b.add_loop("gate", "coder", max_iterations=2)
    m = b.compile()
    assert "gate" in m.nodes


def test_router_target_validation():
    b = _basic_builder()
    b.add_node(_llm("supervisor"))
    b.add_node(
        RouterNodeConfig(
            id="dispatch",
            route_state_key="supervisor_route",
            routes={"NEXT": "supervisor", "FINISH": END},
        )
    )
    b.set_entry("supervisor")
    b.add_edge("supervisor", "dispatch")
    m = b.compile()
    assert "dispatch" in m.nodes


def test_approval_target_validation():
    b = _basic_builder()
    b.add_node(
        ApprovalNodeConfig(
            id="review",
            prompt="Review.",
            approved_target=END,
            rejected_target="missing",
        )
    )
    b.set_entry("review")
    with pytest.raises(BuilderValidationError, match="rejected_target"):
        b.compile()


def test_non_conditional_loop_source_rejects_forward_edges():
    b = _basic_builder()
    b.add_node(_llm("a"))
    b.add_node(_llm("b"))
    b.add_node(_llm("c"))
    b.set_entry("a")
    b.add_edge("a", "b")
    b.add_edge("b", "c")
    b.add_loop("b", "a", max_iterations=2)
    with pytest.raises(BuilderValidationError):
        b.compile()


def test_mermaid_export_contains_nodes():
    b = _basic_builder()
    b.add_node(_llm("a"))
    b.add_node(_llm("b"))
    b.set_entry("a")
    b.add_edge("a", "b")
    b.add_edge("b", END)
    m = b.compile()
    mm = m.mermaid()
    assert "flowchart" in mm
    assert "a" in mm and "b" in mm
    assert "END" in mm


def test_fanout_join_executes_aggregator_after_both_branches():
    b = _basic_builder()
    b.add_node(
        LLMNodeConfig(
            id="dispatcher",
            model="m",
            system_prompt="s",
            user_prompt_template="{user_input}",
            output_state_key="dispatch_brief",
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="specialist_a",
            model="m",
            system_prompt="s",
            user_prompt_template="{dispatch_brief}",
            output_state_key="specialist_a_notes",
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="specialist_b",
            model="m",
            system_prompt="s",
            user_prompt_template="{dispatch_brief}",
            output_state_key="specialist_b_notes",
        )
    )
    b.add_node(
        LLMNodeConfig(
            id="aggregator",
            model="m",
            system_prompt="s",
            user_prompt_template="{specialist_a_notes}\n{specialist_b_notes}",
            output_state_key="final_answer",
        )
    )
    b.set_entry("dispatcher")
    b.add_edge("dispatcher", "specialist_a")
    b.add_edge("dispatcher", "specialist_b")
    b.add_edge("specialist_a", "aggregator")
    b.add_edge("specialist_b", "aggregator")
    b.add_edge("aggregator", END)

    call_order: list[str] = []

    def node_factory(cfg, metadata):
        def _node(state: WorkflowState):
            call_order.append(cfg.id)
            return {cfg.output_state_key: cfg.id}

        return _node

    app = compile_to_langgraph(
        b.compile(),
        node_factory=node_factory,
        gate_router_factory=lambda cfg, loop: (_ for _ in ()).throw(AssertionError("no gates expected")),
        router_dispatch_factory=lambda cfg: (_ for _ in ()).throw(AssertionError("no routers expected")),
        approval_dispatch_factory=lambda cfg: (_ for _ in ()).throw(AssertionError("no approvals expected")),
    ).compile()
    out = app.invoke(
        {
            "user_input": "x",
            "iteration_counts": {},
            "artifacts": {},
            "messages": [],
        },
        config={"configurable": {"thread_id": "fanout-test"}},
    )

    assert call_order[0] == "dispatcher"
    assert set(call_order[1:3]) == {"specialist_a", "specialist_b"}
    assert call_order[-1] == "aggregator"
    assert out["dispatch_brief"] == "dispatcher"
    assert out["specialist_a_notes"] == "specialist_a"
    assert out["specialist_b_notes"] == "specialist_b"
    assert out["final_answer"] == "aggregator"
