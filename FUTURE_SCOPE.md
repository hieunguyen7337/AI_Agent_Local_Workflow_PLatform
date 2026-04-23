# Future Scope

This file tracks everything deliberately deferred beyond M2.4. It is the single source of truth for "we decided not to do this yet, and here's why." Entries should be pruned as they graduate into real milestones.

## Explicitly deferred

### M2 - second workflow pattern + ergonomics

- **Schema-evolution-safe replay** - allow replay when state schema has changed between run and replay.
- **Streaming cancel** - cooperative cancellation during streaming LLM responses.

### M3 - remaining workflow patterns + schema decision

- **Supervisor loop pattern** - orchestrator chooses specialist per turn, loops until FINISH.
- **Dispatch-and-aggregate pattern** - boss fans out to parallel specialists, aggregator produces final verdict.
- **Decision point - canonical `GraphSpec`?** After four workflow patterns exist, evaluate whether the builder's internal form is straining against LangGraph's native representation. If yes, introduce explicit Pydantic `GraphSpec` / `NodeSpec` / `EdgeSpec` layer. Write the decision down either way.
- **JSON graph import/export** - only if `GraphSpec` is introduced. Required for M5.

### M4 - human-in-the-loop and subgraphs

- **Interrupts / approval nodes** - human-in-the-loop (LangGraph supports natively).
- **Reusable subgraphs** - nested workflows as first-class nodes.

### M5 - bounded optimization research spike

- **Claude Opus proposes 10 mutations** of an existing graph (model choice, retry counts, router thresholds, prompt versions - no structural mutations).
- **$20 hard cost cap** for the entire spike.
- Each mutation runs against the eval set; report which improved/regressed pass rate, cost, latency; variance.
- **Outcome is a writeup**, not shipped features. Decide from results whether to invest further.

## Specific technical debt to revisit

- **CLI `--set` deep overrides** - M1 supports one level deep only (`--set node.field=value`). Deeper override syntax if real use cases appear.
- **Builder's internal form -> `GraphSpec`** - M3 decision point. If promoted, replay logic will need to switch from builder-rebuild to spec-mutate-then-compile.
- **OTEL GenAI semconv churn** - all attribute names isolated in `telemetry/genai_attrs.py`. Watch for spec updates.

## Explicitly rejected (do not add)

- Custom DSL with `@graph` decorator or `>>` operator overloading. One authoring surface: the typed Python builder.
- Broad cloud abstraction layer for LLM providers. Keep direct adapters only unless a real integration need appears beyond OpenRouter + OpenAI.
- Mid-node budget cancellation as a required platform behavior. Budget enforcement is intentionally checked after a node finishes and before dispatching the next node.
- Hosted tracing backends (LangSmith, Studio). Local-first is a hard constraint.
- Haystack or other RAG-specific frameworks as the platform substrate. LangGraph is broader.
- Graphviz for static export. Mermaid covers the need and renders on GitHub natively.
