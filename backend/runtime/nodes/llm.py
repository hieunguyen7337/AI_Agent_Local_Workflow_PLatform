"""Generic LLM node factory. Wraps provider call + GenAI span emission + budget update."""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
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
                user_text = _format_template(cfg.user_prompt_template, state)
                user_content = _build_user_content(user_text, state, cfg.image_inputs)
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
                if cancellation is not None and cancellation.is_cancelled():
                    cancelled = CancelledError("user_cancelled")
                    span.set_attribute(WORKFLOW_STATUS, "cancelled")
                    span.set_status(Status(StatusCode.ERROR, str(cancelled)))
                    raise cancelled from e
                span.set_status(Status(StatusCode.ERROR, str(e)))
                raise

    _node.__name__ = f"llm_{cfg.id}"
    return _node


def _format_template(template: str, state: WorkflowState) -> str:
    class _SafeDict(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(_SafeDict(state))


def _build_user_content(user_text: str, state: WorkflowState, image_inputs: list) -> str | list[dict]:
    if not image_inputs:
        return user_text

    content: list[dict] = [{"type": "text", "text": user_text}]
    for image_input in image_inputs:
        path_value = state[image_input.state_key]
        image_url = _image_path_to_data_url(Path(str(path_value)))
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": image_url,
                    "detail": image_input.detail,
                },
            }
        )
    return content


def _image_path_to_data_url(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"image input not found: {path}")
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
