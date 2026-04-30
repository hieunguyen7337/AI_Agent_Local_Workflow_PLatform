"""OTEL TracerProvider setup for a single run. One tracer per run directory."""
from __future__ import annotations

import threading
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from .exporter import RoutingSpanExporter

_PROVIDER: TracerProvider | None = None
_ROUTING_EXPORTER: RoutingSpanExporter | None = None
_LOCK = threading.Lock()


def init_tracer(
    run_dir: Path,
    *,
    run_id: str | None = None,
    service_name: str = "workflow-platform",
) -> trace.Tracer:
    """Initialize the process tracer once and register run-local telemetry."""
    global _PROVIDER, _ROUTING_EXPORTER
    registered_run_id = run_id or run_dir.name
    with _LOCK:
        if _PROVIDER is None:
            _ROUTING_EXPORTER = RoutingSpanExporter()
            _PROVIDER = TracerProvider(resource=Resource.create({"service.name": service_name}))
            _PROVIDER.add_span_processor(SimpleSpanProcessor(_ROUTING_EXPORTER))
            trace.set_tracer_provider(_PROVIDER)
        if _ROUTING_EXPORTER is not None:
            _ROUTING_EXPORTER.register_run(run_id=registered_run_id, run_dir=run_dir)
    return trace.get_tracer("workflow")


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("workflow")
