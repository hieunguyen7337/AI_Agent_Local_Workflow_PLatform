"""Build a 100-sample Market-1501 eval dataset.

Reads the official Market-1501 directory layout and writes
evals/person_reid_market1501/dataset.yaml (gitignored).

Usage:
    python evals/person_reid_market1501/build_dataset.py \\
        --market1501-root /path/to/Market-1501 \\
        --gallery-db evals/person_reid_market1501/gallery_db/attributes.sqlite \\
        --seed 42

Market-1501 expected layout:
    <root>/query/                  query images
    <root>/bounding_box_test/      gallery images

Filename format: <pid>_c<camid>s<seq>_<frame>_<bbox>.jpg
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
from pathlib import Path

import yaml

_FILENAME_RE = re.compile(r"^(\d+)_c(\d+)s\d+_\d+_\d+\.jpg$")
_OUTPUT_PATH = Path(__file__).parent / "dataset.yaml"
_INDEX_DIR = Path(__file__).parent / "gallery_index"
_GALLERY_DB_PATH = Path(__file__).parent / "gallery_db" / "attributes.sqlite"
_QUERY_DB_PATH = Path(__file__).parent / "attribute_db" / "query_attributes.sqlite"
_GALLERY_EMBEDDING_DB_PATH = Path(__file__).parent / "embedding_db" / "gallery_embeddings.sqlite"
_QUERY_EMBEDDING_DB_PATH = Path(__file__).parent / "embedding_db" / "query_embeddings.sqlite"


def _parse_image(filename: str) -> tuple[int, int] | None:
    m = _FILENAME_RE.match(filename)
    if not m:
        return None
    pid, camid = int(m.group(1)), int(m.group(2))
    if pid in {-1, 0}:
        return None
    return pid, camid


def _load_images(directory: Path) -> list[tuple[str, int, int]]:
    results = []
    for p in sorted(directory.glob("*.jpg")):
        parsed = _parse_image(p.name)
        if parsed is None:
            continue
        pid, camid = parsed
        results.append((p.name, pid, camid))
    return results


def _placeholder_embedding(path: Path, dimensions: int = 32) -> list[float]:
    digest = hashlib.sha256(path.read_bytes()).digest()
    values = []
    while len(values) < dimensions:
        for byte in digest:
            values.append((byte / 127.5) - 1.0)
            if len(values) == dimensions:
                break
        digest = hashlib.sha256(digest).digest()
    return values


def build_gallery_index(*, market1501_root: Path, output_dir: Path = _INDEX_DIR) -> dict[str, Path]:
    gallery_dir = market1501_root / "bounding_box_test"
    if not gallery_dir.is_dir():
        raise FileNotFoundError(f"gallery directory not found: {gallery_dir}")

    all_gallery = _load_images(gallery_dir)
    if not all_gallery:
        raise ValueError(f"No valid gallery images found in {gallery_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "gallery_manifest.yaml"
    embeddings_path = output_dir / "gallery_embeddings.jsonl"
    id_to_row_path = output_dir / "id_to_row.yaml"

    manifest = []
    id_to_row: dict[str, int] = {}
    with embeddings_path.open("w", encoding="utf-8") as f:
        for row_index, (gid, gpid, gcamid) in enumerate(all_gallery):
            image_path = (gallery_dir / gid).resolve()
            manifest.append(
                {
                    "id": gid,
                    "pid": gpid,
                    "camid": gcamid,
                    "image_path": image_path.as_posix(),
                }
            )
            id_to_row[gid] = row_index
            f.write(
                json.dumps(
                    {
                        "id": gid,
                        "embedding": _placeholder_embedding(image_path),
                    }
                )
                + "\n"
            )

    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    id_to_row_path.write_text(yaml.safe_dump(id_to_row, sort_keys=True), encoding="utf-8")
    return {
        "manifest": manifest_path,
        "embeddings": embeddings_path,
        "id_to_row": id_to_row_path,
    }


def build_dataset(
    *,
    market1501_root: Path,
    n_queries: int = 100,
    seed: int = 42,
    gallery_db_path: Path = _GALLERY_DB_PATH,
    query_db_path: Path = _QUERY_DB_PATH,
    gallery_embedding_db_path: Path = _GALLERY_EMBEDDING_DB_PATH,
    query_embedding_db_path: Path = _QUERY_EMBEDDING_DB_PATH,
    retrieval_top_k: int = 500,
) -> list[dict]:
    rng = random.Random(seed)

    query_dir = market1501_root / "query"
    gallery_dir = market1501_root / "bounding_box_test"
    if not query_dir.is_dir():
        raise FileNotFoundError(f"query directory not found: {query_dir}")
    if not gallery_dir.is_dir():
        raise FileNotFoundError(f"gallery directory not found: {gallery_dir}")

    all_queries = _load_images(query_dir)
    all_gallery = _load_images(gallery_dir)
    if not all_queries:
        raise ValueError(f"No valid query images found in {query_dir}")
    if not all_gallery:
        raise ValueError(f"No valid gallery images found in {gallery_dir}")

    sampled_queries = rng.sample(all_queries, min(n_queries, len(all_queries)))

    gallery_ids = [g[0] for g in all_gallery]
    gallery_pids = [g[1] for g in all_gallery]
    gallery_camids = [g[2] for g in all_gallery]
    resolved_gallery_db_path = gallery_db_path.resolve()
    resolved_query_db_path = query_db_path.resolve()
    resolved_gallery_embedding_db_path = gallery_embedding_db_path.resolve()
    resolved_query_embedding_db_path = query_embedding_db_path.resolve()

    rows = []
    for qid, qpid, qcamid in sampled_queries:
        rows.append({
            "query_id": qid,
            "query_image_path": (query_dir / qid).resolve().as_posix(),
            "query_db_path": resolved_query_db_path.as_posix(),
            "query_embedding_db_path": resolved_query_embedding_db_path.as_posix(),
            "gallery_db_path": resolved_gallery_db_path.as_posix(),
            "gallery_embedding_db_path": resolved_gallery_embedding_db_path.as_posix(),
            "retrieval_top_k": retrieval_top_k,
            "query_pid": qpid,
            "query_camid": qcamid,
            "gallery_ids": gallery_ids,
            "gallery_pids": gallery_pids,
            "gallery_camids": gallery_camids,
        })

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Market-1501 eval dataset")
    parser.add_argument("--market1501-root", required=True, type=Path)
    parser.add_argument("--n-queries", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=_OUTPUT_PATH)
    parser.add_argument("--gallery-db", type=Path, default=_GALLERY_DB_PATH)
    parser.add_argument("--query-db", type=Path, default=_QUERY_DB_PATH)
    parser.add_argument("--gallery-embedding-db", type=Path, default=_GALLERY_EMBEDDING_DB_PATH)
    parser.add_argument("--query-embedding-db", type=Path, default=_QUERY_EMBEDDING_DB_PATH)
    parser.add_argument("--retrieval-top-k", type=int, default=500)
    args = parser.parse_args()

    rows = build_dataset(
        market1501_root=args.market1501_root,
        n_queries=args.n_queries,
        seed=args.seed,
        gallery_db_path=args.gallery_db,
        query_db_path=args.query_db,
        gallery_embedding_db_path=args.gallery_embedding_db,
        query_embedding_db_path=args.query_embedding_db,
        retrieval_top_k=args.retrieval_top_k,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(rows, allow_unicode=True), encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {args.output}")
    pos_count = sum(
        1 for r in rows
        for gid, gpid, gcamid in zip(r["gallery_ids"], r["gallery_pids"], r["gallery_camids"])
        if gpid == r["query_pid"] and gcamid != r["query_camid"]
    )
    avg_pos = pos_count / len(rows) if rows else 0
    print(f"Gallery size: {len(rows[0]['gallery_ids']) if rows else 0}")
    print(f"Average true positives per query: {avg_pos:.1f}")


if __name__ == "__main__":
    main()
