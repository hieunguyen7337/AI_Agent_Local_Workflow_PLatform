# Local AI Workflow Platform - M5.6

A local-first platform to author, run, visualize, and iterate on AI workflows.
M5.6 uses declarative YAML workflow specs as the editable source of truth, validated by Pydantic `GraphSpec`, reviewed in the UI, and compiled into the existing LangGraph runtime with YAML-only workflow loading, structured run artifacts, human approval interrupts, forked continuation runs, an approval workbench, approval-aware eval coverage, reusable collapsed subgraphs, richer parent/child subgraph review, multi-proposal optimization reports, and audited rollback restore:

## Vision

This project aims to become an adaptable framework for many types of agent workflow and pipeline, including RAG, coder/tester loops, orchestrators, routers, and other multi-agent patterns.

Each workflow should be built from a simple source-of-truth file that is easy for an LLM to understand, analyze, mutate, and iterate on. For humans, the same source of truth should render into a frontend graph that shows the workflow structure and each node's metadata, so the full pipeline is easy to inspect and modification proposals are easy to reason about.

- `coder_tester`: `planner -> coder -> tester -> gate -> (coder | END)`
- `linear_rag`: `query_analyser -> retriever -> reranker -> synthesiser -> END`
- `supervisor_loop`: `supervisor -> dispatch -> (researcher | writer | END)` with bounded specialist loop-backs
- `dispatch_aggregate`: `dispatcher -> specialist_a + specialist_b -> aggregator -> END`
- `approval_review`: `draft -> human_review approval -> (finalizer | END)`
- `rag_subgraph_wrapper`: `rag_child subgraph(linear_rag) -> END`

- Authoring: YAML workflow specs (`workflows/*.yaml`) backed by Pydantic `GraphSpec` (`backend/graphspec/`)
- Compiler helper: typed Python builder (`backend/builder/`) is an internal metadata/compiler layer, not a workflow authoring surface
- Runtime: LangGraph `StateGraph` + SQLite checkpointing
- Telemetry: OpenTelemetry-style span export to SQLite + JSONL
- Budget: cost + latency enforcement after a node completes and before the next node dispatches
- Tester: sandboxed Python execution (timeout/output guardrails) with LLM-judge fallback when no test code is provided
- Evals: YAML fixtures -> Nx runs -> metrics JSON + confidence intervals + baseline regression checks
- UI: FastAPI + React Flow topology + source inspector + proposal review/eval/apply + approval workbench + run list/detail + telemetry overlays + workflow selector + WebSocket live updates
- Providers: OpenRouter and direct OpenAI
- Workflow defaults: `coder_tester` -> OpenRouter `minimax/minimax-m2.7`; `linear_rag`, `supervisor_loop`, and `dispatch_aggregate` -> OpenAI `gpt-4o-mini`
- Pricing: provider/model rates loaded from `prices.yaml`; budget correctness does not depend on provider stream-abort support

See [docs/graphspec_decision.md](docs/graphspec_decision.md) for the source-of-truth decision, [docs/run_artifacts.md](docs/run_artifacts.md) for how to inspect run files, [claude_full_plan.md](claude_full_plan.md) for the base architecture, and [FUTURE_SCOPE.md](FUTURE_SCOPE.md) for deferred items.

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

## Verify M5.6 End-to-End

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

6. Run one `approval_review` execution from the approval node to confirm pending approval behavior:

```powershell
.\.venv\Scripts\python -m backend.cli.main run approval_review --input "Draft a short approval-gated answer."
```

Expected status is `pending_approval`; the run writes `runs/<run_id>/approval.json`. In the UI, the selected run can then be approved or rejected, which creates `approval_decision.json` and a forked continuation run.

7. Run one `rag_subgraph_wrapper` execution to confirm nested subgraph behavior:

```powershell
.\.venv\Scripts\python -m backend.cli.main run rag_subgraph_wrapper --input "What is the refund window for Nimbus Cloud subscriptions?"
```

Expected status is `ok`; the parent run writes subgraph lineage under `runs/<parent_run_id>/subgraphs/`, and the child run writes `parent_run.json`.

8. Run workflow eval harnesses (`n=4`):

