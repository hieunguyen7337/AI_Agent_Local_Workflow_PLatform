"""Run channel ablations for the person-reID description-scoring pipeline.

For every query in `dataset.yaml` we compute three offline rankings:

  * `visual`  - cosine over precomputed Gemini multimodal image embeddings
  * `semantic` - cosine over precomputed text embeddings of the person-only
                 description
  * `facets`  - region-aware token overlap with contradiction penalty

We then evaluate single-channel and fused configurations under the same
`map_cmc` scorer used by `dataset_eval.yaml`, print headline metrics for the
default three-channel preset (including CMC@20), and aggregate
description-correct / visual-miss disagreement counts @K ∈ {1,5,10,20} using
the same filtered-rank helpers as `_compute_map_cmc`. No LangGraph runtime
calls are made.

Requires ``numpy`` (listed under ``[project.optional-dependencies] dev``).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.repo_root import resolve_dataset_path_str
from backend.evals.dataset import (
    _compute_map_cmc,
    map_cmc_filtered_ranked_pairs,
    map_cmc_hit_relevant_within_k,
)
from backend.tools.reid_specialists import (
    DEFAULT_FACET_PENALTIES,
    DEFAULT_FACET_WEIGHTS,
    DEFAULT_FUSION_WEIGHTS,
    _coerce_dict,
    _facet_pair_score,
    decide_reid_fusion_weights,
)

_RRF_K = 60
_HEADLINE_FUSED = "default_3ch"
_DISAGREEMENT_KS: tuple[int, ...] = (1, 5, 10, 20)
_DEFAULT_DATASET = Path(__file__).parent / "partition_1000q_5000g" / "dataset.yaml"


def _l2_normalize_rows(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return mat / norms


def _load_gallery_embedding_matrix(db_path: Path) -> tuple[list[str], np.ndarray]:
    ids: list[str] = []
    vecs: list[list[float]] = []
    with sqlite3.connect(db_path) as con:
        for image_id, embedding_json in con.execute(
            "SELECT image_id, embedding_json FROM image_embeddings ORDER BY image_id"
        ):
            ids.append(str(image_id))
            vecs.append([float(v) for v in json.loads(embedding_json)])
    mat = np.asarray(vecs, dtype=np.float64)
    return ids, _l2_normalize_rows(mat)


def _load_query_embedding_dict(db_path: Path) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    with sqlite3.connect(db_path) as con:
        for image_id, embedding_json in con.execute(
            "SELECT image_id, embedding_json FROM image_embeddings"
        ):
            vec = np.asarray(json.loads(embedding_json), dtype=np.float64)
            n = float(np.linalg.norm(vec))
            out[str(image_id)] = vec / n if n > 0.0 else vec
    return out


def _load_query_descriptions(db_path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(db_path) as con:
        for image_id, tokens_json in con.execute(
            "SELECT image_id, tokens_json FROM image_descriptions"
        ):
            out[str(image_id)] = _coerce_dict(tokens_json)
    return out


def _load_gallery_description_tokens(db_path: Path) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    with sqlite3.connect(db_path) as con:
        for gallery_id, tokens_json in con.execute(
            "SELECT image_id, tokens_json FROM image_descriptions ORDER BY image_id"
        ):
            rows.append((str(gallery_id), _coerce_dict(tokens_json)))
    return rows


def _rank_by_cosine_matrix(
    query_unit: np.ndarray,
    gallery_ids: list[str],
    gallery_mat: np.ndarray,
    top_k: int,
) -> list[str]:
    scores = gallery_mat @ query_unit
    if top_k >= len(scores):
        order = np.argsort(-scores)
    else:
        partial = np.argpartition(-scores, top_k - 1)[:top_k]
        order = partial[np.argsort(-scores[partial])]
    return [gallery_ids[int(i)] for i in order]


def _rank_by_facets(
    query_tokens: dict[str, Any],
    gallery_tokens: list[tuple[str, dict[str, Any]]],
    weights: dict[str, float],
    penalties: dict[str, float],
) -> list[str]:
    scored = [
        (_facet_pair_score(query_tokens, g_tokens, weights, penalties), gid)
        for gid, g_tokens in gallery_tokens
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [gid for _score, gid in scored]


def _fuse_rankings(rankings: list[tuple[list[str], float]], top_k: int = 0) -> list[str]:
    scores: dict[str, float] = {}
    for ranking, weight in rankings:
        if weight <= 0:
            continue
        for rank, gid in enumerate(ranking, start=1):
            scores[gid] = scores.get(gid, 0.0) + weight / (_RRF_K + rank)
    fused = sorted(scores, key=lambda gid: (-scores[gid], gid))
    return fused[:top_k] if top_k else fused


def _aggregate_metrics(metrics: list[dict[str, Any]]) -> dict[str, float]:
    if not metrics:
        return {}
    keys = sorted(metrics[0].keys())
    return {key: sum(m[key] for m in metrics) / len(metrics) for key in keys}


def run_ablation(
    *,
    dataset_path: Path,
    output_path: Path | None,
    truncate_top_k: int = 200,
) -> dict[str, Any]:
    rows = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError(f"empty dataset: {dataset_path}")

    sample = rows[0]
    query_visual_db = Path(resolve_dataset_path_str(sample["query_embedding_db_path"]))
    gallery_visual_db = Path(resolve_dataset_path_str(sample["gallery_embedding_db_path"]))
    query_description_db = Path(resolve_dataset_path_str(sample["query_description_db_path"]))
    gallery_description_db = Path(resolve_dataset_path_str(sample["gallery_description_db_path"]))

    print("Loading offline DBs ...", flush=True)
    query_visual = _load_query_embedding_dict(query_visual_db)
    gallery_visual_ids, gallery_visual_mat = _load_gallery_embedding_matrix(gallery_visual_db)
    query_desc_emb = _load_query_embedding_dict(query_description_db)
    gallery_desc_ids, gallery_desc_mat = _load_gallery_embedding_matrix(gallery_description_db)
    query_desc_tokens = _load_query_descriptions(query_description_db)
    gallery_desc_tokens = _load_gallery_description_tokens(gallery_description_db)
    print(
        f"  query_visual={len(query_visual)} gallery_visual={gallery_visual_mat.shape[0]}"
        f" query_desc={len(query_desc_emb)} gallery_desc={gallery_desc_mat.shape[0]}",
        flush=True,
    )

    configs: dict[str, dict[str, float]] = {
        "visual_only": {"visual": 1.0, "semantic": 0.0, "facets": 0.0},
        "semantic_only": {"visual": 0.0, "semantic": 1.0, "facets": 0.0},
        "facets_only": {"visual": 0.0, "semantic": 0.0, "facets": 1.0},
        "visual+semantic": {"visual": 0.85, "semantic": 0.15, "facets": 0.0},
        "visual+facets": {"visual": 0.90, "semantic": 0.0, "facets": 0.10},
        "default_3ch": dict(
            visual=DEFAULT_FUSION_WEIGHTS["reid_multimodal_embedding"],
            semantic=DEFAULT_FUSION_WEIGHTS["description_semantic"],
            facets=DEFAULT_FUSION_WEIGHTS["description_facets"],
        ),
        "balanced_3ch": {"visual": 0.70, "semantic": 0.20, "facets": 0.10},
        "desc_heavy_3ch": {"visual": 0.50, "semantic": 0.30, "facets": 0.20},
        "adaptive_router": {"visual": -1.0, "semantic": -1.0, "facets": -1.0},
    }
    metrics_by_config: dict[str, list[dict[str, Any]]] = {name: [] for name in configs}
    adaptive_decisions: dict[str, int] = {"description_heavy": 0, "visual_heavy_default": 0}
    desc_only_counts: dict[int, int] = {k: 0 for k in _DISAGREEMENT_KS}
    eligible_queries = 0

    facet_weights = dict(DEFAULT_FACET_WEIGHTS)
    facet_penalties = dict(DEFAULT_FACET_PENALTIES)

    for index, row in enumerate(rows, start=1):
        query_id = row["query_id"]
        q_visual = query_visual[query_id]
        q_desc_tokens = query_desc_tokens[query_id]
        q_desc_emb = query_desc_emb[query_id]

        visual_ranking = _rank_by_cosine_matrix(
            q_visual, gallery_visual_ids, gallery_visual_mat, truncate_top_k
        )
        semantic_ranking = _rank_by_cosine_matrix(
            q_desc_emb, gallery_desc_ids, gallery_desc_mat, truncate_top_k
        )
        facets_ranking = _rank_by_facets(q_desc_tokens, gallery_desc_tokens, facet_weights, facet_penalties)[
            :truncate_top_k
        ]

        gallery_ids = [str(g) for g in row["gallery_ids"]]
        gallery_pids = [int(p) for p in row["gallery_pids"]]
        gallery_camids = [int(c) for c in row["gallery_camids"]]
        query_pid = int(row["query_pid"])
        query_camid = int(row["query_camid"])

        total_rel, visual_f = map_cmc_filtered_ranked_pairs(
            visual_ranking,
            gallery_ids=gallery_ids,
            gallery_pids=gallery_pids,
            gallery_camids=gallery_camids,
            query_pid=query_pid,
            query_camid=query_camid,
        )
        description_rrf = _fuse_rankings(
            [
                (semantic_ranking, DEFAULT_FUSION_WEIGHTS["description_semantic"]),
                (facets_ranking, DEFAULT_FUSION_WEIGHTS["description_facets"]),
            ],
            top_k=0,
        )
        _, desc_f = map_cmc_filtered_ranked_pairs(
            description_rrf,
            gallery_ids=gallery_ids,
            gallery_pids=gallery_pids,
            gallery_camids=gallery_camids,
            query_pid=query_pid,
            query_camid=query_camid,
        )
        if total_rel > 0:
            eligible_queries += 1
            for k in _DISAGREEMENT_KS:
                v_hit = map_cmc_hit_relevant_within_k(visual_f, query_pid=query_pid, k=k)
                d_hit = map_cmc_hit_relevant_within_k(desc_f, query_pid=query_pid, k=k)
                if d_hit and not v_hit:
                    desc_only_counts[k] += 1

        for name, weights in configs.items():
            if name == "adaptive_router":
                decision = decide_reid_fusion_weights(
                    reid_multimodal_embedding_ranked=visual_ranking,
                    description_semantic_ranked=semantic_ranking,
                    description_facets_ranked=facets_ranking,
                )
                adaptive_decisions[decision["decision"]] = adaptive_decisions.get(decision["decision"], 0) + 1
                weights = {
                    "visual": float(decision["rrf_weights"]["reid_multimodal_embedding"]),
                    "semantic": float(decision["rrf_weights"]["description_semantic"]),
                    "facets": float(decision["rrf_weights"]["description_facets"]),
                }
            fused = _fuse_rankings(
                [
                    (visual_ranking, weights["visual"]),
                    (semantic_ranking, weights["semantic"]),
                    (facets_ranking, weights["facets"]),
                ],
                top_k=20,
            )
            scored = _compute_map_cmc(
                fused,
                gallery_ids=gallery_ids,
                gallery_pids=gallery_pids,
                gallery_camids=gallery_camids,
                query_pid=query_pid,
                query_camid=query_camid,
                cmc_ks=[1, 5, 10, 20],
            )
            metrics_by_config[name].append(scored)

        if index % 100 == 0 or index == len(rows):
            print(f"  scored {index}/{len(rows)} queries", flush=True)

    summary: dict[str, dict[str, float]] = {}
    for name, metrics in metrics_by_config.items():
        agg = _aggregate_metrics(metrics)
        summary[name] = {
            "mAP": agg.get("ap", 0.0),
            "CMC@1": agg.get("cmc_1", 0.0),
            "CMC@5": agg.get("cmc_5", 0.0),
            "CMC@10": agg.get("cmc_10", 0.0),
            "CMC@20": agg.get("cmc_20", 0.0),
        }

    headline = summary.get(_HEADLINE_FUSED, {})
    desc_only_payload = {
        "definition": (
            "Per query (gallery with >=1 cross-camera same-pid relevant): "
            "description hits @K on RRF(semantic, facets) with weights from DEFAULT_FUSION_WEIGHTS; "
            "visual miss @K on visual-only cosine ranking. Same map_cmc junk filter for both."
        ),
        "eligible_queries": eligible_queries,
        "counts_description_correct_embedding_incorrect": {str(k): desc_only_counts[k] for k in _DISAGREEMENT_KS},
        "rates_description_correct_embedding_incorrect": (
            {str(k): (desc_only_counts[k] / eligible_queries if eligible_queries else 0.0) for k in _DISAGREEMENT_KS}
        ),
    }

    header = f"{'config':<20} {'mAP':>8} {'CMC@1':>8} {'CMC@5':>8} {'CMC@10':>8} {'CMC@20':>8}"
    print()
    print(f"=== Headline fused preset: {_HEADLINE_FUSED} ===")
    if headline:
        print(
            f"  mAP={headline['mAP']:.4f}  "
            f"CMC@1={headline['CMC@1']:.4f}  CMC@5={headline['CMC@5']:.4f}  "
            f"CMC@10={headline['CMC@10']:.4f}  CMC@20={headline['CMC@20']:.4f}"
        )
    print()
    print("=== Description correct, visual embedding incorrect (@K on filtered ranks) ===")
    print(f"  Eligible queries (total_relevant>0): {eligible_queries}")
    for k in _DISAGREEMENT_KS:
        n = desc_only_counts[k]
        rate = (n / eligible_queries) if eligible_queries else 0.0
        print(f"  @{k:<2} count={n:4d}  rate={rate:.4f}")

    print()
    print(header)
    print("-" * len(header))
    for name, vals in summary.items():
        print(
            f"{name:<20} "
            f"{vals['mAP']:>8.4f} {vals['CMC@1']:>8.4f} {vals['CMC@5']:>8.4f} "
            f"{vals['CMC@10']:>8.4f} {vals['CMC@20']:>8.4f}"
        )

    payload: dict[str, Any] = {
        "summary": summary,
        "configs": configs,
        "adaptive_decisions": adaptive_decisions,
        "headline_fused_preset": _HEADLINE_FUSED,
        "description_embedding_disagreement": desc_only_payload,
    }

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nwrote summary to {output_path}")

    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Channel ablation for person-reID description scoring")
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--truncate-top-k",
        type=int,
        default=200,
        help="Truncate per-channel rankings to this depth before fusion (default 200).",
    )
    args = parser.parse_args()
    run_ablation(
        dataset_path=args.dataset,
        output_path=args.output,
        truncate_top_k=args.truncate_top_k,
    )


if __name__ == "__main__":
    main()
