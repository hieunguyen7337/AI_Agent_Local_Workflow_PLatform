"""Compile-time validation rules."""
from __future__ import annotations

import pytest

from backend.builder.api import END, GateNodeConfig, GraphBuilder, LLMNodeConfig, TesterNodeConfig
from backend.runtime.errors import BuilderValidationError


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
