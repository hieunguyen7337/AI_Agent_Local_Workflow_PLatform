# M1 Implementation Plan â€” Local-First AI Workflow Platform

## Context

Greenfield project at `C:\Users\Admin\Documents\AI_club_projects\AI_Agent_Local_Workflow_PLatform` (empty directory). The user has authored an exhaustive architectural brief for a local-first platform to author, run, visualize, and iterate on AI workflows. The brief explicitly scopes this implementation to **M1 only** â€” one vertical slice end-to-end built around a single workflow (coder/tester validate-and-retry loop), exercising every layer of the stack (builder, runtime, checkpointing, telemetry, eval harness, minimal UI).

Why this shape, in the user's own words: v1 of their plan over-engineered by introducing a canonical `GraphSpec` schema on top of LangGraph before a single workflow ran end-to-end. This plan follows their corrected approach â€” walk **one** workflow all the way through, defer the schema layer to M3 when real pain justifies it, and make evals load-bearing from day one so every downstream optimization claim is falsifiable.

The goal of M1 is to **validate the architecture end-to-end** â€” if LangGraph's native state model strains against our needs, we want that pain at M1, not M4.

---

## Decisions captured from clarification

- **UI bridge**: FastAPI (read-only, serves topology + runs from SQLite) + Typer CLI (`run`, `replay`, `eval`, `export-mermaid`, `serve`).
- **Tester node**: LLM-judge stub â€” an LLM call that judges coder output against fixture `expected`, returns pass/fail. No subprocess execution in M1.
- **Single LLM model**: `minimax/minimax-m2.7` on OpenRouter. Pricing: $0.15/M input, $1.20/M output. Hardcoded in `backend/providers/pricing.py`.
- **Future scope**: captured as an appendix at the bottom of this plan. On approval, the first implementation action is to write `FUTURE_SCOPE.md` at the repo root from that appendix.

---

## Project layout

