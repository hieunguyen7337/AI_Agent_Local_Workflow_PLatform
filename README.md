# Local AI Workflow Platform - M2.4

A local-first platform to author, run, visualize, and iterate on AI workflows.
M2.4 includes two reference workflows and dual-provider support:
- `coder_tester`: `planner -> coder -> tester -> gate -> (coder | END)`
- `linear_rag`: `query_analyser -> retriever -> reranker -> synthesiser -> END`

- Authoring: typed Python builder (`backend/builder/`)
- Runtime: LangGraph `StateGraph` + SQLite checkpointing
- Telemetry: OpenTelemetry-style span export to SQLite + JSONL
- Budget: cost + latency enforcement after a node completes and before the next node dispatches
- Tester: sandboxed Python execution (timeout/output guardrails) with LLM-judge fallback when no test code is provided
- Evals: YAML fixtures -> Nx runs -> metrics JSON + confidence intervals + baseline regression checks
- UI: FastAPI + React Flow topology + run list/detail + telemetry overlays + workflow selector + WebSocket live updates
- Providers: OpenRouter and direct OpenAI
- Workflow defaults: `coder_tester` -> OpenRouter `minimax/minimax-m2.7`; `linear_rag` -> OpenAI `gpt-4o-mini`
- Pricing: provider/model rates loaded from `prices.yaml`; budget correctness does not depend on provider stream-abort support

See [claude_full_plan.md](claude_full_plan.md) for the base architecture and [FUTURE_SCOPE.md](FUTURE_SCOPE.md) for deferred items.

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

## Verify M2.4 End-to-End

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

4. Run eval harnesses (`n=4`):

```powershell
.\.venv\Scripts\python -m backend.cli.main eval coder_tester --n 4
.\.venv\Scripts\python -m backend.cli.main eval linear_rag --n 4
```
`coder_tester` eval fixtures now include executable `test_code`, so evals run sandbox mode by default.

5. Optional baseline workflow:

```powershell
# Set baseline
.\.venv\Scripts\python -m backend.cli.main eval coder_tester --n 4 --update-baseline
.\.venv\Scripts\python -m backend.cli.main eval linear_rag --n 4 --update-baseline

# Compare and fail on regression
.\.venv\Scripts\python -m backend.cli.main eval coder_tester --n 4 --fail-on-regression
.\.venv\Scripts\python -m backend.cli.main eval linear_rag --n 4 --fail-on-regression
```

6. Export Mermaid diagrams:

```powershell
.\.venv\Scripts\python -m backend.cli.main export-mermaid coder_tester
.\.venv\Scripts\python -m backend.cli.main export-mermaid linear_rag
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
- workflow selector switches between `coder_tester` and `linear_rag`
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
- provider/model pricing is loaded from [prices.yaml](prices.yaml)
- runtime nodes resolve providers through the shared adapter layer in [backend/providers](backend/providers)

## Where To Read The Pipeline (for Optimization)

1. Workflow source-of-truth modules
- [backend/workflows/coder_tester.py](backend/workflows/coder_tester.py)
- [backend/workflows/linear_rag.py](backend/workflows/linear_rag.py)

2. Builder and compilation
- [backend/builder/api.py](backend/builder/api.py)
- [backend/builder/compile.py](backend/builder/compile.py)
- [backend/builder/validation.py](backend/builder/validation.py)

3. Runtime and nodes
- [backend/runtime/executor.py](backend/runtime/executor.py)
- [backend/runtime/nodes/llm.py](backend/runtime/nodes/llm.py)
- [backend/runtime/nodes/retriever.py](backend/runtime/nodes/retriever.py)
- [backend/runtime/nodes/tester.py](backend/runtime/nodes/tester.py)
- [backend/runtime/nodes/gate.py](backend/runtime/nodes/gate.py)

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
```

## M2.4 Limits (by design)

- Two reference workflows only (`coder_tester`, `linear_rag`)
- Two direct providers only (`openrouter`, `openai`)
- Replay supports scalar config overrides only (not state schema evolution)
- Budget checks happen after node completion, not during provider generation
- Eval variance at `n=4` is expected for non-deterministic model behavior
- Tester sandbox is best-effort local safety (not hardened container isolation)
