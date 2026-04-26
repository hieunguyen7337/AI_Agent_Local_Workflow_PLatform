# AGENTS.md

Coding-agent operating guide for this repository. This is the concise map Codex and other agents should read before editing.

## Product Vision

This project is a local-first framework for many AI workflow types: RAG, coder/tester loops, orchestrators, routers, approval workflows, and reusable subgraphs.

The source of truth is YAML in `workflows/*.yaml`. LLMs should be able to read and propose changes to that YAML. Humans should be able to inspect the same workflow as a graph, review diffs/eval evidence/artifacts, and explicitly apply or roll back changes.

Do not introduce JSON, Python modules, decorators, or custom DSLs as alternate workflow authoring formats.

## Current Baseline (M5.10)

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
- `GET /api/workflows` scans `workflows/*.yaml` dynamically; the frontend selector is fully API-driven — no hardcoded workflow list.

Read `FUTURE_SCOPE.md` for the current next milestone and deferred work.

## Fast Repo Map

- `workflows/*.yaml` - canonical workflow specs (`coder_tester`, `linear_rag`, `supervisor_loop`, `dispatch_aggregate`, `approval_review`, `rag_subgraph_wrapper`, `approval_subgraph_wrapper`).
- `backend/graphspec/` - Pydantic spec models, YAML loading (`load_graph_spec`, `list_workflow_ids`), spec validation, conversion to metadata.
- `backend/builder/` - internal graph metadata and compile helpers.
- `backend/runtime/executor.py` - run execution and status handling.
- `backend/runtime/artifacts.py` - run id, structured run dirs, manifest/artifact path helpers.
- `backend/runtime/nodes/` - runtime node implementations.
- `backend/checkpointing/replay.py` - replay behavior.
- `backend/evals/` - eval harness, fixtures, metrics, regression.
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
- Keep apply/rollback human-confirmed.
- Keep proposal/eval/optimization artifacts under `runs/`; do not write canonical specs except through apply/restore flows.
- Preserve structured run directory resolution through `backend/runtime/artifacts.py`.
- Treat source runs for approvals as immutable audit records. Decisions create continuation lineage.
- Subgraphs support approval-bearing children (M5.8). Resume is handled via `_subgraph_resume` marker in state; source runs stay immutable.
- Keep UI graph-first. Workbench panels should support the graph, not replace it.

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

Check `FUTURE_SCOPE.md`. The next milestone is **workflow library conventions**: how to discover, organize, and categorize many YAML specs without a second authoring format. Key files to read first:

- `backend/graphspec/loader.py` — `list_workflow_ids`, `load_graph_spec_source`
- `backend/server/routes.py` — `GET /api/workflows`, `GET /api/spec/{workflow}`
- `workflows/` — current flat layout
- `frontend/src/App.tsx` — workflow selector already dynamic
- `FUTURE_SCOPE.md` — accepted scope and rejected directions
