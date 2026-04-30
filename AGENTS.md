# AGENTS.md

Coding-agent operating guide for this repository. This is the concise map Codex and other agents should read before editing.

## Product Vision

This project is a local-first framework for many AI workflow types: RAG, coder/tester loops, orchestrators, routers, approval workflows, and reusable subgraphs.

The source of truth is YAML in `workflows/*.yaml`. LLMs should be able to read and propose changes to that YAML. Humans should be able to inspect the same workflow as a graph, review diffs/eval evidence/artifacts, and explicitly apply or roll back changes.

Do not introduce JSON, Python modules, decorators, or custom DSLs as alternate workflow authoring formats.

## Current Baseline (M5.21)

- YAML `GraphSpec` is canonical.
- Python workflow fallback has been removed.
- `backend/builder/` remains only an internal compile helper.
- Frontend workbench is organized into `Inspect`, `Run`, `Improve`, and `Recover`.
- Proposal loop exists: propose YAML -> validate -> diff -> eval -> human apply.
- Rollback restore exists and writes audit records.
- Approval nodes pause runs and can be approved/rejected with forked continuation runs.
- Subgraphs are supported for acyclic child workflows including approval-bearing children; nested approval pause/resume is fully wired.
- Approval subgraph eval harness: fixture-provided decisions drive the full pause/decide/resume cycle; scored against parent continuation final state.
- Run artifacts are structured under `runs/workflows/<workflow>/<YYYYMMDD>/<run_id>/`.
- `GET /api/workflows` scans `workflows/*.yaml` dynamically and returns metadata, validation health, source paths, and static graph facts; the frontend selector is API-driven, searchable, grouped by category, and shows compact library health counts.
- Reusable workflow templates are normal YAML `GraphSpec` files marked with `template: true`; the UI/API can copy them into new canonical workflow YAML files with `template: false` and a local audit record.
- Template parameterization is convention-first: templates may use normal prompt/state placeholders such as `{user_input}`, copy preserves them unchanged, and customization happens through source review or the proposal/apply loop.
- Template parameter metadata is schema-backed and documentation-only: `template_parameters` describe expected inputs for template UI review, and copied workflows clear them when becoming normal specs.
- Template copy ergonomics include local target id validation, duplicate-id feedback, and post-copy source/audit guidance; backend validation remains authoritative.
- Workflow library quality signals include static eval fixture presence/count and baseline freshness from `evals/<workflow>/`; these signals do not inspect latest runs or mutate baselines.
- Workflow-as-function runtime exposes validated YAML workflows as reusable local Python functions via `backend.runtime.run_workflow_function`, accepting full workflow state while preserving `user_input` compatibility.
- Batch workflow-function execution is available through `backend.runtime.run_workflow_batch` and `POST /api/workflows/{workflow}/batch-run`; default local concurrency is 50, results preserve input order, and each item writes normal isolated run artifacts.
- Fixture evals and generalized dataset eval adapters run canonical workflow calls through the workflow-function boundary, with bounded local concurrency 50 by default. Proposal/optimization evals for in-memory YAML candidates still use low-level `run_graph`.
- Telemetry is concurrency-safe: a single process-wide tracer routes spans by `workflow.run_id` to each run's own `telemetry.db` and `spans.jsonl`.
- `python_tool` nodes call allowlisted local Python functions; callables must appear in `python_tools.yaml`; `inputs` maps function kwargs to state keys; `output_state_key` captures the return value. See `docs/python_tools.md`.
- `llm` nodes can declare `image_inputs` that bind local image paths from state and send base64 `image_url` content parts to vision-capable providers at runtime.
- `person_reid_market1501` workflow demonstrates live boss/final-ranker LLM calls + query-side specialists + per-specialist `python_tool` retrievers over offline attribute DBs; `person_reid_market1501_eval` uses precomputed query attributes for the 100-query/500-gallery partition. Pipeline includes `rrf_precompute` (weighted RRF, attribute weight=10) and `parse_final_ranking` (LLM output parser with regex fallback). Scorer reads `final_state.ranked_gallery_ids`. Eval baseline: mAP=34.5%, CMC@1/5/10=35%.

Read `FUTURE_SCOPE.md` for the current next milestone and deferred work.

## Fast Repo Map

