# CLAUDE.md

Project instructions for Claude instances working in this repository.

## Mission

Build and maintain a local-first AI workflow platform where YAML `GraphSpec` files are the canonical source of truth. The product loop is:

1. Human/LLM reads a simple YAML workflow spec.
2. Backend validates it with Pydantic `GraphSpec`.
3. Runtime compiles it through `GraphMetadata` into LangGraph.
4. Frontend renders the same spec as a graph with full node metadata.
5. LLM proposes YAML mutations.
6. Human reviews diff, graph, eval evidence, and artifacts.
7. Human explicitly applies or rolls back YAML changes.

Keep this loop coherent. Do not introduce a second authoring surface.

## Read First

Read these files before broad exploration:

- `AGENTS.md` - concise implementation map and workflow for all coding agents.
- `FUTURE_SCOPE.md` - current baseline, next milestone, rejected directions.
- `README.md` - user-facing setup and capabilities.
- `docs/ui_vision_audit.md` - UI smoke checklist.
- `docs/run_artifacts.md` - how run files should be understood.

Do not spend tokens re-reading `claude_full_plan.md` unless investigating M1 history. It is old context, not the current source of truth.

## Current Architecture

- Specs: `workflows/*.yaml`
- Spec models/loader: `backend/graphspec/`
- Internal compiler helper: `backend/builder/`
- Runtime: `backend/runtime/`
- CLI: `backend/cli/main.py`
- API: `backend/server/`
- Evals: `backend/evals/` and `evals/*/fixtures.yaml`
- Frontend: `frontend/src/`
- Agent-facing docs: `CLAUDE.md`, `AGENTS.md`

The Python builder is an internal metadata/compiler helper only. It is not a workflow authoring surface.

## Work Style

- Start from the smallest set of files that can answer the task. Use `AGENTS.md` as the map.
- Prefer `rg`/targeted file reads over broad directory scans.
- Preserve local-first behavior: no hosted tracing, no cloud persistence, no hidden remote state.
- Preserve YAML-only workflow loading. Do not restore Python workflow module fallback.
- Keep mutation/apply flows human-reviewed. Do not auto-apply LLM suggestions.
- Use existing patterns before adding abstractions.
- Keep UI tool-first and graph-first. No landing pages or marketing screens.
- Update `FUTURE_SCOPE.md` when a milestone graduates or a new deferred item is created.
- Update `README.md` only for user-facing behavior, setup, or public interfaces.

## Implementation Rules

- Make focused changes. Avoid unrelated refactors.
- Add or update tests for backend behavior changes.
- For frontend changes, run the TypeScript build.
- If a change affects docs, keep docs concise and aligned with the YAML source-of-truth vision.
- Do not edit generated/runtime artifacts under `runs/` unless the task is explicitly about local artifacts.
- Do not change canonical workflow YAML as part of tests unless the task is specifically about source-of-truth changes.

## Verification

Default verification:

```powershell
.\.venv\Scripts\python -m pytest backend\tests -q
cd frontend
npm run build
```

Local app smoke:

```powershell
.\.venv\Scripts\python -m backend.cli.main serve --host 127.0.0.1 --port 8000
cd frontend
npm run dev
```

Open `http://127.0.0.1:5173`.

## Next Milestone

Use `FUTURE_SCOPE.md` as the authority. The current shipped baseline (M5.10) includes nested approval subgraphs, approval subgraph eval coverage, and a fully dynamic workflow selector driven by `GET /api/workflows`. The next milestone is workflow library conventions: organizing many YAML specs, examples, and pipeline templates so they remain discoverable without introducing a second authoring format.

Do not start any new milestone by guessing. Read `FUTURE_SCOPE.md` and the relevant runtime/spec/frontend paths before designing.

## Claude-Specific Completion Standard

When implementing a task:

1. State the scoped plan briefly.
2. Inspect only the files needed for the task.
3. Implement end-to-end, including tests/docs when relevant.
4. Run the appropriate verification commands.
5. Report only high-signal results: changed areas, tests run, failures or limitations.

Avoid long exploratory summaries. If the repo map in `AGENTS.md` already answers where something lives, use it instead of rediscovering the tree.