```powershell
.\.venv\Scripts\python -m backend.cli.main eval coder_tester --n 4
.\.venv\Scripts\python -m backend.cli.main eval linear_rag --n 4
.\.venv\Scripts\python -m backend.cli.main eval supervisor_loop --n 4
.\.venv\Scripts\python -m backend.cli.main eval dispatch_aggregate --n 4
.\.venv\Scripts\python -m backend.cli.main eval approval_review --n 4
.\.venv\Scripts\python -m backend.cli.main eval rag_subgraph_wrapper --n 4
```
`coder_tester` eval fixtures include executable `test_code`, so evals run sandbox mode by default. `approval_review` fixtures include approval decisions so evals can resume without live human input.
`rag_subgraph_wrapper` fixtures score mapped `rag_answer` output and preserve parent/child lineage in eval results.

9. Optional baseline workflow:

```powershell
# Set baseline
.\.venv\Scripts\python -m backend.cli.main eval coder_tester --n 4 --update-baseline
.\.venv\Scripts\python -m backend.cli.main eval linear_rag --n 4 --update-baseline
.\.venv\Scripts\python -m backend.cli.main eval supervisor_loop --n 4 --update-baseline
.\.venv\Scripts\python -m backend.cli.main eval dispatch_aggregate --n 4 --update-baseline
.\.venv\Scripts\python -m backend.cli.main eval approval_review --n 4 --update-baseline
.\.venv\Scripts\python -m backend.cli.main eval rag_subgraph_wrapper --n 4 --update-baseline

# Compare and fail on regression
.\.venv\Scripts\python -m backend.cli.main eval coder_tester --n 4 --fail-on-regression
.\.venv\Scripts\python -m backend.cli.main eval linear_rag --n 4 --fail-on-regression
.\.venv\Scripts\python -m backend.cli.main eval supervisor_loop --n 4 --fail-on-regression
.\.venv\Scripts\python -m backend.cli.main eval dispatch_aggregate --n 4 --fail-on-regression
.\.venv\Scripts\python -m backend.cli.main eval approval_review --n 4 --fail-on-regression
.\.venv\Scripts\python -m backend.cli.main eval rag_subgraph_wrapper --n 4 --fail-on-regression
```

10. Export Mermaid diagrams:

```powershell
.\.venv\Scripts\python -m backend.cli.main export-mermaid coder_tester
.\.venv\Scripts\python -m backend.cli.main export-mermaid linear_rag
.\.venv\Scripts\python -m backend.cli.main export-mermaid supervisor_loop
.\.venv\Scripts\python -m backend.cli.main export-mermaid dispatch_aggregate
.\.venv\Scripts\python -m backend.cli.main export-mermaid approval_review
.\.venv\Scripts\python -m backend.cli.main export-mermaid rag_subgraph_wrapper
```

11. Optional replay workflow:

```powershell
# Full rerun from migrated source snapshot into a new run directory
.\.venv\Scripts\python -m backend.cli.main replay <source_run_id> --workflow coder_tester

# Replay from a real node boundary with config overrides
.\.venv\Scripts\python -m backend.cli.main replay <source_run_id> --workflow coder_tester --at coder --set coder.temperature=0.1
```

12. Cooperative cancellation:

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
npm run dev
```

Open `http://127.0.0.1:5173`.

Expected behavior:
- workflow selector switches between `coder_tester`, `linear_rag`, `supervisor_loop`, `dispatch_aggregate`, `approval_review`, and `rag_subgraph_wrapper`
- graph renders for selected workflow
- selecting a graph node opens source metadata in the inspector
- selecting a subgraph node can open the referenced child graph without changing the parent workflow selector
- the sidebar run form starts the selected workflow from user input and selects the created run
- source inspector shows raw YAML, validation status, mutation proposal diffs, proposal eval summaries, and apply results
- the Propose tab can generate multiple optimization candidates, evaluate them under a shared cost cap, and recommend a candidate for human review
- the Rollback tab lists apply/restore snapshots, previews diffs, and restores selected YAML after confirmation
- applying a valid proposal writes `workflows/*.yaml` and creates an audit record plus rollback snapshot
- pending approval runs show approval node, prompt, timestamp, artifact path, and approve/reject controls in run detail
- approvals panel shows pending and decided approvals, with source and continuation run navigation
- run detail shows parent-to-child and child-to-parent subgraph lineage navigation
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
- CLI, eval, replay, and API loading use YAML specs only.
- `/api/graph/{workflow}` returns topology plus full node metadata so the frontend can show both graph shape and node configuration.
- `/api/spec/{workflow}` returns raw YAML plus validated `GraphSpec` JSON.
- `/api/approvals?status=pending|decided|all` returns approval artifacts discovered under `runs/*/approval.json`, with decision metadata when present.
- YAML is the human/LLM editing format. Pydantic `GraphSpec` is the trusted contract.

