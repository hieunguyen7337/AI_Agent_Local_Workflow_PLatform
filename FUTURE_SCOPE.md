# Future Scope

This file tracks everything deliberately deferred beyond M3.3. It is the single source of truth for "we decided not to do this yet, and here's why." Entries should be pruned as they graduate into real milestones.

## Explicitly deferred

### M2 - second workflow pattern + ergonomics

### M3.3 - declarative workflow source of truth

- **Decision recorded - canonical `GraphSpec` promoted.** YAML workflow files are the primary editable source of truth; Pydantic `GraphSpec` is the validation contract; Python builders are compatibility fallback. See [docs/graphspec_decision.md](docs/graphspec_decision.md).
- **JSON graph import/export** - defer until the YAML + `GraphSpec` contract has stabilized through at least one editing or optimization workflow.

### M4 - human-in-the-loop and subgraphs

- **Interrupts / approval nodes** - next recommended implementation milestone. Add the node type to `GraphSpec`, adapt it through the existing runtime, persist pending approval state locally, and expose read-only pending status before adding approve/reject controls.
- **Reusable subgraphs** - nested workflows as first-class spec nodes, with metadata rich enough for LLM mutation and frontend graph inspection.
- **Frontend spec inspector** - expand from compact node metadata to a fuller source-of-truth view so humans can inspect prompts, routing rules, budgets, and state keys from the graph.

### M5 - bounded optimization research spike

- **Claude Opus proposes 10 mutations** of an existing YAML `GraphSpec` (model choice, retry counts, router thresholds, prompt versions first; structural mutations only after validation rules are mature).
- **$20 hard cost cap** for the entire spike.
- Each mutation runs against the eval set; report which improved/regressed pass rate, cost, latency; variance.
- **Outcome is a writeup**, not shipped features. Decide from results whether to invest further.
- **Mutation review loop** - preserve the diff between the original YAML and each proposed mutation so humans can review the graph-level change before accepting it.

## Specific technical debt to revisit

- **CLI `--set` deep overrides** - M1 supports one level deep only (`--set node.field=value`). Deeper override syntax if real use cases appear.
- **Python workflow modules** - compatibility fallback only. Remove once all CLI, replay, eval, API, and docs workflows are proven against YAML specs.
- **Spec patch format** - decide whether LLM mutations should be represented as full YAML rewrites, JSON Patch, or a constrained graph-edit operation list.
- **OTEL GenAI semconv churn** - all attribute names isolated in `telemetry/genai_attrs.py`. Watch for spec updates.

## Explicitly rejected (do not add)

- Custom DSL with `@graph` decorator or `>>` operator overloading. One primary authoring surface: YAML workflow specs validated by `GraphSpec`.
- Broad cloud abstraction layer for LLM providers. Keep direct adapters only unless a real integration need appears beyond OpenRouter + OpenAI.
- Mid-node budget cancellation as a required platform behavior. Budget enforcement is intentionally checked after a node finishes and before dispatching the next node.
- Hosted tracing backends (LangSmith, Studio). Local-first is a hard constraint.
- Haystack or other RAG-specific frameworks as the platform substrate. LangGraph is broader.
- Graphviz for static export. Mermaid covers the need and renders on GitHub natively.
