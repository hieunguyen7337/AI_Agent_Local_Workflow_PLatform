# Local AI Workflow Platform - M3.3

A local-first platform to author, run, visualize, and iterate on AI workflows.
M3.3 promotes declarative YAML workflow specs as the editable source of truth, validated by Pydantic `GraphSpec` and compiled into the existing LangGraph runtime:

## Vision

This project aims to become an adaptable framework for many types of agent workflow and pipeline, including RAG, coder/tester loops, orchestrators, routers, and other multi-agent patterns.

Each workflow should be built from a simple source-of-truth file that is easy for an LLM to understand, analyze, mutate, and iterate on. For humans, the same source of truth should render into a frontend graph that shows the workflow structure and each node's metadata, so the full pipeline is easy to inspect and modification proposals are easy to reason about.

- `coder_tester`: `planner -> coder -> tester -> gate -> (coder | END)`
- `linear_rag`: `query_analyser -> retriever -> reranker -> synthesiser -> END`
- `supervisor_loop`: `supervisor -> dispatch -> (researcher | writer | END)` with bounded specialist loop-backs
- `dispatch_aggregate`: `dispatcher -> specialist_a + specialist_b -> aggregator -> END`

- Authoring: YAML workflow specs (`workflows/*.yaml`) backed by Pydantic `GraphSpec` (`backend/graphspec/`)
- Compatibility: typed Python builder (`backend/builder/`) remains as a fallback during migration
- Runtime: LangGraph `StateGraph` + SQLite checkpointing
- Telemetry: OpenTelemetry-style span export to SQLite + JSONL
- Budget: cost + latency enforcement after a node completes and before the next node dispatches
- Tester: sandboxed Python execution (timeout/output guardrails) with LLM-judge fallback when no test code is provided
- Evals: YAML fixtures -> Nx runs -> metrics JSON + confidence intervals + baseline regression checks
- UI: FastAPI + React Flow topology + run list/detail + telemetry overlays + workflow selector + WebSocket live updates
- Providers: OpenRouter and direct OpenAI
- Workflow defaults: `coder_tester` -> OpenRouter `minimax/minimax-m2.7`; `linear_rag`, `supervisor_loop`, and `dispatch_aggregate` -> OpenAI `gpt-4o-mini`
- Pricing: provider/model rates loaded from `prices.yaml`; budget correctness does not depend on provider stream-abort support

See [docs/graphspec_decision.md](docs/graphspec_decision.md) for the source-of-truth decision, [claude_full_plan.md](claude_full_plan.md) for the base architecture, and [FUTURE_SCOPE.md](FUTURE_SCOPE.md) for deferred items.

## Full Setup (Windows PowerShell)

Run these from the repo root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -e ".[dev]"

Copy-Item .env.example .env
# Edit .env and set both API keys for full repo coverage:
# OPENROUTER_API_KEY=your_key_here
# OPENAI_API_KEY=your_key_here
```

Install frontend dependencies:

```powershell
cd frontend
npm install
cd ..
```

## Full Setup (macOS/Linux)

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

cp .env.example .env
# edit .env and set both API keys for full repo coverage:
# OPENROUTER_API_KEY=your_key_here
# OPENAI_API_KEY=your_key_here

cd frontend
npm install
cd ..
```

## Verify M3.3 End-to-End

From repo root (Windows commands shown):

1. Run backend tests:

```powershell
.\.venv\Scripts\python -m pytest backend/tests -q
```

2. Run one `coder_tester` execution (OpenRouter default):

```powershell
.\.venv\Scripts\python -m backend.cli.main run coder_tester --input "write fizzbuzz"
```

Optional explicit sandbox test file:

```powershell
.\.venv\Scripts\python -m backend.cli.main run coder_tester --input "write fizzbuzz" --test-code-file .\my_test_code.py
```

3. Run one `linear_rag` execution (direct OpenAI default):

```powershell
.\.venv\Scripts\python -m backend.cli.main run linear_rag --input "What is the refund window for Nimbus Cloud subscriptions?"
```

4. Run one `supervisor_loop` execution (direct OpenAI default):

```powershell
.\.venv\Scripts\python -m backend.cli.main run supervisor_loop --input "Explain why workflow telemetry matters in 2-3 sentences. Include the exact phrase 'audit trail'."
```

5. Run one `dispatch_aggregate` execution (direct OpenAI default):

```powershell
.\.venv\Scripts\python -m backend.cli.main run dispatch_aggregate --input "Explain why local-first workflow tools matter in 2-3 sentences. Include the exact phrase 'fast feedback and audit trail'."
```

6. Run workflow eval harnesses (`n=4`):

