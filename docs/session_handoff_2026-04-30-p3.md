# Session Handoff — 2026-04-30 (Part 3)

Supersedes `docs/session_handoff_2026-04-30-p2.md` for person_reid context. Read Part 2 for `python_tool` node kind, eval adapter, and map_cmc scorer background.

## What Was Done This Session

### 1. Diagnosed why eval scored 1% (all near-zero metrics)

Two root causes:

- **Type mismatch (scorer)**: `final_ranker` output key `ranked_gallery_pids` stored a raw LLM string. The `map_cmc` scorer's lookup is keyed by gallery filenames. When the LLM output was a JSON string like `'["0006_c3s3.jpg"]'`, the scorer received a string and either iterated characters or failed the lookup → all metrics 0.
- **WorkflowState missing new keys** (found in second eval run): LangGraph silently drops node output updates for keys not declared in `WorkflowState`. New keys must be added to `backend/runtime/state.py`.

### 2. Pipeline restructuring — new nodes

Added two `python_tool` nodes to both `person_reid_market1501_eval.yaml` and `person_reid_market1501.yaml`:

**`rrf_precompute`** (before `final_ranker`):
- Inputs: `visual_ranked`, `attribute_ranked`, `text_ranked`, `body_shape_ranked`
- Computes weighted RRF: attribute weight=10, stubs weight=1
- Output: `rrf_merged_ranking: list[str]` (top-20 pre-merged)
- Reduces LLM cognitive load; provides deterministic fallback

**`parse_final_ranking`** (after `final_ranker`):
- Inputs: `ranked_gallery_pids_raw` (raw LLM string), `rrf_merged_ranking` (fallback)
- Tries `json.loads()`, then regex `_JSON_ARRAY_RE`, then falls back to RRF list
- Output: `ranked_gallery_ids: list[str]` — always a clean typed list

Updated `final_ranker`:
- `output_state_key`: `ranked_gallery_pids` → `ranked_gallery_pids_raw` (signals unprocessed)
- `max_tokens`: 500 → 800 (20 filenames ~600 tokens)
- Prompt: simplified to receive `{rrf_merged_ranking}` (one list, not four), `{query_attributes}` replaces `{dispatch_brief}`

Scorer updated: `dataset_eval.yaml` `actual` changed to `final_state.ranked_gallery_ids`.

### 3. WorkflowState additions (`backend/runtime/state.py`)

```python
rrf_merged_ranking: list[str]
ranked_gallery_pids_raw: str
ranked_gallery_ids: list[str]
```

**Critical**: any new `output_state_key` in a YAML node must be declared here or LangGraph will silently discard the update.

### 4. Improved attribute scoring

`attribute_gallery_retriever` in `backend/tools/reid_specialists.py`:

- **Before**: exact string match on all 4 fields — `"white shirt, gray shorts, backpack"` ≠ `"white shirt, gray shorts"` → score 0
- **After**:
  - `gender`, `age_group`: exact match (reliable short categoricals, weight 1.0 each)
  - `color`, `clothing`: Jaccard token-overlap — split on `, ` and ` `, compute `len(A∩B)/len(A∪B)` (weight 0.0–1.0 each)
- Net effect: compound clothing/color descriptions now get partial credit

### 5. Eval results progression

| Run | mAP | CMC@1 | CMC@5 | CMC@10 | Notes |
|---|---|---|---|---|---|
| Original (before fixes) | ~0% | 1% | 1% | 1% | All parse failures; 1 lucky match |
| After WorkflowState fix | 10.8% | 10% | 11% | 15% | Pipeline working; exact-match attrs; equal RRF weights |
| After Jaccard + weighted RRF + attr prompt | **34.5%** | **35%** | **35%** | **35%** | Current baseline |

The flat CMC@1=CMC@5=CMC@10=35% means every query the pipeline gets right is ranked at position 1 — the LLM final ranker consolidates signal to the top when given explicit attribute context.

## Current State

- **219 backend tests pass**
- **Frontend build clean** (no TypeScript changes this session)
- Both `person_reid_market1501.yaml` and `person_reid_market1501_eval.yaml` updated identically for the pipeline changes
- `python_tools.yaml` has `rrf_precompute` and `parse_final_ranking` registered

## Important Implementation Notes

1. **WorkflowState is required** — adding a new node that writes a new key requires declaring that key in `backend/runtime/state.py`. Forgetting this produces no error but the key never appears in state downstream.

2. **RRF weights** — attribute weight is 10 because it's the only real (non-stub) specialist. When real visual/text/body_shape specialists are added, weights should be re-evaluated and ideally learned from a validation split.

3. **parse_final_ranking fallback** — if the LLM returns malformed JSON, the function falls back to `rrf_merged_ranking`. This means scores are always non-zero (the RRF list is deterministic and real). Never remove this fallback.

4. **Scorer key chain**: YAML node `output_state_key: ranked_gallery_ids` → `dataset_eval.yaml actual: final_state.ranked_gallery_ids` → `_compute_map_cmc` in `backend/evals/dataset.py`. All three must be consistent.

## Next Milestone

**Real specialist model wrappers**: replace placeholder visual/text/body_shape specialists with TransReID/ViT, CLIP/SigLIP, HMR2.0/SMPLify. Current attribute-only baseline is mAP=34.5% — real visual specialists should push this significantly higher. Prerequisites: GPU/weight loading strategy, real gallery embedding index, multimodal image path flow.

Read `FUTURE_SCOPE.md` before designing.