```
AI_Agent_Local_Workflow_PLatform/
â”œâ”€â”€ README.md                         # Quickstart + M1 scope statement
â”œâ”€â”€ FUTURE_SCOPE.md                   # Written from appendix of this plan (step 0)
â”œâ”€â”€ pyproject.toml                    # Python deps, pytest config, Typer console_script
â”œâ”€â”€ .gitignore                        # runs/, .venv/, node_modules/, *.db, .env
â”œâ”€â”€ .env.example                      # OPENROUTER_API_KEY placeholder
â”œâ”€â”€ runs/                             # gitignored â€” SQLite DBs, JSONL span logs, checkpoints
â”‚   â””â”€â”€ .gitkeep
â”œâ”€â”€ evals/
â”‚   â””â”€â”€ coder_tester/
â”‚       â”œâ”€â”€ fixtures.yaml             # {id, input, expected} list
â”‚       â””â”€â”€ __init__.py
â”‚
â”œâ”€â”€ backend/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ builder/
â”‚   â”‚   â”œâ”€â”€ api.py                    # GraphBuilder, NodeRef â€” typed fluent surface
â”‚   â”‚   â”œâ”€â”€ nodes.py                  # NodeConfig base + LLMNodeConfig, GateNodeConfig, TesterNodeConfig
â”‚   â”‚   â”œâ”€â”€ validation.py             # Compile-time checks
â”‚   â”‚   â””â”€â”€ compile.py                # GraphBuilder.compile() â†’ LangGraph StateGraph + GraphMetadata
â”‚   â”œâ”€â”€ runtime/
â”‚   â”‚   â”œâ”€â”€ state.py                  # WorkflowState TypedDict
â”‚   â”‚   â”œâ”€â”€ nodes/
â”‚   â”‚   â”‚   â”œâ”€â”€ llm.py                # Generic LLM node factory (OpenRouter call)
â”‚   â”‚   â”‚   â”œâ”€â”€ gate.py               # Conditional routing from tester verdict
â”‚   â”‚   â”‚   â””â”€â”€ tester.py             # M1 LLM-judge stub
â”‚   â”‚   â”œâ”€â”€ executor.py               # Runs compiled graph; wires budget + telemetry
â”‚   â”‚   â””â”€â”€ errors.py                 # BudgetExceededError, MaxIterationsError
â”‚   â”œâ”€â”€ providers/
â”‚   â”‚   â”œâ”€â”€ openrouter.py             # Single client (httpx), retry, token accounting
â”‚   â”‚   â””â”€â”€ pricing.py                # PRICE_TABLE: minimax/minimax-m2.7 entry
â”‚   â”œâ”€â”€ telemetry/
â”‚   â”‚   â”œâ”€â”€ tracer.py                 # OTEL TracerProvider + custom exporter wiring
â”‚   â”‚   â”œâ”€â”€ genai_attrs.py            # GenAI semconv constants (isolated for churn resilience)
â”‚   â”‚   â””â”€â”€ exporter.py               # SpanExporter â†’ SQLite rows + JSONL append
â”‚   â”œâ”€â”€ checkpointing/
â”‚   â”‚   â”œâ”€â”€ store.py                  # Wraps LangGraph SqliteSaver; run_id helpers
â”‚   â”‚   â””â”€â”€ replay.py                 # Rebuild graph w/ overrides â†’ resume from checkpoint
â”‚   â”œâ”€â”€ budget/
â”‚   â”‚   â””â”€â”€ enforcer.py               # Running totals; raises between nodes
â”‚   â”œâ”€â”€ evals/
â”‚   â”‚   â”œâ”€â”€ harness.py                # Load YAML â†’ run graph N times â†’ collect metrics
â”‚   â”‚   â”œâ”€â”€ fixtures.py               # Pydantic Fixture model + YAML loader
â”‚   â”‚   â””â”€â”€ metrics.py                # pass_rate, mean_cost, mean_latency, p95_latency
â”‚   â”œâ”€â”€ cli/
â”‚   â”‚   â””â”€â”€ main.py                   # Typer app: run, replay, eval, export-mermaid, serve
â”‚   â”œâ”€â”€ server/
â”‚   â”‚   â”œâ”€â”€ app.py                    # FastAPI app (bound 127.0.0.1)
â”‚   â”‚   â””â”€â”€ routes.py                 # /api/graph, /api/runs, /api/runs/{id}
â”‚   â”œâ”€â”€ workflows/
â”‚   â”‚   â””â”€â”€ coder_tester.py           # THE M1 reference graph (builder-authored)
â”‚   â””â”€â”€ tests/
â”‚       â”œâ”€â”€ test_builder.py           # Validation rules (loop requires max_iterations, budgets required)
â”‚       â”œâ”€â”€ test_runtime.py           # Node execution w/ mocked provider
â”‚       â”œâ”€â”€ test_budget.py            # Cost + latency breach paths
â”‚       â”œâ”€â”€ test_checkpoint_replay.py # Replay w/ modified node config
â”‚       â”œâ”€â”€ test_eval_harness.py      # Fixture load + metrics aggregation
â”‚       â””â”€â”€ test_openrouter_mock.py   # Provider retry + token accounting
â”‚
â””â”€â”€ frontend/
    â”œâ”€â”€ package.json                  # react, react-flow, vite, tailwind, react-query, dagre
    â”œâ”€â”€ vite.config.ts
    â”œâ”€â”€ tailwind.config.js
    â”œâ”€â”€ index.html
    â””â”€â”€ src/
        â”œâ”€â”€ main.tsx
        â”œâ”€â”€ App.tsx
        â”œâ”€â”€ api/client.ts             # Fetch wrappers for FastAPI
        â”œâ”€â”€ components/
        â”‚   â”œâ”€â”€ GraphView.tsx         # React Flow topology (dagre auto-layout)
        â”‚   â”œâ”€â”€ RunList.tsx           # Recent runs table (polls /api/runs every 2s)
        â”‚   â””â”€â”€ RunDetail.tsx         # Per-run summary (status, cost, latency)
        â””â”€â”€ types.ts                  # Mirror backend response shapes
```

---

## Module responsibilities

**builder** â€” Typed fluent construction; compile-time validation; emission of a LangGraph `StateGraph` plus a sidecar `GraphMetadata` (stable node IDs, budgets, iteration limits, mermaid source).
- Key types: `GraphBuilder`, `NodeRef`, `LLMNodeConfig`, `GateNodeConfig`, `TesterNodeConfig`, `LoopConfig(max_iterations: int)`, `GraphMetadata`.
- Mechanism for "loop requires `max_iterations`": `builder.add_loop(back_edge_from, back_edge_to, max_iterations=...)` is the **only** builder method that creates a back-edge. Plain `add_edge()` runs a DFS ancestor check and raises on cycles. `compile()` additionally refuses if `cost_budget_usd` or `latency_budget_ms` are unset.