## Source Inspection And Proposal Review

- `Node`, `Source`, and `Validation` tabs expose node metadata, raw YAML, schema status, budgets, edges, and loops.
- The `Propose` tab calls `POST /api/spec/{workflow}/propose-mutation` to ask an LLM for a complete revised YAML spec.
- Proposed YAML is validated through `GraphSpec` and returned with a unified diff; workflow files are not modified.
- Valid proposals can be evaluated with `POST /api/spec/{workflow}/evaluate-proposal`, which runs existing eval fixtures against the proposed spec in memory.
- Proposal eval artifacts are written under `runs/proposal_eval_<workflow>_<timestamp>/eval.json`.
- Multiple candidates can be generated and compared with `POST /api/spec/{workflow}/optimize-proposals`.
- Optimization reports are written under `runs/optimization_<workflow>_<timestamp>/report.json` and rank candidates by regression status, pass rate, cost, and latency.
- Valid proposals can be accepted with `POST /api/spec/{workflow}/apply-proposal` after explicit human confirmation in the UI.
- Applying a proposal writes the canonical YAML file, refreshes graph/source views, and creates `runs/spec_audit/<workflow>/<timestamp>/audit.json` plus `original.yaml` rollback snapshot.
- Rollback snapshots can be listed, previewed, and restored through `/api/spec/{workflow}/rollback-snapshots`.
- Restoring a snapshot validates the YAML, writes the canonical workflow file, refreshes graph/source views, and creates a new restore audit entry.

## Approval Interrupts

- YAML specs can include `kind: approval` nodes with a prompt, approval state key, approved target, and rejected target.
- An approval node pauses execution with run status `pending_approval` and writes `runs/<run_id>/approval.json`.
- Approval artifacts include workflow, run id, node id, prompt, targets, timestamp, and a review state snapshot.
- `/api/approvals` and `/api/runs/{run_id}` expose approval metadata read-only.
- `POST /api/approvals/{run_id}/decision` records an approve/reject decision and forks a continuation run.
- Decision artifacts are written to `runs/<run_id>/approval_decision.json`; continuation lineage is written to `runs/<continuation_run_id>/approval_resume.json`.
- The frontend approval workbench separates pending and decided approvals and links between source and continuation runs.
- Eval fixtures can provide `approval_decision: approved|rejected` so approval workflows can be evaluated without live human input.

## Reusable Subgraphs

- YAML specs can include `kind: subgraph` nodes that reference another workflow by id.
- Subgraph `inputs` map parent state keys into child workflow state keys; `outputs` map child final-state keys back into parent state keys.
- Subgraph executions create nested local runs and write parent/child lineage artifacts.
- Subgraph nodes are shown collapsed in the graph and source inspector with their referenced workflow and mappings.
- The source inspector can open the referenced child graph in place for read-only review.
- Run detail exposes subgraph child runs and parent run links from lineage artifacts.
- Eval fixtures can score mapped subgraph output state such as `rag_answer`.
- M5.2 intentionally supports acyclic, non-approval child workflows only; nested approval resume semantics are deferred.

## Replay Behavior

- `workflow replay` now forks into a new run directory instead of mutating the source run
- `--at <node>` is functional and replays from the most recent checkpoint boundary that would execute that node
- if `--input` is omitted, replay defaults to the source snapshot's `user_input`
- additive and removed state-schema changes are handled by default migration
- each replay run writes lineage metadata to `runs/<replay_run_id>/replay.json`

## Cancellation Behavior

