"""Embedding node factory for hosted vector models."""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
import time
from typing import Callable

from opentelemetry.trace import Status, StatusCode

from backend.builder.nodes import EmbeddingNodeConfig
from backend.providers import call_embedding_provider
from backend.providers.pricing import price_for
from backend.runtime.audit import AuditRecorder, audit_preview
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


def make_embedding_node(
    cfg: EmbeddingNodeConfig,
    *,
    run_id: str,
    graph_name: str,
    on_cost: Callable[[float], None],
    cancellation: CancellationController | None = None,
    audit: AuditRecorder | None = None,
) -> Callable[[WorkflowState], dict]:
    """Return a LangGraph-compatible embedding node function."""
    tracer = get_tracer()
    price = price_for(cfg.provider, cfg.model)

    def _node(state: WorkflowState) -> dict:
        iteration = state.get("iteration_counts", {}).get(cfg.id, 0)
        attrs: dict[str, object] = {
            **node_attrs(
                run_id=run_id,
                graph_name=graph_name,
                node_id=cfg.id,
                node_kind="embedding",
                iteration=iteration,
            ),
            **llm_request_attrs(
                system=cfg.provider,
                model=cfg.model,
                temperature=0.0,
                max_tokens=None,
            ),
        }
        with tracer.start_as_current_span(f"node.{cfg.id}", attributes=attrs) as span:
            t0 = time.monotonic_ns()
            try:
                if cancellation is not None and cancellation.is_cancelled():
                    raise CancelledError("user_cancelled")
                input_payload = _build_embedding_input(
                    _format_template(cfg.input_template, state),
                    state,
                    cfg.image_inputs,
                )
                resp = call_embedding_provider(
                    cfg.provider,
                    model=cfg.model,
                    input_payload=input_payload,
                    dimensions=cfg.dimensions,
                    max_retries=cfg.max_retries,
                )
                cost = price.cost_usd(resp.usage.input_tokens, 0)
                latency_ms = (time.monotonic_ns() - t0) / 1_000_000
                span.set_attributes(
                    llm_usage_attrs(
                        input_tokens=resp.usage.input_tokens,
                        output_tokens=0,
                        model=resp.model,
                    )
                )
                span.set_attribute("gen_ai.response.embedding_dimensions", len(resp.embedding))
                span.set_attribute(WORKFLOW_COST_USD, cost)
                span.set_attribute(WORKFLOW_LATENCY_MS, latency_ms)
                span.set_status(Status(StatusCode.OK))
                on_cost(cost)
                if audit is not None:
                    audit.write_event(
                        {
                            "node_id": cfg.id,
                            "node_kind": "embedding",
                            "status": "ok",
                            "model": resp.model,
                            "input_template": _format_template(cfg.input_template, state),
                            "image_inputs": [
                                {"state_key": item.state_key, "path": str(state.get(item.state_key, "")), "detail": item.detail}
                                for item in cfg.image_inputs
                            ],
                            "output_state_key": cfg.output_state_key,
                            "embedding_dimensions": len(resp.embedding),
                            "output_preview": audit_preview(resp.embedding[:10]),
                            "usage": {"input_tokens": resp.usage.input_tokens, "output_tokens": 0},
                            "cost_usd": cost,
                            "latency_ms": latency_ms,
                        }
                    )
                return {cfg.output_state_key: resp.embedding}
            except CancelledError as e:
                span.set_attribute(WORKFLOW_STATUS, "cancelled")
                span.set_status(Status(StatusCode.ERROR, str(e)))
                if audit is not None:
                    audit.write_event({"node_id": cfg.id, "node_kind": "embedding", "status": "cancelled", "error": str(e)})
                raise
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                if audit is not None:
                    audit.write_event({"node_id": cfg.id, "node_kind": "embedding", "status": "error", "error": f"{type(e).__name__}: {e}"})
                raise

    _node.__name__ = f"embedding_{cfg.id}"
    return _node


def _format_template(template: str, state: WorkflowState) -> str:
    class _SafeDict(dict):
        def __missing__(self, key):
            return ""

    return template.format_map(_SafeDict(state)).strip()


def _build_embedding_input(user_text: str, state: WorkflowState, image_inputs: list) -> str | list[dict]:
    if not image_inputs:
        return user_text

    content: list[dict] = []
    if user_text:
        content.append({"type": "text", "text": user_text})
    for image_input in image_inputs:
        path_value = state[image_input.state_key]
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": _image_path_to_data_url(Path(str(path_value)))},
            }
        )
    return [{"content": content}]


def _image_path_to_data_url(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"image input not found: {path}")
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"
