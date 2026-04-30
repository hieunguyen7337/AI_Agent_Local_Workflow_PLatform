# Session Handoff - 2026-04-30

This handoff captures recent implementation context for continuing work in a new Claude Code or Codex session.

## Current Implemented Baseline

- Workflow specs remain YAML-only under `workflows/*.yaml`.
- `GraphSpec` supports workflow library metadata: `category`, `tags`, `template`, and read-only `template_parameters`.
- `/api/workflows` returns validation health, source path, graph facts, template metadata, and static eval quality from `evals/<workflow>/`.
- Template copy is human-confirmed, writes `template: false`, clears `template_parameters`, and records audit files under `runs/spec_audit/`.
- `backend.runtime.run_workflow_function()` is the public local Python execution API for canonical workflow runs.
- `backend.runtime.run_workflow_batch()` runs many workflow-function calls with ordered results and bounded local thread concurrency.
- Fixture evals and dataset evals use the workflow-function boundary; proposal/optimization evals for in-memory candidate metadata still use low-level `run_graph`.
- Default batch/eval concurrency is now `50`.
- Telemetry is hardened for threaded high concurrency: a single process-wide tracer routes spans by `workflow.run_id` to each run's own `telemetry.db` and `spans.jsonl`.

## Important Recent Code Changes

- `backend/runtime/functions.py`
  - Defines `DEFAULT_MAX_CONCURRENCY = 50`.
  - Defines `WorkflowFunctionResult`, `WorkflowBatchItem`, `WorkflowBatchResult`.
  - `run_workflow_function()` accepts full workflow state or string `user_input`.
  - `run_workflow_batch()` uses `ThreadPoolExecutor`, preserves input order, and captures per-item errors.
- `backend/evals/harness.py`
  - `run_eval()` defaults to `max_concurrency=50`.
  - Canonical fixture evals call `run_workflow_batch()`.
  - `run_eval_for_metadata()` intentionally still uses `run_graph` for proposed YAML/optimization candidates.
- `backend/evals/dataset.py`
  - Adds generalized dataset evals for CSV, JSONL, and YAML list files.
  - Uses `dataset_eval.yaml` with row-to-state mapping and built-in scorers.
  - Defaults `max_concurrency` to `50`.
- `backend/server/routes.py`
  - Adds `POST /api/workflows/{workflow}/batch-run`.
  - `BatchRunRequest.max_concurrency` defaults to `50`.
  - `/api/runs` uses `run_workflow_function()`.
- `backend/telemetry/tracer.py` and `backend/telemetry/exporter.py`
  - Replaces per-run exporter registration with `RoutingSpanExporter`.
  - Registers `run_id -> run_dir` and routes spans by `workflow.run_id`.
  - Avoids `Overriding of current TracerProvider is not allowed` under concurrent runs.
- `prices.yaml`
  - Adds OpenRouter pricing for `google/gemma-4-26b-a4b-it`:
    - input: `$0.06/M`
    - output: `$0.33/M`

## Recent Test And Smoke Results

- Backend suite after telemetry hardening:
  - `.\.venv\Scripts\python -m pytest backend\tests -q`
  - Result: `181 passed`
- Frontend build:
  - `cd frontend && npm run build`
  - Result: passed.
- `coder_tester --n 8` comparison:
  - Sequential `max_concurrency=1`: `992.8s`, 24 runs, pass rate `95.8%`.
  - Parallel `max_concurrency=8`: `1550.1s`, 24 runs, pass rate `91.7%`.
  - Conclusion: concurrency was real, but tail latency and budget-exceeded outliers made this heavy workflow slower.
- `dispatch_aggregate` with temporary Gemma/OpenRouter workflow, exactly 20 jobs:
  - Sequential `max_concurrency=1`: `157.7s`, 20 runs, pass rate `100%`.
  - Parallel `max_concurrency=20`: `23.3s`, 20 runs, pass rate `100%`.
  - Conclusion: high concurrency can provide strong speedup for simpler workflows/providers.
- After this smoke, the temporary workflow `workflows/dispatch_aggregate_gemma_eval.yaml` was removed. The comparison artifacts remain under `runs/eval_compare_dispatch_gemma_20260430_124044/`.

## Current Dirty Worktree Notes

The worktree contains many modified and untracked files from recent milestones. Do not revert them blindly.

Expected recent additions include:

- `backend/runtime/functions.py`
- `backend/evals/dataset.py`
- `backend/tests/test_workflow_function.py`
- `backend/tests/test_dataset_eval.py`
- `evals/linear_rag/dataset.yaml`
- `evals/linear_rag/dataset_eval.yaml`

Expected deleted cleanup artifact:

- `workflows/simple_llm_ergonomics_smoke_copy.yaml`

The deletion is intentional cleanup from a previous browser smoke template copy. Its audit directory was also removed if present.

## Design Decisions To Preserve

- Do not introduce a second workflow authoring format.
- Do not make subdirectories under `workflows/` part of workflow IDs yet.
- Invalid specs remain discoverable through `/api/workflows`.
- Template copy does not substitute parameters or mutate prompts.
- `run_graph` remains the low-level executor for compiled metadata, replay, approval continuation internals, subgraph internals, and proposed YAML evals.
- `run_workflow_function` and `run_workflow_batch` are the public integration APIs.
- High default concurrency is local thread concurrency only; no distributed queue, hosted state, or provider-specific throttling has been added.

## Likely Next Milestone

The next product milestone is still **external local Python tool nodes**:

- Add a YAML node kind for approved local Python functions or model wrappers.
- Require an allowlist/import boundary.
- Require explicit state input mapping and output mapping.
- Capture artifacts and errors in normal run directories.
- Start with pure local functions before model-heavy wrappers such as CLIP.

Read `FUTURE_SCOPE.md`, `AGENTS.md`, and `docs/workflow_library.md` before designing or implementing this milestone.
