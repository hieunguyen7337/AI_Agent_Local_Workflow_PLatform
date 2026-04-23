"""Typed node configurations. Pydantic models for validation + replay override support."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class NodeConfig(BaseModel):
    """Base. Do not instantiate directly."""

    id: str
    kind: str
    name: str = ""
    description: str = ""

    model_config = {"extra": "forbid"}


class LLMNodeConfig(NodeConfig):
    kind: Literal["llm"] = "llm"
    provider: Literal["openrouter", "openai"] = "openrouter"
    model: str
    system_prompt: str
    user_prompt_template: str  # format string over state keys, e.g. "Task: {user_input}\nPlan: {plan}"
    output_state_key: str  # which state field to write the response to
    temperature: float = 0.2
    max_tokens: int | None = None
    max_retries: int = 3


class TesterNodeConfig(NodeConfig):
    """LLM-judge stub for M1. Judges `candidate_state_key` against the current fixture's expected."""

    __test__ = False  # tell pytest this isn't a test class

    kind: Literal["tester"] = "tester"
    provider: Literal["openrouter", "openai"] = "openrouter"
    model: str
    system_prompt: str = (
        "You are a strict evaluator. Decide if the candidate output satisfies the expected "
        "outcome. Respond with exactly one word on the first line: PASS or FAIL. "
        "On subsequent lines, give a short reason."
    )
    candidate_state_key: str = "coder_output"
    expected_state_key: str = "_expected"  # injected by eval harness / runner
    test_code_state_key: str = "_test_code"
    execution_mode: Literal["sandbox", "llm_judge"] = "sandbox"
    timeout_s: float = Field(3.0, gt=0.0)
    max_output_bytes: int = Field(12_000, gt=0)
    memory_limit_mb: int | None = Field(256, gt=0)
    temperature: float = 0.0
    max_retries: int = 3


class RetrieverNodeConfig(NodeConfig):
    """Deterministic local-corpus retriever for RAG workflows."""

    kind: Literal["retriever"] = "retriever"
    corpus_path: str
    query_state_key: str = "user_input"
    output_state_key: str = "retrieved_context"
    top_k: int = Field(2, gt=0)


class GateNodeConfig(NodeConfig):
    """Conditional router. Reads tester_verdict from state, routes to pass_target or fail_target."""

    kind: Literal["gate"] = "gate"
    verdict_state_key: str = "tester_verdict"
    pass_target: str  # node id
    fail_target: str  # node id


class RouterNodeConfig(NodeConfig):
    """Multi-way router that dispatches on a state token."""

    kind: Literal["router"] = "router"
    route_state_key: str
    routes: dict[str, str]
    default_target: str | None = None


class LoopConfig(BaseModel):
    """Attached to a back-edge. M1 design rule: every loop has max_iterations."""

    loop_id: str
    back_edge_from: str  # node id that decides to loop
    back_edge_to: str  # node id to jump back to
    max_iterations: int = Field(..., gt=0)

    model_config = {"extra": "forbid"}
