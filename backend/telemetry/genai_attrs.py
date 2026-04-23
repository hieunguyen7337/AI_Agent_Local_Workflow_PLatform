"""OpenTelemetry GenAI semconv attribute names, isolated for churn resilience.

If upstream renames any of these, this is the only file to edit.
"""
from __future__ import annotations

GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"

WORKFLOW_RUN_ID = "workflow.run_id"
WORKFLOW_NODE_ID = "workflow.node_id"
WORKFLOW_NODE_KIND = "workflow.node_kind"
WORKFLOW_GRAPH_NAME = "workflow.graph_name"
WORKFLOW_COST_USD = "workflow.cost_usd"
WORKFLOW_LATENCY_MS = "workflow.latency_ms"
WORKFLOW_ITERATION = "workflow.iteration"
WORKFLOW_STATUS = "workflow.status"


def llm_request_attrs(
    *,
    system: str,
    model: str,
    temperature: float,
    max_tokens: int | None = None,
) -> dict[str, object]:
    attrs: dict[str, object] = {
        GEN_AI_SYSTEM: system,
        GEN_AI_REQUEST_MODEL: model,
        GEN_AI_REQUEST_TEMPERATURE: temperature,
    }
    if max_tokens is not None:
        attrs[GEN_AI_REQUEST_MAX_TOKENS] = max_tokens
    return attrs


def llm_usage_attrs(*, input_tokens: int, output_tokens: int, model: str) -> dict[str, object]:
    return {
        GEN_AI_USAGE_INPUT_TOKENS: input_tokens,
        GEN_AI_USAGE_OUTPUT_TOKENS: output_tokens,
        GEN_AI_RESPONSE_MODEL: model,
    }


def node_attrs(
    *,
    run_id: str,
    graph_name: str,
    node_id: str,
    node_kind: str,
    iteration: int = 0,
) -> dict[str, object]:
    return {
        WORKFLOW_RUN_ID: run_id,
        WORKFLOW_GRAPH_NAME: graph_name,
        WORKFLOW_NODE_ID: node_id,
        WORKFLOW_NODE_KIND: node_kind,
        WORKFLOW_ITERATION: iteration,
    }
