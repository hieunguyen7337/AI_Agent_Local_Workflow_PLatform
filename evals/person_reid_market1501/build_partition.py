"""Build a Market-1501 eval partition (default 1000 queries / 5000 gallery images)."""
from __future__ import annotations

import argparse
import random
import re
import shutil
from pathlib import Path

import yaml

from backend.repo_root import find_repo_root, to_repo_posix

_FILENAME_RE = re.compile(r"^(\d+)_c(\d+)s\d+_\d+_\d+\.jpg$")
_DEFAULT_OUTPUT = Path(__file__).parent / "partition_1000q_5000g"
_DEFAULT_DESCRIPTION_DB_DIRNAME = "description_db_google_gemma_4_31b_it"


def _parse_image(filename: str) -> tuple[int, int] | None:
    match = _FILENAME_RE.match(filename)
    if not match:
        return None
    pid, camid = int(match.group(1)), int(match.group(2))
    if pid in {-1, 0}:
        return None
    return pid, camid


def _load_images(directory: Path) -> list[tuple[str, Path, int, int]]:
    rows = []
    for path in sorted(directory.glob("*.jpg")):
        parsed = _parse_image(path.name)
        if parsed is None:
            continue
        pid, camid = parsed
        rows.append((path.name, path, pid, camid))
    return rows


def build_partition(
    *,
    market1501_root: Path,
    output_dir: Path = _DEFAULT_OUTPUT,
    n_queries: int = 1000,
    gallery_size: int = 5000,
    seed: int = 42,
    retrieval_top_k: int = 200,
) -> Path:
    query_dir = market1501_root / "query"
    gallery_dir = market1501_root / "bounding_box_test"
    if not query_dir.is_dir():
        raise FileNotFoundError(f"query directory not found: {query_dir}")
    if not gallery_dir.is_dir():
        raise FileNotFoundError(f"gallery directory not found: {gallery_dir}")

    queries = _load_images(query_dir)
    gallery = _load_images(gallery_dir)
    if len(queries) < n_queries:
        raise ValueError(f"Need {n_queries} valid queries, found {len(queries)}")
    if len(gallery) < gallery_size:
        raise ValueError(f"Need {gallery_size} valid gallery images, found {len(gallery)}")

    positives_by_query: dict[str, list[tuple[str, Path, int, int]]] = {
        qid: [g for g in gallery if g[2] == qpid and g[3] != qcamid]
        for qid, _qpath, qpid, qcamid in queries
    }
    eligible_queries = [q for q in queries if positives_by_query[q[0]]]
    rng = random.Random(seed)
    if len(eligible_queries) < n_queries:
        raise ValueError(f"Need {n_queries} queries with positives, found {len(eligible_queries)}")
    selected_queries = rng.sample(eligible_queries, n_queries)

    selected_gallery: dict[str, tuple[str, Path, int, int]] = {}
    for query in selected_queries:
        qid = query[0]
        selected_positive = rng.choice(positives_by_query[qid])
        selected_gallery[selected_positive[0]] = selected_positive

    # Any image not yet chosen may be used as filler (including other views of query
    # identities). Excluding entire query PIDs exhausts the pool for large n_queries.
    filler = [item for item in gallery if item[0] not in selected_gallery]
    rng.shuffle(filler)
    needed = gallery_size - len(selected_gallery)
    if len(filler) < needed:
        raise ValueError(f"Need {needed} gallery fillers, found {len(filler)}")
    for item in filler[:needed]:
        selected_gallery[item[0]] = item

    gallery_rows = sorted(selected_gallery.values(), key=lambda item: item[0])
    query_rows = sorted(selected_queries, key=lambda item: item[0])
    repo_root = find_repo_root()

    out_query_dir = output_dir / "query"
    out_gallery_dir = output_dir / "bounding_box_test"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    out_query_dir.mkdir(parents=True)
    out_gallery_dir.mkdir(parents=True)

    for qid, path, _pid, _camid in query_rows:
        shutil.copy2(path, out_query_dir / qid)
    for gid, path, _pid, _camid in gallery_rows:
        shutil.copy2(path, out_gallery_dir / gid)

    gallery_ids = [gid for gid, _path, _pid, _camid in gallery_rows]
    gallery_pids = [pid for _gid, _path, pid, _camid in gallery_rows]
    gallery_camids = [camid for _gid, _path, _pid, camid in gallery_rows]
    gallery_embedding_db_path = to_repo_posix(
        output_dir.parent / "embedding_db" / "gallery_embeddings.sqlite", repo_root=repo_root
    )
    query_embedding_db_path = to_repo_posix(
        output_dir.parent / "embedding_db" / "query_embeddings.sqlite", repo_root=repo_root
    )
    gallery_description_db_path = to_repo_posix(
        output_dir.parent / _DEFAULT_DESCRIPTION_DB_DIRNAME / "gallery_descriptions.sqlite",
        repo_root=repo_root,
    )
    query_description_db_path = to_repo_posix(
        output_dir.parent / _DEFAULT_DESCRIPTION_DB_DIRNAME / "query_descriptions.sqlite",
        repo_root=repo_root,
    )

    rows = []
    for qid, _path, qpid, qcamid in query_rows:
        rows.append(
            {
                "query_id": qid,
                "query_image_path": to_repo_posix(out_query_dir / qid, repo_root=repo_root),
                "query_embedding_db_path": query_embedding_db_path,
                "query_description_db_path": query_description_db_path,
                "gallery_embedding_db_path": gallery_embedding_db_path,
                "gallery_description_db_path": gallery_description_db_path,
                "retrieval_top_k": retrieval_top_k,
                "query_pid": qpid,
                "query_camid": qcamid,
                "gallery_ids": gallery_ids,
                "gallery_pids": gallery_pids,
                "gallery_camids": gallery_camids,
            }
        )
    (output_dir / "dataset.yaml").write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Build partitioned Market-1501 eval dataset")
    parser.add_argument("--market1501-root", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--n-queries", type=int, default=1000)
    parser.add_argument("--gallery-size", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--retrieval-top-k", type=int, default=200)
    args = parser.parse_args()

    output = build_partition(
        market1501_root=args.market1501_root,
        output_dir=args.output_dir,
        n_queries=args.n_queries,
        gallery_size=args.gallery_size,
        seed=args.seed,
        retrieval_top_k=args.retrieval_top_k,
    )
    print(f"Wrote partition to {output}")


if __name__ == "__main__":
    main()