**runtime** â€” `WorkflowState` TypedDict (`messages`, `iteration_counts: dict[str, int]`, `tester_verdict: bool | None`, `cost_usd_accum`, `latency_ms_accum`, `artifacts`), node factories that produce LangGraph-compatible callables, the execution driver.

**telemetry** â€” OTEL tracer setup, GenAI attribute encoding, custom span exporter writing to both SQLite (indexed rows for UI queries) and JSONL (full fidelity). All semconv attribute names isolated in `genai_attrs.py` â€” one-file edit if they rename.

**checkpointing** â€” Wraps LangGraph's `SqliteSaver`. `replay(run_id, at_node, config_overrides)` rebuilds the graph with overrides applied, then resumes using the stored checkpoint's thread_id. Works because node IDs are stable across rebuilds (builder enforces) and state schema is unchanged.

**budget** â€” `BudgetEnforcer` threaded through state; increments on every node completion; raises `BudgetExceededError` **between nodes** before dispatching the next one. Mid-node cancellation is M2.

**evals** â€” Loads YAML fixtures, executes graph N times per fixture, aggregates metrics, writes a results JSON. Per-fixture pass/fail comes from the tester stub's verdict.

**cli** â€” Typer app. Commands: `run <workflow>`, `replay <run_id> --at <node> --set k=v`, `eval <workflow>`, `export-mermaid <workflow>`, `serve` (starts FastAPI on 127.0.0.1).

**server** â€” FastAPI, read-only over SQLite + the workflow module (for topology). Bound to 127.0.0.1; no auth.

**UI** â€” React Flow topology (dagre auto-layout computed once on load) + recent-runs table (polls every 2s via react-query) + minimal run detail. No overlays in M1.

---

## Execution order

1. **Write `FUTURE_SCOPE.md`** at repo root from the appendix below. First action after approval.
2. `pyproject.toml`, `.gitignore`, `.env.example`, skeleton directories with empty `__init__.py`.
3. `providers/pricing.py` â€” hardcoded `PRICE_TABLE` with the `minimax/minimax-m2.7` entry. `providers/openrouter.py` â€” sync httpx call, retry, returns `(text, usage)`.
4. `telemetry/` â€” tracer + SQLite/JSONL exporter. Define `spans` table schema. Smoke test: emit a span, query it back.
5. `runtime/state.py` + `runtime/errors.py` â€” types first, no logic.
6. `budget/enforcer.py` â€” pure unit-testable class. Tests for cost + latency breach.
7. `builder/nodes.py` + `builder/api.py` + `builder/validation.py` â€” with tests rejecting: missing `max_iterations`, missing budgets, unknown node refs, `add_edge` cycles.
8. `builder/compile.py` â€” emit real LangGraph `StateGraph` + `GraphMetadata`. Test against a trivial 2-node graph.
9. `runtime/nodes/llm.py` â€” wraps OpenRouter, emits GenAI span, updates budget. Unit test with mocked provider.
10. `runtime/nodes/tester.py` â€” LLM-judge stub. `runtime/nodes/gate.py` â€” reads tester verdict, routes to coder or END.
11. `runtime/executor.py` â€” glues compiled graph + checkpointer + budget + tracer. First real end-to-end run here.
12. **`workflows/coder_tester.py`** â€” the M1 reference graph, the forcing function driving everything above.
13. `checkpointing/store.py` + `checkpointing/replay.py` â€” replay test: run, fail gate, replay from coder checkpoint with modified prompt, confirm new path.
14. `evals/` â€” `evals/coder_tester/fixtures.yaml` with 3â€“5 small programming tasks. Run 4x, emit metrics JSON.
15. `cli/main.py` â€” wire all commands. Mermaid export is a string walk over `GraphMetadata`.
16. `server/app.py` + routes â€” read-only over SQLite + topology module.
17. `frontend/` â€” Vite scaffold, React Flow + dagre, `RunList`, minimal `RunDetail`. 2s polling.
18. Test sweep + README quickstart. Run pytest, then run the 4x eval, verify metrics file.

