# Session Handoff — 2026-04-30 (Part 2)

Supersedes `docs/session_handoff_2026-04-30.md`. Read that file for earlier context on workflow-function, dataset-eval, concurrency, and telemetry; this document captures the two milestones shipped on top of it.

## Current Implemented Baseline

Everything from Part 1 plus:

- **`python_tool` node kind** — YAML workflows can declare `kind: python_tool` nodes that call approved local Python functions. Callables must be listed in `python_tools.yaml` (repo root); validated at spec parse time and again at execution time (defense-in-depth). `inputs: {kwarg: state_key}` maps state values to function kwargs; `output_state_key` captures the return value. stdout/stderr are captured into OTEL span attributes (truncated to 4 KB). Exceptions propagate as run failures with ERROR spans.
- **`python_tools.yaml`** — allowlist file at repo root. Default empty = all python_tool nodes fail validation (secure default). Currently permits the three reid specialist stubs.
- **Person-reID Market-1501 concept demo** — `workflows/person_reid_market1501.yaml` is a boss (gemma-4-31b-it) → fan-out to 4 specialists → LLM final ranker pipeline. Visual, text, and body-shape specialists are `python_tool` nodes calling deterministic placeholder functions in `backend/tools/reid_specialists.py`. Attribute specialist is an LLM (qwen3.5-9b). Final ranker is an LLM (gemma-4-31b-it). Edges unchanged from fan-out pattern.
- **Market-1501 dataset eval adapter** — `evals/person_reid_market1501/build_dataset.py` generates a 100-query × 500-gallery `dataset.yaml` from the official Market-1501 directory. `dataset_eval.yaml` wires all fields. `dataset.yaml` is gitignored.
- **`map_cmc` retrieval scorer** — new scorer type in `backend/evals/dataset.py`. Implements Market-1501 junk filtering (same-cam-same-pid, pid=-1/0), per-row Average Precision, and CMC@k indicators. Returns `{ap, cmc_1, cmc_5, cmc_10}` per row; aggregates to mAP + CMC@1/5/10 across the dataset.
- **Prices** — `google/gemma-4-31b-it` and `qwen/qwen3.5-9b` added to `prices.yaml`.
- **`python_tool` frontend rendering** — teal node color (`#f0fdfa` / `#5eead4` border) distinguishes python_tool nodes in GraphView; metadata panel shows abbreviated function name, input kwarg names, and output_state_key.
- **Test counts** — 206 backend tests pass (was 187 before this session). Frontend build clean.

## Important Recent Code Changes

- `backend/builder/nodes.py` — `PythonToolNodeConfig` added (callable_path, inputs, output_state_key, allowlist validator).
- `backend/graphspec/models.py` — `PythonToolNodeConfig` added to `GraphNodeSpec` discriminator union.
- `backend/builder/python_tool_allowlist.py` — new; `load_allowlist() -> frozenset[str]`, cached, `_clear_cache()` test helper.
- `backend/runtime/nodes/python_tool.py` — new; `make_python_tool_node(...)`, importlib resolution, stdout/stderr capture, OTEL span.
- `backend/runtime/executor.py` — added `elif isinstance(cfg, PythonToolNodeConfig)` dispatch branch.
- `backend/tools/__init__.py` + `backend/tools/reid_specialists.py` — new; three placeholder specialists (deterministic hash-seeded shuffle).
- `backend/evals/dataset.py` — added `map_cmc` scorer branch + `_parse_ranked_ids` + `_compute_map_cmc` helpers.
- `workflows/person_reid_market1501.yaml` — visual/text/body_shape swapped from `kind: llm` → `kind: python_tool`.
- `frontend/src/types.ts` — `"python_tool"` added to `NodeKind` union.
- `frontend/src/components/GraphView.tsx` — teal styling + `metadataLines()` branch for python_tool.
- `evals/person_reid_market1501/dataset_eval.yaml` — new.
- `evals/person_reid_market1501/build_dataset.py` — new.
- `python_tools.yaml` — new (repo root).
- `prices.yaml` — added gemma-4-31b-it and qwen3.5-9b OpenRouter entries.
- `docs/python_tools.md` — new; how to register callables and author python_tool YAML.
- `backend/tests/test_python_tool_node.py` — new; 19 tests.
- `backend/tests/test_dataset_eval.py` — 6 new tests for map_cmc scorer.