- `workflow run`, `workflow replay`, and `workflow eval` support cooperative Ctrl+C cancellation
- the first Ctrl+C requests graceful cancellation of the active streamed LLM node and the run ends with `status: "cancelled"`
- the second Ctrl+C exits immediately
- cancellation is user-driven only; it is distinct from the intentionally rejected mid-node budget cancellation behavior
- the current web UI does not start or stop runs

## Where To Read The Pipeline (for Optimization)

1. Workflow source-of-truth specs
- [workflows/coder_tester.yaml](workflows/coder_tester.yaml)
- [workflows/linear_rag.yaml](workflows/linear_rag.yaml)
- [workflows/supervisor_loop.yaml](workflows/supervisor_loop.yaml)
- [workflows/dispatch_aggregate.yaml](workflows/dispatch_aggregate.yaml)
- [workflows/approval_review.yaml](workflows/approval_review.yaml)
- [workflows/rag_subgraph_wrapper.yaml](workflows/rag_subgraph_wrapper.yaml)

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
- [backend/runtime/nodes/subgraph.py](backend/runtime/nodes/subgraph.py)

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
- [evals/approval_review/fixtures.yaml](evals/approval_review/fixtures.yaml)
- [evals/rag_subgraph_wrapper/fixtures.yaml](evals/rag_subgraph_wrapper/fixtures.yaml)

## Run Artifacts

Execution runs are written to workflow/date folders with readable run ids:

```text
runs/workflows/<workflow>/<YYYYMMDD>/<run_id>/
  run_manifest.json
  telemetry.db
  spans.jsonl
  checkpoints.db
  subgraphs/<node_id>_<child_run_id>.json
  parent_run.json
  approval.json
  approval_decision.json
  approval_resume.json
```

Run ids use `run_YYYYMMDDTHHMMSSZ_<workflow>_<8hex>`, for example `run_20260425T091230Z_coder_tester_a1b2c3d4`.
Open `run_manifest.json` first for a summary and artifact paths. `telemetry.db` is a SQLite database for run/spans inspection; `checkpoints.db` is LangGraph replay/resume state. See [docs/run_artifacts.md](docs/run_artifacts.md) for DB Browser, `sqlite3`, and Python inspection commands.

Main eval and audit outputs:

```text
runs/eval_coder_tester.json
runs/eval_linear_rag.json
runs/eval_supervisor_loop.json
runs/eval_dispatch_aggregate.json
runs/eval_approval_review.json
runs/eval_rag_subgraph_wrapper.json
runs/proposal_eval_<workflow>_<timestamp>/eval.json
runs/optimization_<workflow>_<timestamp>/report.json
runs/spec_audit/<workflow>/<timestamp>/audit.json
runs/spec_audit/<workflow>/<timestamp>/original.yaml
```

## M5.6 Limits (by design)

- Six reference workflows only (`coder_tester`, `linear_rag`, `supervisor_loop`, `dispatch_aggregate`, `approval_review`, `rag_subgraph_wrapper`)
- Two direct providers only (`openrouter`, `openai`)
- YAML workflow specs are the editable source of truth; legacy Python workflow modules are no longer a runtime fallback
- mutation proposals and proposal evals are read-only until a human explicitly applies a valid proposal
- optimization can recommend a candidate, but cannot automatically apply it
- applying a proposal and restoring a rollback snapshot both create audit records, but there is no multi-user approval or auth layer
- approval resume uses forked continuation runs; in-place continuation of the original run is intentionally avoided
- approval workflows can be evaluated with fixture-provided decisions, but approval-containing child subgraphs are still deferred
- subgraph UI renders collapsed parent nodes with read-only child graph inspection; full inline graph expansion/editing is still deferred
- Branch outputs in `dispatch_aggregate` remain fixed named state keys rather than a generic map-reduce collection
- Replay stays within the same workflow id and still assumes stable node ids for the selected replay point
- Generic replay migration covers additive/removal schema changes; rename-level replay migrations need a future YAML/spec-aware compatibility path
- Streaming cancellation applies to LLM provider calls only; retriever and sandbox tester still stop at node boundaries
- Budget checks happen after node completion, not during provider generation
- Eval variance at `n=4` is expected for non-deterministic model behavior
- Tester sandbox is best-effort local safety (not hardened container isolation)
