# GraphSpec Decision Record

## Decision

Promote a canonical Pydantic `GraphSpec` layer for M3.3.

YAML workflow files are now the primary editable source of truth. `GraphSpec` validates and normalizes those files, then adapts them into the existing `GraphMetadata` runtime representation.

As of M5.5, CLI, replay, eval, API, and graph export paths load workflows from YAML specs only. The typed Python builder remains as an internal metadata/compiler helper, not a workflow authoring fallback.

## Context

The project goal is an adaptable framework for many agent workflows and pipelines, including RAG, coder/tester loops, orchestrators, routers, and fan-out aggregation. That requires a source file that is compact enough for LLMs to analyze, mutate, diff, and iterate on, while still being understandable to humans and renderable as a graph with node metadata.

M3.2 already had four reference workflow patterns:

- `coder_tester`
- `linear_rag`
- `supervisor_loop`
- `dispatch_aggregate`

That coverage is enough to justify a declarative contract instead of continuing with Python-only workflow authoring.

Current pressure points now directly affect the core product loop:

- LLMs need to propose graph and node-config changes without editing Python code.
- Humans need frontend graph views backed by full node metadata.
- Replay, evals, and optimization need stable graph/config mutation boundaries.
- UI editing and source inspection need a schema-backed contract around the YAML source of truth.

## Rationale

YAML is a good first editing format because it is compact, readable, comment-friendly, and easier for LLMs to mutate than Python or JSON. Pydantic remains the trusted contract; YAML by itself is not trusted.

The runtime should not be rewritten. The existing builder validation, LangGraph compilation, telemetry, checkpointing, replay, evals, and frontend topology view remain useful implementation layers behind the new declarative source.

## Follow-On Scope

The next implementation milestone should build on YAML + `GraphSpec`:

- Expand spec-driven frontend inspection and editing.
- Add approval/interrupt nodes to the spec and builder/runtime adapter.
- Persist pending approval state in local run artifacts and checkpoints.
- Expose read-only pending status first.
- Use spec mutation plus eval comparison for optimization experiments.

Do not add a custom DSL. YAML plus `GraphSpec` is the first declarative layer.

## Revisit Triggers

Revisit the spec shape if one of these becomes active scope:

- Structural graph mutations go beyond current node/edge/loop patterns.
- Reusable subgraphs need a serializable composition boundary.
- Frontend editing needs optimistic validation and patch previews.
- External tooling needs versioned graph execution contracts.

## Status

Accepted for M3.3 implementation.
