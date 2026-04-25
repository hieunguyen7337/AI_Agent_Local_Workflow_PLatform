# Future Scope

This file tracks deliberately deferred work beyond M5.5. It should stay aligned with the product vision:

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
- **Approval decisions** - pending approvals can be approved or rejected, producing an immutable source decision artifact and a forked continuation run with lineage.
- **Approval workbench** - approvals are visible as a first-class frontend list with pending/decided filtering and source/continuation navigation.
- **Approval eval coverage** - approval workflows can be evaluated with fixture-provided decisions, continuation lineage checks, and approval path coverage metrics.
- **Reusable collapsed subgraphs** - YAML workflows can reference acyclic, non-approval child workflows as collapsed nodes, execute them as nested local runs, and persist parent/child lineage artifacts.
- **Richer subgraph review** - reviewers can open a subgraph node's child graph/source context, navigate parent/child run lineage, and evaluate mapped subgraph outputs.
- **Multi-proposal optimization loop** - the app can generate multiple YAML mutation candidates, evaluate them under a shared cost cap, rank them, and recommend one for human review.
- **Rollback restore flow** - rollback snapshots can be listed, previewed, and restored after human confirmation, with a new audit record for restore actions.
- **YAML-only workflow loading** - CLI, replay, evals, API, graph export, and tests load canonical YAML specs without Python workflow module fallback. The Python builder remains only as an internal metadata/compiler helper.

## Next product milestone - JSON graph import/export

- **Export JSON graph specs** - expose a stable JSON representation of validated `GraphSpec` for external tooling, archival, and deterministic machine review.
- **Import JSON graph specs** - accept JSON as an interchange format, validate it through the same `GraphSpec` contract, and convert it back to canonical YAML before apply.
- **Round-trip checks** - prove YAML -> `GraphSpec` -> JSON -> `GraphSpec` -> YAML preserves workflow identity, topology, node metadata, budgets, loops, routes, approvals, and subgraph mappings.
- **UI affordance** - add read-only JSON export first; defer imported JSON apply until validation, diff, and audit behavior match YAML proposal/apply safety.

## Workflow capabilities

- **Nested approval subgraphs** - child workflows with `approval` nodes remain deferred until parent/child pause and resume semantics are explicit.
- **Inline subgraph editing** - child graph inspection is read-only; editing still happens through the YAML proposal/apply loop.

## Technical debt and compatibility

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
