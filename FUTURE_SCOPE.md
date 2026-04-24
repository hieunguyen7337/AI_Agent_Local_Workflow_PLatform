# Future Scope

This file tracks deliberately deferred work beyond M4.1. It should stay aligned with the product vision:

- YAML `GraphSpec` files are the simple source of truth for workflows.
- LLMs should be able to read, analyze, propose mutations, and iterate on that source of truth.
- Humans should be able to inspect the same spec as a graph, review node metadata, compare diffs, and evaluate behavior before accepting changes.
- The runtime should compile validated specs into executable LangGraph workflows without making Python workflow code the long-term authoring surface.

Entries should be pruned or moved when they graduate into implemented milestones.

## Current baseline

- **Canonical spec path** - YAML workflow files are the primary editable source of truth. Pydantic `GraphSpec` validates them and converts them into runtime `GraphMetadata`.
- **Graph and source inspection** - the frontend can show graph topology, full node metadata, raw YAML, and validation state for each workflow.
- **Read-only mutation proposals** - the app can ask an LLM to propose revised YAML, validate the proposal, and show a diff without writing files.
- **Read-only proposal evaluation** - valid proposed YAML can run through the eval harness in memory and compare behavior before any acceptance step.
- **Human-reviewed apply** - a valid proposal can be accepted into `workflows/*.yaml` after explicit confirmation, with an audit record and rollback snapshot.
- **Approval interrupts** - YAML `approval` nodes can pause a run, persist pending approval state locally, and expose that state through the API and run detail UI.
- **Python workflow modules** - still exist as compatibility fallback, not the intended long-term authoring surface.

## Next product milestone - approval resume controls

- **Approve / reject endpoints** - accept a pending approval decision, write the decision artifact, and resume from the checkpoint boundary.
- **Resume semantics** - define whether approval resumes the original run id or forks a continuation run with explicit lineage.
- **Approval audit trail** - persist prompt, decision, reviewer/source, timestamp, pre-decision state snapshot, and continuation run id.
- **Frontend controls** - add approve/reject actions only after backend resume behavior is deterministic and tested.

## Workflow capabilities

- **Reusable subgraphs** - support nested workflows as first-class spec nodes with validation, display metadata, and clear mutation boundaries.
- **JSON graph import/export** - defer until the YAML + `GraphSpec` apply flow is stable. JSON should be an interchange format, not a competing source of truth.

## Optimization research

- **Multi-proposal mutation harness** - generate multiple candidate YAML specs from one workflow and goal, then evaluate all candidates against the same fixture set.
- **Cost-bounded spike** - cap the research run at $20 total and stop cleanly when the cap is reached.
- **Comparison report** - summarize pass rate, cost, latency, variance, and regressions for each candidate.
- **Research-only outcome** - produce a writeup and implementation recommendation; do not automatically ship the best candidate.

## Technical debt and compatibility

- **Python workflow fallback removal** - remove legacy workflow modules once CLI, replay, evals, API, and docs are fully proven against YAML specs.
- **Rollback restore flow** - apply now creates rollback snapshots, but restoring one from the UI/API is still deferred.
- **CLI `--set` deep overrides** - M1 supports one level deep only (`--set node.field=value`). Add deeper override syntax only when real use cases appear.
- **Spec patch format** - current proposals use full YAML rewrites plus unified diffs. Decide later whether accepted changes should use JSON Patch or constrained graph-edit operations.
- **OTEL GenAI semconv churn** - attribute names are isolated in `telemetry/genai_attrs.py`; update only when the upstream convention stabilizes.

## Explicitly rejected

- Custom DSL with `@graph` decorators or `>>` operator overloading. One primary authoring surface: YAML workflow specs validated by `GraphSpec`.
- Broad cloud abstraction layer for LLM providers. Keep direct adapters unless a real integration need appears beyond OpenRouter + OpenAI.
- Mid-node budget cancellation as a required platform behavior. Budget enforcement intentionally happens after a node finishes and before dispatching the next node.
- Hosted tracing backends such as LangSmith or Studio. Local-first is a hard constraint.
- Haystack or other RAG-specific frameworks as the platform substrate. LangGraph is broader and remains the runtime layer.
- Graphviz for static export. Mermaid covers the current need and renders on GitHub natively.