- `workflows/*.yaml` - canonical workflow specs (`coder_tester`, `linear_rag`, `supervisor_loop`, `dispatch_aggregate`, `approval_review`, `rag_subgraph_wrapper`, `approval_subgraph_wrapper`, `simple_llm_template`, `person_reid_market1501`, `person_reid_market1501_eval`).
- `python_tools.yaml` - allowlist of approved `python_tool` callable paths.
- `backend/tools/` - approved local Python functions callable from `python_tool` nodes.
- `backend/builder/python_tool_allowlist.py` - allowlist loader (cached, secure default = empty).
- `backend/runtime/nodes/python_tool.py` - `python_tool` node factory.
- `backend/graphspec/` - Pydantic spec models, YAML loading (`load_graph_spec`, `list_workflow_ids`), spec validation, conversion to metadata.
- `backend/builder/` - internal graph metadata and compile helpers.
- `backend/runtime/executor.py` - run execution and status handling.
- `backend/runtime/functions.py` - workflow-as-function and batch public API.
- `backend/runtime/artifacts.py` - run id, structured run dirs, manifest/artifact path helpers.
- `backend/runtime/nodes/` - runtime node implementations.
- `backend/checkpointing/replay.py` - replay behavior.
- `backend/evals/` - eval harness, fixtures, metrics, regression.
- `backend/evals/dataset.py` - generalized dataset eval adapters.
- `backend/server/routes.py` - FastAPI routes.
- `backend/cli/main.py` - CLI commands.
- `frontend/src/App.tsx` - graph/workbench shell.
- `frontend/src/components/SpecInspector.tsx` - inspect/improve/recover panels.
- `frontend/src/components/RunStarter.tsx` - UI run trigger.
- `frontend/src/components/RunList.tsx` - recent runs.
- `frontend/src/components/RunDetail.tsx` - run artifacts, spans, lineage, approvals.
- `frontend/src/components/ApprovalWorkbench.tsx` - pending/decided approvals.
- `frontend/src/components/GraphView.tsx` - React Flow graph canvas.
- `docs/run_artifacts.md` - how to inspect run files and SQLite DBs.
- `docs/ui_vision_audit.md` - manual UI audit checklist.
- `docs/workflow_library.md` - workflow naming, category, tag, template, and placeholder conventions.
- `docs/python_tools.md` - how to register callables in `python_tools.yaml` and author `python_tool` YAML nodes.
- `evals/person_reid_market1501/` - `dataset_eval.yaml`, `build_partition.py`, `build_attribute_dbs.py`, `build_dataset.py`, `build_gallery_db.py`, and gitignored partition/DB/index artifacts generated from local data.

## Common Commands

Backend tests:

```powershell
.\.venv\Scripts\python -m pytest backend\tests -q
```

Frontend build:

```powershell
cd frontend
npm run build
```

Run backend API:

```powershell
.\.venv\Scripts\python -m backend.cli.main serve --host 127.0.0.1 --port 8000
```

Run frontend:

```powershell
cd frontend
npm run dev
```

Run a workflow:

```powershell
.\.venv\Scripts\python -m backend.cli.main run coder_tester --input "write fizzbuzz"
```

Run eval:

```powershell
.\.venv\Scripts\python -m backend.cli.main eval approval_review --n 1
```

## Implementation Guidance

- Before editing, identify the smallest file set from the map above.
- Keep workflow authoring YAML-only.
- Validate YAML through `GraphSpec`; never trust raw YAML directly.
- Keep apply/rollback/template-copy writes human-confirmed.
- Keep proposal/eval/optimization artifacts under `runs/`; do not write canonical specs except through apply/restore flows.
- Preserve structured run directory resolution through `backend/runtime/artifacts.py`.
- Treat source runs for approvals as immutable audit records. Decisions create continuation lineage.
- Subgraphs support approval-bearing children (M5.8). Resume is handled via `_subgraph_resume` marker in state; source runs stay immutable.
- Keep UI graph-first. Workbench panels should support the graph, not replace it.
- Use `run_workflow_function` for canonical local integrations and `run_workflow_batch` for multi-input calls. Keep `run_graph` for compiled metadata, proposal evals, replay, approval continuation internals, subgraph internals, and low-level runtime tests.

## Testing Expectations

- Backend behavior change: add/update tests and run `pytest backend\tests -q`.
- Frontend/type change: run `npm run build`.
- API shape change: update `frontend/src/types.ts`, API client, route tests, and docs if public.
- Workflow spec change: add/update GraphSpec validation tests and eval fixtures if behavior changes.
- UI workflow change: use `docs/ui_vision_audit.md` for manual smoke checks when practical.

## Avoid

- Do not reintroduce `backend/workflows/*.py` as runtime workflow definitions.
- Do not add JSON import/export as a future direction unless the project vision changes.
- Do not add hosted tracing, LangSmith, Studio, or cloud persistence.
- Do not add a custom DSL.
- Do not auto-apply LLM-generated workflow changes.
- Do not flatten or rename run artifacts without updating resolver code, API responses, docs, and tests.

## Current Next Step

Check `FUTURE_SCOPE.md`. The next milestone is **real specialist model wrappers for person reID**: replacing the three placeholder `python_tool` specialists in `person_reid_market1501` with real model implementations (TransReID/ViT, CLIP/SigLIP, HMR2.0/SMPLify). Current eval baseline with stubs: mAP=34.5%, CMC@1/5/10=35% (attribute channel weight=10 in RRF). Requires heavyweight model dependency and GPU loading choices. Read `FUTURE_SCOPE.md` and `docs/python_tools.md` before designing.

Implementation guardrails (carry forward):

- Keep Python tool execution local, explicit, allowlisted, and auditable (`python_tools.yaml`).
- Do not allow arbitrary module import from YAML without an approved boundary.
- Do not add hosted state, cloud persistence, or auth.
- Do not introduce a second workflow authoring format.
