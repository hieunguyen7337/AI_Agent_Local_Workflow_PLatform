"""Build AI-Agent-platform precomputed retrieval DBs from ReID embeddings.

The person-ReID eval workflow consumes a SQLite `retrieval_rankings` table with
top-k gallery IDs already scored and sorted per query/channel. This builder
adapts Torchreid/FastReID embedding DBs into that exact format and can copy the
retained description channels from the existing precompute DB.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.repo_root import resolve_dataset_path_str

_DEFAULT_OUTPUT = (
    Path(__file__).parent
    / "precomputed_retrieval"
    / "market1501_1000q_5000g_retrieval_rankings_all_paths_v1.sqlite"
)
_DEFAULT_DESCRIPTION_SOURCE = Path(__file__).parent / "retrieval_scores_google_gemma_4_31b_it.sqlite"

VISUAL_TORCHREID_CHANNEL = "visual_torchreid_osnet_ain_x1_0_msmt17"
VISUAL_FASTREID_CHANNEL = "visual_fastreid_sbs_r101_ibn_market1501"
DESCRIPTION_CHANNEL_MAP = {
    "description_semantic_text": "description_semantic_text_gemini_embedding_2_preview",
    "description_structured_facets": "description_structured_facets_gemma_4_31b_it",
}


def _resolve_path(path: Path | str | None) -> Path | None:
    if path is None:
        return None
    raw = str(path)
    if not raw:
        return None
    return Path(resolve_dataset_path_str(raw))


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vec))
    if norm == 0.0:
        return [float(value) for value in vec]
    return [float(value) / norm for value in vec]


def _load_embedding_db(db_path: Path) -> tuple[list[str], list[list[float]], dict[str, dict[str, Any]]]:
    ids: list[str] = []
    vectors: list[list[float]] = []
    metadata: dict[str, dict[str, Any]] = {}
    with sqlite3.connect(db_path) as con:
        for image_id, image_path, pid, camid, embedding_json in con.execute(
            """
            SELECT image_id, image_path, pid, camid, embedding_json
            FROM image_embeddings
            ORDER BY image_id
            """
        ):
            image_id_s = str(image_id)
            ids.append(image_id_s)
            vectors.append(_l2_normalize([float(value) for value in json.loads(embedding_json)]))
            metadata[image_id_s] = {
                "image_path": str(image_path),
                "pid": int(pid),
                "camid": int(camid),
            }
    if not ids:
        raise ValueError(f"embedding DB is empty: {db_path}")
    return ids, vectors, metadata


def _rank_with_numpy(
    query_vector: list[float],
    gallery_ids: list[str],
    gallery_vectors: list[list[float]],
    top_k: int,
) -> list[tuple[int, str, float]] | None:
    try:
        import numpy as np
    except ImportError:
        return None

    q = np.asarray(query_vector, dtype=np.float64)
    gallery = np.asarray(gallery_vectors, dtype=np.float64)
    scores = gallery @ q
    limit = min(int(top_k), len(gallery_ids))
    if limit >= len(gallery_ids):
        order = np.argsort(-scores)
    else:
        partial = np.argpartition(-scores, limit - 1)[:limit]
        order = partial[np.argsort(-scores[partial])]
    return [(rank, gallery_ids[int(index)], float(scores[int(index)])) for rank, index in enumerate(order, start=1)]


def _rank_with_python(
    query_vector: list[float],
    gallery_ids: list[str],
    gallery_vectors: list[list[float]],
    top_k: int,
) -> list[tuple[int, str, float]]:
    scored = []
    for gallery_id, gallery_vector in zip(gallery_ids, gallery_vectors):
        score = sum(q * g for q, g in zip(query_vector, gallery_vector))
        scored.append((float(score), gallery_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(rank, gallery_id, score) for rank, (score, gallery_id) in enumerate(scored[: int(top_k)], start=1)]


def _rank_query(
    query_vector: list[float],
    gallery_ids: list[str],
    gallery_vectors: list[list[float]],
    top_k: int,
) -> list[tuple[int, str, float]]:
    return _rank_with_numpy(query_vector, gallery_ids, gallery_vectors, top_k) or _rank_with_python(
        query_vector, gallery_ids, gallery_vectors, top_k
    )


def _create_schema(con: sqlite3.Connection) -> None:
    con.execute("DROP VIEW IF EXISTS retrieval_rankings_json")
    con.execute("DROP TABLE IF EXISTS retrieval_rankings")
    con.execute("DROP TABLE IF EXISTS retrieval_db_metadata")
    con.execute("DROP TABLE IF EXISTS retrieval_channels")
    con.execute("DROP TABLE IF EXISTS queries")
    con.execute("DROP TABLE IF EXISTS gallery")
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
    con.execute(
        """
        CREATE TABLE retrieval_db_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE retrieval_channels (
            channel TEXT PRIMARY KEY,
            path_key TEXT NOT NULL,
            modality TEXT NOT NULL,
            display_name TEXT NOT NULL,
            score_type TEXT NOT NULL,
            higher_is_better INTEGER NOT NULL DEFAULT 1,
            top_k INTEGER NOT NULL,
            source_kind TEXT NOT NULL,
            source_repo TEXT,
            model_name TEXT,
            checkpoint_path TEXT,
            config_path TEXT,
            embedding_dim INTEGER,
            normalized INTEGER,
            precompute_artifacts_json TEXT,
            notes TEXT
        )
        """
    )
    con.execute(
        """
        CREATE TABLE queries (
            query_id TEXT PRIMARY KEY,
            image_path TEXT,
            pid INTEGER,
            camid INTEGER
        )
        """
    )
    con.execute(
        """
        CREATE TABLE gallery (
            gallery_id TEXT PRIMARY KEY,
            image_path TEXT,
            pid INTEGER,
            camid INTEGER
        )
        """
    )


def _create_json_view(con: sqlite3.Connection) -> None:
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


def _insert_metadata(con: sqlite3.Connection, *, top_k: int, notes: str) -> None:
    rows = {
        "schema_version": "person_reid_precomputed_retrieval/v1",
        "dataset": "market1501",
        "partition": "1000q_5000g",
        "workflow_family": "person_reid_market1501",
        "top_k": str(int(top_k)),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "builder": "evals/person_reid_market1501/build_reid_retrieval_score_db.py",
        "notes": notes,
    }
    con.executemany("INSERT INTO retrieval_db_metadata (key, value) VALUES (?, ?)", rows.items())


def _insert_channel(
    con: sqlite3.Connection,
    *,
    channel: str,
    path_key: str,
    modality: str,
    display_name: str,
    score_type: str,
    top_k: int,
    source_kind: str,
    source_repo: str | None = None,
    model_name: str | None = None,
    checkpoint_path: str | None = None,
    config_path: str | None = None,
    embedding_dim: int | None = None,
    normalized: bool | None = None,
    artifacts: dict[str, Any] | None = None,
    notes: str | None = None,
) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO retrieval_channels (
            channel, path_key, modality, display_name, score_type, higher_is_better,
            top_k, source_kind, source_repo, model_name, checkpoint_path, config_path,
            embedding_dim, normalized, precompute_artifacts_json, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            channel,
            path_key,
            modality,
            display_name,
            score_type,
            1,
            int(top_k),
            source_kind,
            source_repo,
            model_name,
            checkpoint_path,
            config_path,
            embedding_dim,
            None if normalized is None else int(bool(normalized)),
            json.dumps(artifacts or {}, sort_keys=True),
            notes,
        ),
    )


def _insert_manifest_rows(
    con: sqlite3.Connection,
    *,
    query_meta: dict[str, dict[str, Any]] | None,
    gallery_meta: dict[str, dict[str, Any]] | None,
) -> None:
    if query_meta:
        con.executemany(
            "INSERT OR IGNORE INTO queries (query_id, image_path, pid, camid) VALUES (?, ?, ?, ?)",
            [
                (query_id, meta["image_path"], int(meta["pid"]), int(meta["camid"]))
                for query_id, meta in sorted(query_meta.items())
            ],
        )
    if gallery_meta:
        con.executemany(
            "INSERT OR IGNORE INTO gallery (gallery_id, image_path, pid, camid) VALUES (?, ?, ?, ?)",
            [
                (gallery_id, meta["image_path"], int(meta["pid"]), int(meta["camid"]))
                for gallery_id, meta in sorted(gallery_meta.items())
            ],
        )


def _insert_visual_rankings(
    con: sqlite3.Connection,
    *,
    channel: str,
    query_db: Path,
    gallery_db: Path,
    top_k: int,
) -> tuple[int, int, int]:
    query_ids, query_vectors, query_meta = _load_embedding_db(query_db)
    gallery_ids, gallery_vectors, gallery_meta = _load_embedding_db(gallery_db)
    _insert_manifest_rows(con, query_meta=query_meta, gallery_meta=gallery_meta)

    insert_rows: list[tuple[str, str, int, str, float]] = []
    for index, (query_id, query_vector) in enumerate(zip(query_ids, query_vectors), start=1):
        for rank, gallery_id, score in _rank_query(query_vector, gallery_ids, gallery_vectors, top_k):
            insert_rows.append((query_id, channel, rank, gallery_id, score))
        if len(insert_rows) >= 50_000:
            con.executemany("INSERT INTO retrieval_rankings VALUES (?, ?, ?, ?, ?)", insert_rows)
            insert_rows.clear()
        if index % 100 == 0 or index == len(query_ids):
            print(f"  {channel}: ranked {index}/{len(query_ids)} queries", flush=True)
    if insert_rows:
        con.executemany("INSERT INTO retrieval_rankings VALUES (?, ?, ?, ?, ?)", insert_rows)
    return len(query_ids), len(gallery_ids), len(query_vectors[0])


def _copy_description_channels(
    con: sqlite3.Connection,
    *,
    source_db: Path,
    channel_map: dict[str, str],
    top_k: int,
) -> None:
    if not source_db.is_file():
        raise FileNotFoundError(f"description source DB not found: {source_db}")
    copied_channels: set[str] = set()
    insert_rows: list[tuple[str, str, int, str, float]] = []
    with sqlite3.connect(source_db) as src:
        for old_channel, new_channel in channel_map.items():
            rows = src.execute(
                """
                SELECT query_id, rank, gallery_id, score
                FROM retrieval_rankings
                WHERE channel = ? AND rank <= ?
                ORDER BY query_id, rank
                """,
                (old_channel, int(top_k)),
            ).fetchall()
            if not rows:
                raise ValueError(f"channel {old_channel!r} not found in {source_db}")
            copied_channels.add(new_channel)
            insert_rows.extend((str(qid), new_channel, int(rank), str(gid), float(score)) for qid, rank, gid, score in rows)
            if len(insert_rows) >= 50_000:
                con.executemany("INSERT INTO retrieval_rankings VALUES (?, ?, ?, ?, ?)", insert_rows)
                insert_rows.clear()
    if insert_rows:
        con.executemany("INSERT INTO retrieval_rankings VALUES (?, ?, ?, ?, ?)", insert_rows)
    for old_channel, new_channel in channel_map.items():
        if new_channel in copied_channels:
            if old_channel == "description_semantic_text":
                _insert_channel(
                    con,
                    channel=new_channel,
                    path_key="description_semantic_text",
                    modality="text",
                    display_name="Description semantic text retrieval",
                    score_type="cosine",
                    top_k=top_k,
                    source_kind="copied_precompute",
                    model_name="google/gemini-embedding-2-preview",
                    artifacts={"source_db": source_db.as_posix(), "source_channel": old_channel},
                    notes="Copied from the existing precomputed description score DB.",
                )
            elif old_channel == "description_structured_facets":
                _insert_channel(
                    con,
                    channel=new_channel,
                    path_key="description_structured_facets",
                    modality="structured_text",
                    display_name="Description structured facet retrieval",
                    score_type="facet_overlap_penalty",
                    top_k=top_k,
                    source_kind="copied_precompute",
                    model_name="google/gemma-4-31b-it",
                    artifacts={"source_db": source_db.as_posix(), "source_channel": old_channel},
                    notes="Copied from the existing precomputed description score DB.",
                )


def build_reid_retrieval_score_db(
    *,
    output: Path,
    top_k: int = 200,
    torchreid_query_db: Path | None = None,
    torchreid_gallery_db: Path | None = None,
    fastreid_query_db: Path | None = None,
    fastreid_gallery_db: Path | None = None,
    description_source_db: Path | None = _DEFAULT_DESCRIPTION_SOURCE,
    include_descriptions: bool = True,
) -> Path:
    torch_pair = (torchreid_query_db, torchreid_gallery_db)
    fast_pair = (fastreid_query_db, fastreid_gallery_db)
    if any(torch_pair) and not all(torch_pair):
        raise ValueError("provide both torchreid_query_db and torchreid_gallery_db")
    if any(fast_pair) and not all(fast_pair):
        raise ValueError("provide both fastreid_query_db and fastreid_gallery_db")
    if not all(torch_pair) and not all(fast_pair):
        raise ValueError("provide at least one ReID query/gallery DB pair")

    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as con:
        _create_schema(con)
        _insert_metadata(
            con,
            top_k=top_k,
            notes="Combined precomputed person-ReID retrieval rankings for ReID visual paths and retained description paths.",
        )

        if torchreid_query_db and torchreid_gallery_db:
            q_count, g_count, dim = _insert_visual_rankings(
                con,
                channel=VISUAL_TORCHREID_CHANNEL,
                query_db=torchreid_query_db,
                gallery_db=torchreid_gallery_db,
                top_k=top_k,
            )
            _insert_channel(
                con,
                channel=VISUAL_TORCHREID_CHANNEL,
                path_key="visual_image_embedding",
                modality="image",
                display_name="Torchreid OSNet-AIN x1.0 MSMT17 visual ReID",
                score_type="cosine",
                top_k=top_k,
                source_kind="reid_repo",
                source_repo="deep-person-reid",
                model_name="osnet_ain_x1_0",
                checkpoint_path="deep-person-reid/weights/osnet_ain_x1_0_reid_msmt17.pth",
                embedding_dim=dim,
                normalized=True,
                artifacts={
                    "query_db": torchreid_query_db.as_posix(),
                    "gallery_db": torchreid_gallery_db.as_posix(),
                    "query_count": q_count,
                    "gallery_count": g_count,
                },
            )

        if fastreid_query_db and fastreid_gallery_db:
            q_count, g_count, dim = _insert_visual_rankings(
                con,
                channel=VISUAL_FASTREID_CHANNEL,
                query_db=fastreid_query_db,
                gallery_db=fastreid_gallery_db,
                top_k=top_k,
            )
            _insert_channel(
                con,
                channel=VISUAL_FASTREID_CHANNEL,
                path_key="visual_image_embedding",
                modality="image",
                display_name="FastReID SBS R101-IBN Market1501 visual ReID",
                score_type="cosine",
                top_k=top_k,
                source_kind="reid_repo",
                source_repo="fast-reid",
                model_name="sbs_R101-ibn",
                config_path="fast-reid/configs/Market1501/sbs_R101-ibn.yml",
                checkpoint_path="fast-reid/weights/market1501/market_sbs_R101-ibn.pth",
                embedding_dim=dim,
                normalized=True,
                artifacts={
                    "query_db": fastreid_query_db.as_posix(),
                    "gallery_db": fastreid_gallery_db.as_posix(),
                    "query_count": q_count,
                    "gallery_count": g_count,
                },
            )

        if include_descriptions:
            if description_source_db is None:
                raise ValueError("description_source_db is required when include_descriptions=True")
            _copy_description_channels(
                con,
                source_db=description_source_db,
                channel_map=DESCRIPTION_CHANNEL_MAP,
                top_k=top_k,
            )

        _create_json_view(con)
        con.commit()
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build precomputed ReID retrieval rankings DB")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--top-k", type=int, default=200)
    parser.add_argument("--torchreid-query-db", type=Path)
    parser.add_argument("--torchreid-gallery-db", type=Path)
    parser.add_argument("--fastreid-query-db", type=Path)
    parser.add_argument("--fastreid-gallery-db", type=Path)
    parser.add_argument("--description-source-db", type=Path, default=_DEFAULT_DESCRIPTION_SOURCE)
    parser.add_argument("--no-descriptions", action="store_true")
    args = parser.parse_args()

    output = build_reid_retrieval_score_db(
        output=args.output,
        top_k=args.top_k,
        torchreid_query_db=_resolve_path(args.torchreid_query_db),
        torchreid_gallery_db=_resolve_path(args.torchreid_gallery_db),
        fastreid_query_db=_resolve_path(args.fastreid_query_db),
        fastreid_gallery_db=_resolve_path(args.fastreid_gallery_db),
        description_source_db=_resolve_path(args.description_source_db),
        include_descriptions=not args.no_descriptions,
    )
    print(f"Wrote ReID retrieval score DB to {output}")


if __name__ == "__main__":
    main()
