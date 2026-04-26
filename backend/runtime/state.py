"""Workflow runtime state. LangGraph-compatible TypedDict.

For M1, state is intentionally small and workflow-agnostic-ish: the coder/tester loop
only needs user_input, coder_output, tester_verdict, tester_feedback plus accounting.
"""
from __future__ import annotations

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    user_input: str
    plan: str
    coder_output: str
    tester_verdict: bool
    tester_feedback: str
    query_analysis: str
    retrieved_context: str
    reranked_context: str
    final_answer: str
    rag_answer: str
    draft_answer: str
    supervisor_route: str
    research_notes: str
    dispatch_brief: str
    specialist_a_notes: str
    specialist_b_notes: str
    retrieved_doc_ids: list[str]
    _test_code: str
    tester_mode: str
    approval_decision: str
    pending_approval: dict[str, Any]
    nested_final_answer: str
    pending_subgraph_approval: dict[str, Any]
    _subgraph_resume: dict[str, Any]

    iteration_counts: dict[str, int]
    cost_usd_accum: float
    latency_ms_accum: float
    artifacts: dict[str, Any]
    messages: list[dict]


def new_state(user_input: str) -> WorkflowState:
    return WorkflowState(
        user_input=user_input,
        plan="",
        coder_output="",
        tester_verdict=False,
        tester_feedback="",
        query_analysis="",
        retrieved_context="",
        reranked_context="",
        final_answer="",
        rag_answer="",
        draft_answer="",
        supervisor_route="",
        research_notes="",
        dispatch_brief="",
        specialist_a_notes="",
        specialist_b_notes="",
        retrieved_doc_ids=[],
        _test_code="",
        tester_mode="",
        approval_decision="",
        pending_approval={},
        nested_final_answer="",
        pending_subgraph_approval={},
        _subgraph_resume={},
        iteration_counts={},
        cost_usd_accum=0.0,
        latency_ms_accum=0.0,
        artifacts={},
        messages=[],
    )
