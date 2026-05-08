# Session Handoff - 2026-05-05

Supersedes `docs/session_handoff_2026-04-30-p3.md` for current person_reid evaluation workflow details.

## Scope Completed

### 1) map_cmc-aligned disagreement logic

Refactored the dataset scorer path so filtered-rank logic is reusable:

- Added `map_cmc_filtered_ranked_pairs(...)` in `backend/evals/dataset.py`.
- Added `map_cmc_hit_relevant_within_k(...)` in `backend/evals/dataset.py`.
- Updated `_compute_map_cmc(...)` to use the shared filtered-rank helper.

This guarantees ablation and scorer use the same junk filtering and relevance semantics.

### 2) Description-correct-only disagreement reporting in ablation

Updated `evals/person_reid_market1501/run_ablation.py` to compute and emit:

- K set: `{1, 5, 10, 20}`
- Definition: description hit at K and visual embedding miss at K, on the same map_cmc filtered ranks
- Eligible denominator: only queries with `total_relevant > 0`

Description branch for disagreement uses RRF of:

- semantic ranking, weight `DEFAULT_FUSION_WEIGHTS["description_semantic"]`
- facets ranking, weight `DEFAULT_FUSION_WEIGHTS["description_facets"]`

Visual branch is visual-only cosine ranking.

### 3) CMC@20 headline alignment

Extended ablation scoring from CMC@1/5/10 to CMC@1/5/10/20 and kept `default_3ch` as the headline preset.

### 4) Test coverage

Added `test_map_cmc_helpers_match_compute_map_cmc` to `backend/tests/test_dataset_eval.py` to verify helper hit@k bits match `_compute_map_cmc` CMC bits for K `{1,5,10,20}`.

## Verified Results (1000q / 5000g partition run)

Command run:

```powershell
.\.venv\Scripts\python evals\person_reid_market1501\run_ablation.py --dataset evals\person_reid_market1501\partition_1000q_5000g\dataset.yaml --output runs\evals\person_reid_market1501_eval\ablation_summary.json
```

Headline (`default_3ch`):

- mAP: `0.2148`
- CMC@1: `0.4000`
- CMC@5: `0.5720`
- CMC@10: `0.6360`
- CMC@20: `0.6950`

Description-correct-only counts (eligible queries = 1000):

- @1: `128`
- @5: `147`
- @10: `153`
- @20: `158`

## Additional Analysis Artifacts

Generated deeper cohort analysis file:

- `runs/evals/person_reid_market1501_eval/description_correct_only_analysis.json`

This file includes:

- Representative `description-correct-only @20` samples
- Group-vs-rest distribution summaries for token richness, carried-item and mark fields
- Score distributions (best relevant semantic cosine, facet score, description RRF)
- Rank-gap distributions (first relevant visual vs description)

## Current Description DB Model Defaults

In `evals/person_reid_market1501/build_description_dbs.py`:

- description model: `qwen/qwen3-vl-8b-instruct`
- embedding model: `google/gemini-embedding-2-preview`
- provider: OpenRouter for both text generation and embedding calls

## Validation Run in This Session

```powershell
.\.venv\Scripts\python -m pytest backend\tests -q
```

Result: `262 passed`.

## Suggested Next Session

1. Run a small description-model sweep (2-3 VLM/embedding combos).
2. Compare each candidate on both:
   - fused headline (mAP, CMC@1/5/10/20)
   - disagreement counts (`description-correct-only @K`)
3. Keep baseline split and pipeline fixed for apples-to-apples comparison.
