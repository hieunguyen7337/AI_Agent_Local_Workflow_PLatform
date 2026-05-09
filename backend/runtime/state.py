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
    query_embedding: list[float]
    retrieved_vector_doc_ids: list[str]
    reranked_context: str
    final_answer: str
    rag_answer: str
    draft_answer: str
    supervisor_route: str
    research_notes: str
    dispatch_brief: str
    start_marker: str
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

    query_id: str
    query_image_path: str
    query_db_path: str
    query_embedding_db_path: str
    gallery_db_path: str
    gallery_embedding_db_path: str
    retrieval_top_k: int
    query_pid: int
    query_camid: int
    gallery_ids: list[str]
    gallery_pids: list[int]
    gallery_camids: list[int]
    query_multimodal_embedding: list[float]
    reid_multimodal_embedding_ranked: list[str]
    rrf_merged_ranking: list[str]
    reciprocal_rank_fused_ranking: list[str]
    ranked_gallery_ids: list[str]
    ranked_gallery_pids: list[str] | str

    iteration_counts: dict[str, int]
    cost_usd_accum: float
    latency_ms_accum: float
    artifacts: dict[str, Any]
    messages: list[dict]

    # Coding-agent / Claude-style YAML workflows (claude_code_*)
    runtime_config: dict[str, Any]
    assembled_context: str
    compacted_context: str
    agent_model_response: str
    agent_route: str
    agent_should_stop: bool
    tool_request: dict[str, Any]
    permission_route: str
    permission_decision: dict[str, Any] | str
    tool_result_history: list[Any]
    tool_result: dict[str, Any]
    tool_result_digest: str
    hook_pre_result: dict[str, Any]
    hook_post_result: dict[str, Any]
    context_pressure_exceeded: bool
    subagent_requested: bool
    subagent_tasks: list[Any]
    subagent_child_inputs: list[Any]
    subagent_child_runs: list[Any]
    subagent_joined: list[Any]
    subagent_results: list[Any]
    agent_messages: list[dict[str, Any]]
    task_plan: str
    tool_approval_decision: str
    memory_write_result: dict[str, Any]

    # Person-reID Market-1501 workflows + dataset eval input_mapping
    query_llm_description: str
    description_facets_ranked: list[str]
    description_semantic_ranked: list[str]
    torchreid_visual_ranked: list[str]
    fastreid_visual_ranked: list[str]
    query_description_embedding: list[float]
    fusion_weight_analysis: dict[str, Any]
    fusion_weight_config: dict[str, Any]
    query_description_db_path: str
    gallery_description_db_path: str
    retrieval_score_db_path: str
    retrieval_score_top_k: int
    channel_visual_image_embedding: str
    channel_torchreid_visual_embedding: str
    channel_fastreid_visual_embedding: str
    channel_description_semantic_text: str
    channel_description_structured_facets: str


def new_state(user_input: str) -> WorkflowState:
    return WorkflowState(
        user_input=user_input,
        plan="",
        coder_output="",
        tester_verdict=False,
        tester_feedback="",
        query_analysis="",
        retrieved_context="",
        query_embedding=[],
        retrieved_vector_doc_ids=[],
        reranked_context="",
        final_answer="",
        rag_answer="",
        draft_answer="",
        supervisor_route="",
        research_notes="",
        dispatch_brief="",
        start_marker="",
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
