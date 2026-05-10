"""Build a MARS video-ReID eval partition (tracklets as units).

Follows the official MARS test split (Torchreid ``mars.py``): queries from
``query_IDX.mat``; gallery tracklets are all other rows in ``tracks_test_info``.
Uses the middle frame of each tracklet as the canonical image.

Outputs under ``output_dir``:

- ``query/``, ``bounding_box_test/`` — copied middle frames named by tracklet id
- ``dataset.yaml`` — list of row dicts compatible with Market-1501 partition rows
- ``partition_stats.json`` — full-protocol counts vs this subset
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path
from typing import Any

import yaml
from scipy.io import loadmat

from backend.repo_root import find_repo_root, to_repo_posix

_DEFAULT_OUTPUT = Path(__file__).parent / "partition_100q_1000g"
_DEFAULT_DESCRIPTION_DB_DIRNAME = "description_db_google_gemma_4_31b_it"


def tracklet_id(track_test_row_index: int) -> str:
    return f"mars_{int(track_test_row_index):05d}"


def _load_test_names(test_name_path: Path) -> list[str]:
    lines = test_name_path.read_text(encoding="utf-8").splitlines()
    return [line.rstrip() for line in lines if line.strip()]


def _middle_frame_index(num_frames: int) -> int:
    if num_frames <= 0:
        raise ValueError("tracklet has no frames")
    return (num_frames - 1) // 2


def middle_frame_relpath(
    mars_root: Path,
    names: list[str],
    track_row: Any,
    *,
    bbox_home: str = "bbox_test",
) -> Path:
    """Return absolute path to the middle frame of one tracklet."""
    start_index = int(track_row[0])
    end_index = int(track_row[1])
    img_names = names[start_index - 1 : end_index]
    if not img_names:
        raise ValueError("empty tracklet slice")
    mid = _middle_frame_index(len(img_names))
    img_name = img_names[mid]
    person_folder = img_name[:4]
    return (mars_root / bbox_home / person_folder / img_name).resolve()


def _junk_pid(pid: int) -> bool:
    return int(pid) in {-1, 0}


def _collect_track_meta(track_test: Any, row_index: int) -> tuple[int, int]:
    pid = int(track_test[row_index, 2])
    camid = int(track_test[row_index, 3])
    return pid, camid


def build_partition(
    *,
    mars_root: Path,
    output_dir: Path = _DEFAULT_OUTPUT,
    n_queries: int = 100,
    gallery_size: int = 1000,
    seed: int = 42,
    retrieval_top_k: int = 200,
    bbox_home: str = "bbox_test",
) -> Path:
    """Sample ``n_queries`` official query tracklets and a shared ``gallery_size`` gallery."""
    info_dir = mars_root / "info"
    test_name_path = info_dir / "test_name.txt"
    track_test_path = info_dir / "tracks_test_info.mat"
    query_idx_path = info_dir / "query_IDX.mat"

    for path in (test_name_path, track_test_path, query_idx_path):
        if not path.is_file():
            raise FileNotFoundError(f"required MARS metadata file not found: {path}")

    bbox_root = mars_root / bbox_home
    if not bbox_root.is_dir():
        raise FileNotFoundError(f"{bbox_home} directory not found under MARS root: {bbox_root}")

    names = _load_test_names(test_name_path)
    track_test = loadmat(track_test_path)["track_test_info"]
    query_idx_raw = loadmat(query_idx_path)["query_IDX"].squeeze()
    query_indices = {int(x) for x in query_idx_raw.flatten().tolist()}
    # MATLAB indices -> 0-based row indices into track_test (Torchreid convention).
    query_indices = {i - 1 for i in query_indices}

    num_test_tracklets = int(track_test.shape[0])
    all_indices = set(range(num_test_tracklets))
    invalid_query_idx = sorted(query_indices - all_indices)
    if invalid_query_idx:
        raise ValueError(f"query_IDX out of range for track_test: {invalid_query_idx[:8]}...")

    gallery_indices_list = sorted(all_indices - query_indices)
    full_stats = {
        "num_test_tracklets": num_test_tracklets,
        "num_query_tracklets_official": len(query_indices),
        "num_gallery_tracklets_official": len(gallery_indices_list),
    }

    gallery_index_set = set(gallery_indices_list)

    def eligible_query_indices() -> list[int]:
        eligible: list[int] = []
        for q_idx in sorted(query_indices):
            qpid, qcam = _collect_track_meta(track_test, q_idx)
            if _junk_pid(qpid):
                continue
            has_positive = False
            for g_idx in gallery_index_set:
                gpid, gcam = _collect_track_meta(track_test, g_idx)
                if _junk_pid(gpid):
                    continue
                if gpid == qpid and gcam != qcam:
                    has_positive = True
                    break
            if has_positive:
                eligible.append(q_idx)
        return eligible

    eligible = eligible_query_indices()
    rng = random.Random(seed)
    if len(eligible) < n_queries:
        raise ValueError(
            f"Need {n_queries} eligible queries with cross-camera positives in gallery, "
            f"found {len(eligible)}"
        )
    selected_query_indices = rng.sample(eligible, n_queries)

    positives_by_query: dict[int, list[int]] = {}
    for q_idx in selected_query_indices:
        qpid, qcam = _collect_track_meta(track_test, q_idx)
        positives_by_query[q_idx] = [
            g_idx
            for g_idx in gallery_index_set
            if not _junk_pid(int(track_test[g_idx, 2]))
            and int(track_test[g_idx, 2]) == qpid
            and int(track_test[g_idx, 3]) != qcam
        ]

    selected_gallery: dict[int, tuple[str, Path, int, int]] = {}
    for q_idx in selected_query_indices:
        choices = positives_by_query[q_idx]
        if not choices:
            raise RuntimeError(f"internal error: query {q_idx} lost positives")
        g_idx = rng.choice(choices)
        gid = tracklet_id(g_idx)
        gpid, gcam = _collect_track_meta(track_test, g_idx)
        path_mid = middle_frame_relpath(mars_root, names, track_test[g_idx], bbox_home=bbox_home)
        selected_gallery[g_idx] = (gid, path_mid, gpid, gcam)

    filler_pool = [g_idx for g_idx in gallery_index_set if g_idx not in selected_gallery]
    rng.shuffle(filler_pool)
    needed = gallery_size - len(selected_gallery)
    if needed < 0:
        raise ValueError(
            f"gallery_size {gallery_size} smaller than required unique positives {len(selected_gallery)}"
        )
    if len(filler_pool) < needed:
        raise ValueError(f"Need {needed} gallery fillers, found {len(filler_pool)}")

    for g_idx in filler_pool[:needed]:
        gid = tracklet_id(g_idx)
        gpid, gcam = _collect_track_meta(track_test, g_idx)
        path_mid = middle_frame_relpath(mars_root, names, track_test[g_idx], bbox_home=bbox_home)
        selected_gallery[g_idx] = (gid, path_mid, gpid, gcam)

    gallery_rows = sorted(selected_gallery.values(), key=lambda item: item[0])
    gallery_ids = [item[0] for item in gallery_rows]
    gallery_pids = [int(item[2]) for item in gallery_rows]
    gallery_camids = [int(item[3]) for item in gallery_rows]

    repo_root = find_repo_root()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    out_query_dir = output_dir / "query"
    out_gallery_dir = output_dir / "bounding_box_test"
    out_query_dir.mkdir(parents=True)
    out_gallery_dir.mkdir(parents=True)

    query_rows_sorted = sorted(selected_query_indices)

    for _gid, src_path, _p, _c in gallery_rows:
        if not src_path.is_file():
            raise FileNotFoundError(f"middle frame missing for gallery tracklet {_gid}: {src_path}")

    query_embedding_db_path = to_repo_posix(
        output_dir.parent / "embedding_db" / "query_embeddings.sqlite", repo_root=repo_root
    )
    gallery_embedding_db_path = to_repo_posix(
        output_dir.parent / "embedding_db" / "gallery_embeddings.sqlite", repo_root=repo_root
    )
    gallery_description_db_path = to_repo_posix(
        output_dir.parent / _DEFAULT_DESCRIPTION_DB_DIRNAME / "gallery_descriptions.sqlite",
        repo_root=repo_root,
    )
    query_description_db_path = to_repo_posix(
        output_dir.parent / _DEFAULT_DESCRIPTION_DB_DIRNAME / "query_descriptions.sqlite",
        repo_root=repo_root,
    )

    rows: list[dict[str, Any]] = []
    for q_idx in query_rows_sorted:
        qid_str = tracklet_id(q_idx)
        qpid, qcam = _collect_track_meta(track_test, q_idx)
        src_query = middle_frame_relpath(mars_root, names, track_test[q_idx], bbox_home=bbox_home)
        if not src_query.is_file():
            raise FileNotFoundError(f"middle frame missing for query tracklet {q_idx}: {src_query}")
        dest_query = out_query_dir / f"{qid_str}{src_query.suffix.lower()}"
        shutil.copy2(src_query, dest_query)

        rows.append(
            {
                "query_id": qid_str,
                "query_image_path": to_repo_posix(dest_query, repo_root=repo_root),
                "query_embedding_db_path": query_embedding_db_path,
                "query_description_db_path": query_description_db_path,
                "gallery_embedding_db_path": gallery_embedding_db_path,
                "gallery_description_db_path": gallery_description_db_path,
                "retrieval_top_k": retrieval_top_k,
                "query_pid": int(qpid),
                "query_camid": int(qcam),
                "gallery_ids": gallery_ids,
                "gallery_pids": gallery_pids,
                "gallery_camids": gallery_camids,
            }
        )

    for gid_str, src_path, _p, _c in gallery_rows:
        dest_g = out_gallery_dir / f"{gid_str}{src_path.suffix.lower()}"
        shutil.copy2(src_path, dest_g)

    dataset_path = output_dir / "dataset.yaml"
    dataset_path.write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")

    partition_stats = {
        "mars_root": str(mars_root.resolve()),
        "full_protocol": full_stats,
        "partition": {
            "n_queries": n_queries,
            "gallery_size": gallery_size,
            "seed": seed,
            "eligible_query_tracklets": len(eligible),
            "selected_query_tracklets": len(selected_query_indices),
            "unique_gallery_tracklets": len(selected_gallery),
        },
        "notes": (
            "Gallery tracklets are the official complement of query_IDX in tracks_test_info "
            "(Torchreid MARS convention). Middle frame = index (n-1)//2 within each tracklet."
        ),
    }
    stats_path = output_dir / "partition_stats.json"
    stats_path.write_text(json.dumps(partition_stats, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(partition_stats, indent=2, ensure_ascii=False))

    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Build MARS tracklet eval partition (official test split)")
    parser.add_argument("--mars-root", required=True, type=Path, help="Path to MARS root (contains bbox_test/ and info/)")
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--n-queries", type=int, default=100)
    parser.add_argument("--gallery-size", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--retrieval-top-k", type=int, default=200)
    parser.add_argument(
        "--bbox-home",
        default="bbox_test",
        help="Subdirectory under mars-root with bounding boxes (default bbox_test)",
    )
    args = parser.parse_args()

    output = build_partition(
        mars_root=args.mars_root,
        output_dir=args.output_dir,
        n_queries=args.n_queries,
        gallery_size=args.gallery_size,
        seed=args.seed,
        retrieval_top_k=args.retrieval_top_k,
        bbox_home=args.bbox_home,
    )
    print(f"Wrote partition to {output}")


if __name__ == "__main__":
    main()