---

## Feasibility flags

**Replay with modified node config** â€” LangGraph's `SqliteSaver` replays *state*, not graph structure. Our mechanism: rebuild graph via builder with overrides, then resume using stored checkpoint's thread_id. This works only when node IDs are stable (builder enforces) and state schema is unchanged. **M1 overrides are restricted to scalar knobs: prompt text, model choice, retry counts, router thresholds.** Schema-evolving mutations break replay â€” documented limit.

**OpenTelemetry GenAI semconv** â€” still evolving. Attribute names like `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` are settled; event/body conventions are not. All semconv references isolated in `telemetry/genai_attrs.py`. Do **not** use `opentelemetry-instrumentation-*` auto-instrumentors for LLMs â€” they churn.

**UI live updates** â€” Polling every 2s via react-query. Websockets add broadcast hub + reconnection complexity for zero M1 value; runs take seconds to minutes.

**Budget enforcement granularity** â€” Enforced *between* nodes. Mid-node cancellation requires cancelling in-flight HTTP to OpenRouter â€” M2 concern. Consequence: a single runaway node can overshoot by one node's worth of cost/latency. Documented caveat.

**Eval variance at N=4** â€” With temperature > 0 on reasoning tasks, pass rate across 4 runs on 3â€“5 fixtures is noisy. M1 goal is that the metric **exists and is reproducible-given-seed**, not that it's a scientific benchmark. Recommend `temperature=0.2`; report pass rate as a fraction ("3/4") + std-dev of cost/latency, not a stable percentage.

---

## Critical files

- `backend/builder/api.py` â€” authoring surface. Everything user-facing flows through here.
- `backend/builder/compile.py` â€” translation to LangGraph + metadata emission. Integration fulcrum.
- `backend/runtime/executor.py` â€” where runtime, telemetry, budget, and checkpointing meet.
- `backend/workflows/coder_tester.py` â€” the M1 reference workflow and forcing function.
- `backend/checkpointing/replay.py` â€” the hardest single piece of M1 (config-override replay).
- `backend/evals/harness.py` â€” the load-bearing piece for the whole optimization story.

---

## Verification

End-to-end acceptance for M1 â€” every one of these must pass:

1. `pytest backend/tests/` â€” all tests green (builder validation, runtime, budget, checkpoint/replay, eval harness, provider mock).
2. `python -m backend.cli.main run coder_tester` â€” executes the coder/tester graph once against a sample input; produces a run directory under `runs/<run_id>/` with SQLite checkpoints and JSONL spans.
3. `python -m backend.cli.main eval coder_tester` â€” executes the graph 4x against `evals/coder_tester/fixtures.yaml`; produces a metrics JSON with `pass_rate`, `mean_cost_usd`, `mean_latency_ms`, `p95_latency_ms`.
4. `python -m backend.cli.main replay <run_id> --at coder --set coder.prompt="<alt prompt>"` â€” resumes from the coder checkpoint with a modified prompt and shows a different downstream path.
5. **Budget guard**: construct a workflow with `cost_budget_usd=0.0001`, run it, confirm `BudgetExceededError` raises cleanly between nodes.
6. **Iteration guard**: construct a coder/tester loop with `max_iterations=1`, force the tester to fail twice, confirm `MaxIterationsError` raises cleanly.
7. `python -m backend.cli.main export-mermaid coder_tester > graph.mmd` â€” produces a valid Mermaid string renderable on GitHub.
8. `python -m backend.cli.main serve` + `cd frontend && npm run dev` â€” React Flow UI renders the coder/tester topology with dagre layout; the run list populates after step 2/3 and updates within 2s of a new run.
9. **Builder rejection tests**: graphs missing `max_iterations` on a loop, missing `cost_budget_usd`, or creating a cycle via `add_edge` all raise at `compile()` time.

---

## Punted from M1 (tracked in FUTURE_SCOPE.md)