## Recent Test and Smoke Results

```
.venv/Scripts/python -m pytest backend/tests -q
206 passed
```

```
cd frontend && npm run build
✓ built in 4.09s
```

Market-1501 dataset **not yet downloaded** on this machine. `build_dataset.py` and the dataset eval have not been run end-to-end. Once Market-1501 is available:

```powershell
# 1. Generate dataset
.venv/Scripts/python evals/person_reid_market1501/build_dataset.py `
  --market1501-root <path> --n-queries 100 --gallery-size 500 --seed 42

# 2. Run eval (requires OPENROUTER_API_KEY)
.venv/Scripts/python -m backend.cli.main eval person_reid_market1501 --n 100
```

Expected: mAP and CMC numbers will be near-random because specialists are stubs. Eval should complete without errors and produce per-row `{ap, cmc_1, cmc_5, cmc_10}` in the artifact JSON.

## Current Dirty Worktree Notes

Modified files span docs, runtime, eval, telemetry, server, CLI, frontend types/App from the earlier session (see Part 1 handoff). New additions from this session:

- New: `python_tools.yaml`, `backend/builder/python_tool_allowlist.py`, `backend/runtime/nodes/python_tool.py`, `backend/tools/__init__.py`, `backend/tools/reid_specialists.py`, `evals/person_reid_market1501/dataset_eval.yaml`, `evals/person_reid_market1501/build_dataset.py`, `docs/python_tools.md`, `backend/tests/test_python_tool_node.py`
- Modified: `backend/builder/nodes.py`, `backend/graphspec/models.py`, `backend/runtime/executor.py`, `backend/evals/dataset.py`, `backend/tests/test_dataset_eval.py`, `workflows/person_reid_market1501.yaml`, `frontend/src/types.ts`, `frontend/src/components/GraphView.tsx`, `prices.yaml`, `.gitignore`, `AGENTS.md`, `FUTURE_SCOPE.md`
- Gitignored: `evals/person_reid_market1501/dataset.yaml` (generated; requires local Market-1501 data)

Do not revert any of these — they are intentional milestone deliverables.

## Design Decisions to Preserve

- `python_tools.yaml` is the allowlist boundary. Functions still run in-process; the YAML file is the human-reviewable approval gate.
- Default empty allowlist → all python_tool nodes fail at spec validation time. This is intentional (secure default).
- Allowlist is checked twice: once at spec validation (field_validator) and once at execution time in `_resolve_callable`. Do not collapse these into one.
- `output_state_key` is a single key (not a dict mapping). The function's return value is stored wholesale. Multi-output is deferred.
- `hot-reload` of `python_tools.yaml` is NOT implemented. Loaded once, cached at first use. Restart required to pick up changes.
- Placeholder specialists produce deterministic distinct orderings (different seed offsets per specialist). Do not make them identical.
- `map_cmc` AP computation uses `total_relevant` from the full gallery (not just ranked subset) as the denominator. Do not change this without re-reading the Market-1501 eval protocol.
- `gallery_ids` is passed through workflow state as a Python list. LLM `format_map` renders it as Python repr; that is acceptable for the demo.

## Likely Next Milestone

**Real specialist model wrappers for person reID** — replace the three placeholder `python_tool` specialists with real implementations:
- `visual_specialist`: TransReID or ViT backbone feature extraction + cosine similarity ranking
- `text_specialist`: CLIP or SigLIP text-image similarity
- `body_shape_specialist`: HMR2.0 or SMPLify 3D shape parameter extraction

Prerequisites before designing:
- Multimodal image input: query image bytes/path must flow through workflow state and into vision-capable LLM calls (boss + attribute need to see the actual image, not just the filename)
- Heavyweight model dependency strategy: GPU loading, weights storage, import boundary within `python_tools.yaml`
- Real candidate gallery: embedding index over actual bounding_box_test images, replacing the shared-500 precomputed list

Read `FUTURE_SCOPE.md` and `docs/python_tools.md` before designing. Do not begin by guessing.
