"""OTEL TracerProvider setup for a single run. One tracer per run directory."""
from __future__ import annotations

from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from .exporter import SqliteJsonlExporter

_PROVIDER: TracerProvider | None = None


def init_tracer(run_dir: Path, *, service_name: str = "workflow-platform") -> trace.Tracer:
    """Initialize (or reuse) a global TracerProvider with an exporter pointed at run_dir.

    We keep a module-level provider to avoid OTEL's warning about setting multiple providers,
    but we do rebuild the exporter per run to retarget the db/jsonl paths.
    """
    global _PROVIDER
    db_path = run_dir / "telemetry.db"
    jsonl_path = run_dir / "spans.jsonl"

    exporter = SqliteJsonlExporter(db_path=db_path, jsonl_path=jsonl_path)

    if _PROVIDER is None:
        _PROVIDER = TracerProvider(resource=Resource.create({"service.name": service_name}))
        trace.set_tracer_provider(_PROVIDER)

    _PROVIDER.add_span_processor(SimpleSpanProcessor(exporter))
    return trace.get_tracer("workflow")


def get_tracer() -> trace.Tracer:
    return trace.get_tracer("workflow")