- Real sandboxed code execution in tester (M2)
- Telemetry overlays on React Flow (M1.5)
- Websocket live updates (M2, only if polling proves insufficient)
- Mutation engine / automated search / DSPy (M2+)
- Multi-model / multi-provider abstraction (M2)
- JSON graph import/export + canonical `GraphSpec` (M3 decision point)
- The other three workflow patterns (linear RAG, supervisor, dispatch/aggregate) (M2â€“M3)
- Mid-node budget cancellation / streaming cancel (M2)
- Eval statistical framework â€” confidence intervals, regression detection (M1.5)
- CLI `--set` overrides beyond one level deep (later)
- Schema-evolution-safe replay (M2)
- Human-in-the-loop / subgraphs (M4)
- Model-driven optimization spike, $20 cost cap (M5)

---

## Appendix â€” `FUTURE_SCOPE.md` content (to be written at repo root, step 0)

```markdown
# Future Scope

This file tracks everything deliberately deferred beyond M1. It is the single source of truth for "we decided not to do this yet, and here's why." Entries should be pruned as they graduate into real milestones.

## Explicitly deferred

### M1.5 â€” polish

- **Telemetry overlays on React Flow nodes** â€” per-node fail %, p95 latency, cost-per-run, retry count badges. Requires memoization care at 50+ nodes.
- **Eval statistical framework** â€” confidence intervals, regression detection, std-dev reporting beyond the M1 basics.

### M2 â€” second workflow pattern + ergonomics

- **Linear RAG pipeline** as the second reference workflow (`START â†’ query_analyser â†’ retriever â†’ reranker â†’ synthesiser â†’ END`). Forces the runtime to handle a non-loop topology.
- **Real sandboxed code execution in tester** â€” subprocess + timeout + resource limits. Replaces the M1 LLM-judge stub.
- **Websocket live updates** â€” only if polling proves insufficient in practice.
- **Mid-node budget cancellation** â€” cancel in-flight OpenRouter requests when budget is exceeded mid-stream.
- **Multi-model / multi-provider abstraction** â€” beyond OpenRouter + single model.
- **Schema-evolution-safe replay** â€” allow replay when state schema has changed between run and replay.
- **Streaming cancel** â€” cooperative cancellation during streaming LLM responses.

### M3 â€” remaining workflow patterns + schema decision

- **Supervisor loop pattern** â€” orchestrator chooses specialist per turn, loops until FINISH.
- **Dispatch-and-aggregate pattern** â€” boss fans out to parallel specialists, aggregator produces final verdict.
- **Decision point â€” canonical `GraphSpec`?** After four workflow patterns exist, evaluate whether the builder's internal form is straining against LangGraph's native representation. If yes, introduce explicit Pydantic `GraphSpec` / `NodeSpec` / `EdgeSpec` layer. Write the decision down either way.
- **JSON graph import/export** â€” only if `GraphSpec` is introduced. Required for M5.

### M4 â€” human-in-the-loop and subgraphs

- **Interrupts / approval nodes** â€” human-in-the-loop (LangGraph supports natively).
- **Reusable subgraphs** â€” nested workflows as first-class nodes.

### M5 â€” bounded optimization research spike

- **Claude Opus proposes 10 mutations** of an existing graph (model choice, retry counts, router thresholds, prompt versions â€” no structural mutations).
- **$20 hard cost cap** for the entire spike.
- Each mutation runs against the eval set; report which improved/regressed pass rate, cost, latency; variance.
- **Outcome is a writeup**, not shipped features. Decide from results whether to invest further.

## Specific technical debt to revisit

- **CLI `--set` deep overrides** â€” M1 supports one level deep only (`--set node.field=value`). Deeper override syntax if real use cases appear.
- **Cost estimation replaceability** â€” M1 hardcodes price table in `backend/providers/pricing.py`. First real need to change it â†’ swap to `prices.yaml` at startup.
- **Builder's internal form â†’ `GraphSpec`** â€” M3 decision point. If promoted, replay logic will need to switch from builder-rebuild to spec-mutate-then-compile.
- **OTEL GenAI semconv churn** â€” all attribute names isolated in `telemetry/genai_attrs.py`. Watch for spec updates.

## Explicitly rejected (do not add)

- Custom DSL with `@graph` decorator or `>>` operator overloading. One authoring surface: the typed Python builder.
- Cloud abstraction layer for LLM providers. OpenRouter + one model until M2.
- Hosted tracing backends (LangSmith, Studio). Local-first is a hard constraint.
- Haystack or other RAG-specific frameworks as the platform substrate. LangGraph is broader.
- Graphviz for static export. Mermaid covers the need and renders on GitHub natively.
```
