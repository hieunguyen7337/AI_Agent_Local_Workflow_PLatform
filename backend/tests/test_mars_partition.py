"""Tests for MARS partition builder (synthetic mini dataset)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from scipy.io import savemat

from evals.person_reid_mars.build_partition import build_partition, middle_frame_relpath, tracklet_id


def _write_synthetic_mars(mars: Path) -> None:
    """Create a tiny MARS tree that matches Torchreid filename conventions."""
    # One image per tracklet; query_IDX selects first track (MATLAB 1 -> row 0).
    names = [
        "0001C10001.jpg",
        "0001C20002.jpg",
        "0010C10003.jpg",
        "0011C10004.jpg",
        "0012C10005.jpg",
        "0013C10006.jpg",
        "0014C10007.jpg",
        "0015C10008.jpg",
    ]
    (mars / "info").mkdir(parents=True)
    (mars / "info" / "test_name.txt").write_text("\n".join(names) + "\n", encoding="utf-8")

    # rows: start, end (MATLAB inclusive-style matching Torchreid slicing), pid, camid
    import numpy as np

    track_test = np.array(
        [
            [1, 1, 1, 1],
            [2, 2, 1, 2],
            [3, 3, 10, 1],
            [4, 4, 11, 1],
            [5, 5, 12, 1],
            [6, 6, 13, 1],
            [7, 7, 14, 1],
            [8, 8, 15, 1],
        ],
        dtype=np.int64,
    )
    savemat(mars / "info" / "tracks_test_info.mat", {"track_test_info": track_test})
    savemat(mars / "info" / "query_IDX.mat", {"query_IDX": np.array([[1]], dtype=np.int64)})

    bbox = mars / "bbox_test"
    for name in names:
        person = name[:4]
        dest = bbox / person / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\xff\xd8\xff\xd9")  # minimal JPEG markers


def test_middle_frame_relpath_first_image(tmp_path: Path):
    mars = tmp_path / "mars"
    _write_synthetic_mars(mars)
    names = (mars / "info" / "test_name.txt").read_text(encoding="utf-8").splitlines()
    track_test = __import__("scipy.io", fromlist=["loadmat"]).loadmat(
        mars / "info" / "tracks_test_info.mat"
    )["track_test_info"]
    p = middle_frame_relpath(mars, names, track_test[0])
    assert p.name == "0001C10001.jpg"


def test_build_partition_synthetic_1q_4g(tmp_path: Path):
    mars = tmp_path / "mars"
    _write_synthetic_mars(mars)
    out = tmp_path / "partition"
    build_partition(
        mars_root=mars,
        output_dir=out,
        n_queries=1,
        gallery_size=4,
        seed=0,
        retrieval_top_k=10,
    )

    rows = yaml.safe_load((out / "dataset.yaml").read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["query_id"] == tracklet_id(0)
    assert len(rows[0]["gallery_ids"]) == 4
    assert len(rows[0]["gallery_pids"]) == 4
    assert rows[0]["gallery_ids"] == sorted(rows[0]["gallery_ids"])

    stats = __import__("json").loads((out / "partition_stats.json").read_text(encoding="utf-8"))
    assert stats["full_protocol"]["num_test_tracklets"] == 8
    assert stats["full_protocol"]["num_query_tracklets_official"] == 1
    assert stats["full_protocol"]["num_gallery_tracklets_official"] == 7

    assert (out / "query" / f"{tracklet_id(0)}.jpg").is_file()
    for gid in rows[0]["gallery_ids"]:
        assert (out / "bounding_box_test" / f"{gid}.jpg").is_file()

    assert any(
        pid == rows[0]["query_pid"] and cam != rows[0]["query_camid"]
        for pid, cam in zip(rows[0]["gallery_pids"], rows[0]["gallery_camids"])
    )
