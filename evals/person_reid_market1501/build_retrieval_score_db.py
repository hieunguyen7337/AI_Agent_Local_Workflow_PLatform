"""Precompute top-k retrieval rankings for the person-ReID eval workflow."""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.tools.reid_specialists import DEFAULT_FACET_PENALTIES, DEFAULT_FACET_WEIGHTS, _facet_pair_score
from evals.person_reid_market1501.run_ablation import (
    _load_gallery_description_tokens,
    _load_gallery_embedding_matrix,
    _load_query_descriptions,
    _load_query_embedding_dict,
    _rank_by_cosine_matrix,
    _rank_by_facets,
)

_DEFAULT_DATASET = Path(__file__).parent / "partition_1000q_5000g" / "dataset_google_gemma_4_31b_it.yaml"
_DEFAULT_OUTPUT = Path(__file__).parent / "retrieval_scores_google_gemma_4_31b_it.sqlite"


def build_retrieval_score_db(*, dataset_path: Path, output: Path, top_k: int = 200) -> Path:
    rows = yaml.safe_load(dataset_path.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError(f"empty dataset: {dataset_path}")

    sample = rows[0]
    query_visual_db = Path(sample["query_embedding_db_path"])
    gallery_visual_db = Path(sample["gallery_embedding_db_path"])
    query_description_db = Path(sample["query_description_db_path"])
    gallery_description_db = Path(sample["gallery_description_db_path"])

    print("Loading offline DBs ...", flush=True)
    query_visual = _load_query_embedding_dict(query_visual_db)
    gallery_visual_ids, gallery_visual_mat = _load_gallery_embedding_matrix(gallery_visual_db)
    query_desc_emb = _load_query_embedding_dict(query_description_db)
    gallery_desc_ids, gallery_desc_mat = _load_gallery_embedding_matrix(gallery_description_db)
    query_desc_tokens = _load_query_descriptions(query_description_db)
    gallery_desc_tokens = _load_gallery_description_tokens(gallery_description_db)

    facet_weights = dict(DEFAULT_FACET_WEIGHTS)
    facet_penalties = dict(DEFAULT_FACET_PENALTIES)

    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as con:
        con.execute("DROP TABLE IF EXISTS retrieval_rankings")
        con.execute(
            """
            CREATE TABLE retrieval_rankings (
                query_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                rank INTEGER NOT NULL,
                gallery_id TEXT NOT NULL,
                score REAL NOT NULL,
                PRIMARY KEY (query_id, channel, rank)
            )
            """
        )
        con.execute("CREATE INDEX idx_retrieval_rankings_lookup ON retrieval_rankings(query_id, channel, rank)")

        insert_rows: list[tuple[str, str, int, str, float]] = []
        for index, row in enumerate(rows, start=1):
            query_id = str(row["query_id"])

            visual_scores = gallery_visual_mat @ query_visual[query_id]
            visual_order = visual_scores.argsort()[::-1][:top_k]
            for rank, gallery_index in enumerate(visual_order, start=1):
                insert_rows.append(
                    (
                        query_id,
                        "visual_image_embedding",
                        rank,
                        gallery_visual_ids[int(gallery_index)],
                        float(visual_scores[int(gallery_index)]),
                    )
                )

            semantic_scores = gallery_desc_mat @ query_desc_emb[query_id]
            semantic_order = semantic_scores.argsort()[::-1][:top_k]
            for rank, gallery_index in enumerate(semantic_order, start=1):
                insert_rows.append(
                    (
                        query_id,
                        "description_semantic_text",
                        rank,
                        gallery_desc_ids[int(gallery_index)],
                        float(semantic_scores[int(gallery_index)]),
                    )
                )

            facets_scored = [
                (_facet_pair_score(query_desc_tokens[query_id], gallery_tokens, facet_weights, facet_penalties), gallery_id)
                for gallery_id, gallery_tokens in gallery_desc_tokens
            ]
            facets_scored.sort(key=lambda item: (-item[0], item[1]))
            for rank, (score, gallery_id) in enumerate(facets_scored[:top_k], start=1):
                insert_rows.append((query_id, "description_structured_facets", rank, gallery_id, float(score)))

            if len(insert_rows) >= 30_000:
                con.executemany("INSERT INTO retrieval_rankings VALUES (?, ?, ?, ?, ?)", insert_rows)
                con.commit()
                insert_rows.clear()
            if index % 100 == 0 or index == len(rows):
                print(f"  precomputed {index}/{len(rows)} queries", flush=True)

        if insert_rows:
            con.executemany("INSERT INTO retrieval_rankings VALUES (?, ?, ?, ?, ?)", insert_rows)
        con.execute(
            """
            CREATE VIEW retrieval_rankings_json AS
            SELECT query_id, channel,
                   json_group_array(gallery_id) AS ranked_gallery_ids_json
            FROM (
                SELECT query_id, channel, gallery_id
                FROM retrieval_rankings
                ORDER BY query_id, channel, rank
            )
            GROUP BY query_id, channel
            """
        )
        con.commit()

    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Precompute top-k retrieval score DB")
    parser.add_argument("--dataset", type=Path, default=_DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--top-k", type=int, default=200)
    args = parser.parse_args()
    output = build_retrieval_score_db(dataset_path=args.dataset, output=args.output, top_k=args.top_k)
    print(f"Wrote retrieval score DB to {output}")


if __name__ == "__main__":
    main()