```powershell
.\.venv\Scripts\python -m backend.cli.main eval coder_tester --n 4
.\.venv\Scripts\python -m backend.cli.main eval linear_rag --n 4
.\.venv\Scripts\python -m backend.cli.main eval supervisor_loop --n 4
.\.venv\Scripts\python -m backend.cli.main eval dispatch_aggregate --n 4
```
`coder_tester` eval fixtures now include executable `test_code`, so evals run sandbox mode by default.

7. Optional baseline workflow:

```powershell
# Set baseline
.\.venv\Scripts\python -m backend.cli.main eval coder_tester --n 4 --update-baseline
.\.venv\Scripts\python -m backend.cli.main eval linear_rag --n 4 --update-baseline
.\.venv\Scripts\python -m backend.cli.main eval supervisor_loop --n 4 --update-baseline
.\.venv\Scripts\python -m backend.cli.main eval dispatch_aggregate --n 4 --update-baseline

# Compare and fail on regression
.\.venv\Scripts\python -m backend.cli.main eval coder_tester --n 4 --fail-on-regression
.\.venv\Scripts\python -m backend.cli.main eval linear_rag --n 4 --fail-on-regression
.\.venv\Scripts\python -m backend.cli.main eval supervisor_loop --n 4 --fail-on-regression
.\.venv\Scripts\python -m backend.cli.main eval dispatch_aggregate --n 4 --fail-on-regression
```

8. Export Mermaid diagrams:

```powershell
.\.venv\Scripts\python -m backend.cli.main export-mermaid coder_tester
.\.venv\Scripts\python -m backend.cli.main export-mermaid linear_rag
.\.venv\Scripts\python -m backend.cli.main export-mermaid supervisor_loop
.\.venv\Scripts\python -m backend.cli.main export-mermaid dispatch_aggregate
```

9. Optional replay workflow:

```powershell
# Full rerun from migrated source snapshot into a new run directory
.\.venv\Scripts\python -m backend.cli.main replay <source_run_id> --workflow coder_tester

# Replay from a real node boundary with config overrides
.\.venv\Scripts\python -m backend.cli.main replay <source_run_id> --workflow coder_tester --at coder --set coder.temperature=0.1
```

10. Cooperative cancellation:

```powershell
# While any long-running command is active:
# First Ctrl+C -> request graceful cancellation
# Second Ctrl+C -> force immediate exit
.\.venv\Scripts\python -m backend.cli.main run coder_tester --input "write fizzbuzz"
.\.venv\Scripts\python -m backend.cli.main replay <source_run_id> --workflow coder_tester --at coder
.\.venv\Scripts\python -m backend.cli.main eval coder_tester --n 4
```

## Run Backend + Frontend Locally

Open two terminals.

Terminal 1 (backend API on `127.0.0.1:8000`):

```powershell
cd <repo-root>
.\.venv\Scripts\python -m backend.cli.main serve --host 127.0.0.1 --port 8000
```

Terminal 2 (frontend on `127.0.0.1:5173`):

```powershell
cd <repo-root>\frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`.

Expected behavior:
- workflow selector switches between `coder_tester`, `linear_rag`, `supervisor_loop`, and `dispatch_aggregate`
- graph renders for selected workflow
- run list/detail and node overlays update live via `/ws/live` (no polling loop required)
- selecting a run shows status/cost/latency/spans
- nodes show overlay badges: `Fail %`, `P95`, `$/run`, `Retries/run`
- backend restarts trigger reconnect + REST resync (`/api/runs`, `/api/runs/{run_id}`, `/api/graph/{workflow}/node-metrics`)

## Live Update Channel

- WebSocket endpoint: `/ws/live`
- Subscribe message:

```json
{"action":"subscribe","workflow":"coder_tester"}
```

- Event envelope:

```json
{
  "type": "run_updated",
  "workflow": "coder_tester",
  "run_id": "run_...",
  "emitted_at_ns": 1234567890,
  "data": {}
}
```

## Provider Configuration

- `.env.example` includes both `OPENROUTER_API_KEY` and `OPENAI_API_KEY`
- `coder_tester` defaults to `provider="openrouter"` with model `minimax/minimax-m2.7`
- `linear_rag` defaults to `provider="openai"` with model `gpt-4o-mini`
- `supervisor_loop` defaults to `provider="openai"` with model `gpt-4o-mini`
- `dispatch_aggregate` defaults to `provider="openai"` with model `gpt-4o-mini`
- provider/model pricing is loaded from [prices.yaml](prices.yaml)
- runtime nodes resolve providers through the shared adapter layer in [backend/providers](backend/providers)

## Workflow Source Of Truth

- Canonical editable specs live in [workflows](workflows).
- Specs are parsed by [backend/graphspec](backend/graphspec), validated as `GraphSpec`, and adapted to the existing runtime `GraphMetadata`.
- CLI, eval, replay, and API loading prefer YAML specs and fall back to Python workflow modules only for compatibility.
- `/api/graph/{workflow}` returns topology plus full node metadata so the frontend can show both graph shape and node configuration.
- YAML is the human/LLM editing format. Pydantic `GraphSpec` is the trusted contract.

