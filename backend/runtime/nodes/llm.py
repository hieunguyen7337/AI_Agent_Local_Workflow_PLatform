"""Generic LLM node factory. Wraps provider call + GenAI span emission + budget update."""
from __future__ import annotations

import time
from typing import Callable

from opentelemetry.trace import Status, StatusCode

from backend.builder.nodes import LLMNodeConfig
from backend.providers import stream_provider
from backend.providers.pricing import price_for
from backend.runtime.cancellation import CancellationController
from backend.runtime.errors import CancelledError
from backend.runtime.state import WorkflowState
from backend.telemetry.genai_attrs import (
    WORKFLOW_COST_USD,
    WORKFLOW_LATENCY_MS,
    WORKFLOW_STATUS,
    llm_request_attrs,
    llm_usage_attrs,
    node_attrs,
)
from backend.telemetry.tracer import get_tracer


def make_llm_node(
    cfg: LLMNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    on_cost: Callable[[float], None],
    cancellation: CancellationController | None = None,
) -> Callable[[WorkflowState], dict]:
    """Return a LangGraph-compatible node function."""
    tracer = get_tracer()
    price = price_for(cfg.provider, cfg.model)

    def _node(state: WorkflowState) -> dict:
        iteration = state.get("iteration_counts", {}).get(cfg.id, 0)
        attrs: dict[str, object] = {
            **node_attrs(
                run_id=run_id,
                graph_name=graph_name,
                node_id=cfg.id,
                node_kind="llm",
                iteration=iteration,
            ),
            **llm_request_attrs(
                system=cfg.provider,
                model=cfg.model,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            ),
        }
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            t0 = time.monotonic_ns()
            try:
                user_content = _format_template(cfg.user_prompt_template, state)
                messages = [
                    {"role": "system", "content": cfg.system_prompt},
                    {"role": "user", "content": user_content},
                ]
                resp = stream_provider(
                    cfg.provider,
                    model=cfg.model,
                    messages=messages,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                    max_retries=cfg.max_retries,
                    cancel_check=cancellation.is_cancelled if cancellation else None,
                )
                cost = price.cost_usd(resp.usage.input_tokens, resp.usage.output_tokens)
                latency_ms = (time.monotonic_ns() - t0) / 1_000_000

                span.set_attributes(
                    llm_usage_attrs(
                        input_tokens=resp.usage.input_tokens,
                        output_tokens=resp.usage.output_tokens,
                        model=resp.model,
                    )
                )
                span.set_attribute(WORKFLOW_COST_USD, cost)
                span.set_attribute(WORKFLOW_LATENCY_MS, latency_ms)
                span.set_status(Status(StatusCode.OK))

                on_cost(cost)
                return {
                    cfg.output_state_key: resp.text,
                }
            except CancelledError as e:
                span.set_attribute(WORKFLOW_STATUS, "cancelled")
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    _node.__name__ = f"llm_{cfg.id}"
    return _node


def _format_template(template: str, state: WorkflowState) -> str:
    class _SafeDict(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(_SafeDict(state))