## Replay Behavior

- `workflow replay` now forks into a new run directory instead of mutating the source run
- `--at <node>` is functional and replays from the most recent checkpoint boundary that would execute that node
- if `--input` is omitted, replay defaults to the source snapshot's `user_input`
- additive and removed state-schema changes are handled by default migration
- workflow-specific rename/reshape fixes can be implemented with `migrate_replay_state(...)` in the workflow module
- each replay run writes lineage metadata to `runs/<replay_run_id>/replay.json`

## Cancellation Behavior

- `workflow run`, `workflow replay`, and `workflow eval` support cooperative Ctrl+C cancellation
- the first Ctrl+C requests graceful cancellation of the active streamed LLM node and the run ends with `status: "cancelled"`
- the second Ctrl+C exits immediately
- cancellation is user-driven only; it is distinct from the intentionally rejected mid-node budget cancellation behavior
- the current web UI remains read-only and does not start or stop runs

## Where To Read The Pipeline (for Optimization)

1. Workflow source-of-truth specs
- [workflows/coder_tester.yaml](workflows/coder_tester.yaml)
- [workflows/linear_rag.yaml](workflows/linear_rag.yaml)
- [workflows/supervisor_loop.yaml](workflows/supervisor_loop.yaml)
- [workflows/dispatch_aggregate.yaml](workflows/dispatch_aggregate.yaml)

2. GraphSpec, builder, and compilation
- [backend/graphspec/models.py](backend/graphspec/models.py)
- [backend/graphspec/loader.py](backend/graphspec/loader.py)
- [backend/builder/api.py](backend/builder/api.py)
- [backend/builder/compile.py](backend/builder/compile.py)
- [backend/builder/validation.py](backend/builder/validation.py)

3. Runtime and nodes
- [backend/runtime/executor.py](backend/runtime/executor.py)
- [backend/runtime/nodes/llm.py](backend/runtime/nodes/llm.py)
- [backend/runtime/nodes/retriever.py](backend/runtime/nodes/retriever.py)
- [backend/runtime/nodes/tester.py](backend/runtime/nodes/tester.py)
- [backend/runtime/nodes/gate.py](backend/runtime/nodes/gate.py)
- [backend/runtime/nodes/router.py](backend/runtime/nodes/router.py)

4. Provider abstraction and pricing
- [backend/providers/base.py](backend/providers/base.py)
- [backend/providers/openrouter.py](backend/providers/openrouter.py)
- [backend/providers/openai.py](backend/providers/openai.py)
- [backend/providers/pricing.py](backend/providers/pricing.py)
- [prices.yaml](prices.yaml)

5. Evals and regression
- [backend/evals/harness.py](backend/evals/harness.py)
- [backend/evals/metrics.py](backend/evals/metrics.py)
- [backend/evals/regression.py](backend/evals/regression.py)
- [evals/coder_tester/fixtures.yaml](evals/coder_tester/fixtures.yaml)
- [evals/linear_rag/corpus.yaml](evals/linear_rag/corpus.yaml)
- [evals/linear_rag/fixtures.yaml](evals/linear_rag/fixtures.yaml)
- [evals/supervisor_loop/fixtures.yaml](evals/supervisor_loop/fixtures.yaml)
- [evals/dispatch_aggregate/fixtures.yaml](evals/dispatch_aggregate/fixtures.yaml)

## Run Artifacts

Each run is written to:

```text
runs/<run_id>/
  telemetry.db
  spans.jsonl
  checkpoints.db
```

Main eval outputs:

```text
runs/eval_coder_tester.json
runs/eval_linear_rag.json
runs/eval_supervisor_loop.json
runs/eval_dispatch_aggregate.json
```

## M3.3 Limits (by design)

- Four reference workflows only (`coder_tester`, `linear_rag`, `supervisor_loop`, `dispatch_aggregate`)
- Two direct providers only (`openrouter`, `openai`)
- YAML workflow specs are the editable source of truth; Python workflow modules remain only as compatibility fallback
- JSON graph import/export is still deferred until the YAML + `GraphSpec` contract stabilizes
- Branch outputs in `dispatch_aggregate` remain fixed named state keys rather than a generic map-reduce collection
- Replay stays within the same workflow id and still assumes stable node ids for the selected replay point
- Generic replay migration covers additive/removal schema changes; rename-level changes need workflow hook logic
- Streaming cancellation applies to LLM provider calls only; retriever and sandbox tester still stop at node boundaries
- Budget checks happen after node completion, not during provider generation
- Eval variance at `n=4` is expected for non-deterministic model behavior
- Tester sandbox is best-effort local safety (not hardened container isolation)
