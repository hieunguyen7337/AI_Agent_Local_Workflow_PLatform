"""Generalized dataset eval adapter tests."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import yaml

from backend.evals import dataset
from backend.evals.dataset import DatasetEvalConfig, load_dataset_rows, run_dataset_eval


def _fake_result(tmp_path: Path, *, final_state: dict, status: str = "ok") -> SimpleNamespace:
    run_dir = tmp_path / f"run_{len(list(tmp_path.glob('run_*')))}"
    run_dir.mkdir(parents=True)
    return SimpleNamespace(
        status=status,
        final_state=final_state,
        run_id=run_dir.name,
        run_dir=run_dir,
        error=None,
        cost_usd=0.01,
        latency_ms=10.0,
    )


def test_load_dataset_rows_csv_jsonl_yaml(tmp_path: Path):
    csv_path = tmp_path / "data.csv"
    csv_path.write_text("input,expected\nhello,world\n", encoding="utf-8")
    assert load_dataset_rows(csv_path, "csv") == [{"input": "hello", "expected": "world"}]

    jsonl_path = tmp_path / "data.jsonl"
    jsonl_path.write_text('{"input": "hello", "expected": "world"}\n', encoding="utf-8")
    assert load_dataset_rows(jsonl_path, "jsonl") == [{"input": "hello", "expected": "world"}]

    yaml_path = tmp_path / "data.yaml"
    yaml_path.write_text(yaml.safe_dump([{"input": "hello", "expected": "world"}]), encoding="utf-8")
    assert load_dataset_rows(yaml_path, "yaml") == [{"input": "hello", "expected": "world"}]


def test_dataset_eval_maps_rows_to_full_state_and_writes_artifact(monkeypatch, tmp_path: Path):
    eval_dir = tmp_path / "evals" / "demo"
    eval_dir.mkdir(parents=True)
    dataset_path = eval_dir / "data.jsonl"
    dataset_path.write_text(
        '{"question": "Q1", "expected": "A1", "row_id": 7}\n'
        '{"question": "Q2", "expected": "A2", "row_id": 8}\n',
        encoding="utf-8",
    )
    config_path = eval_dir / "dataset_eval.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "dataset_path": "data.jsonl",
                "input_mapping": {
                    "user_input": "question",
                    "_expected": "expected",
                    "artifacts.row_id": "row_id",
                },
                "scorers": [
                    {
                        "id": "answer_contains_expected",
                        "type": "substring",
                        "actual": "final_state.final_answer",
                        "expected": "row.expected",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    captured_states: list[dict] = []
    captured_concurrency: dict[str, int] = {}
    captured_batch_kwargs: dict[str, object] = {}

    def _fake_batch(workflow, items, **kwargs):
        captured_batch_kwargs.update(kwargs)
        captured_concurrency["value"] = kwargs["max_concurrency"]
        captured_states.extend([dict(item.input_state) for item in items])
        return [
            _fake_result(
                tmp_path,
                final_state={"final_answer": f"answer {item.input_state['_expected']}"},
            )
            for item in items
        ]

    monkeypatch.setattr(dataset, "run_workflow_batch", _fake_batch)

    out = run_dataset_eval(
        workflow="demo",
        config_path=config_path,
        runs_root=tmp_path / "runs",
    )

    assert out["status"] == "ok"
    assert out["row_count"] == 2
    assert out["completed_run_count"] == 2
    assert out["overall"]["passes"] == 2
    assert captured_states == [
        {"user_input": "Q1", "_expected": "A1", "artifacts": {"row_id": 7}},
        {"user_input": "Q2", "_expected": "A2", "artifacts": {"row_id": 8}},
    ]
    assert captured_concurrency["value"] == 50
    output_path = Path(out["output_path"])
    assert output_path.exists()
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["results"][0]["scorers"][0]["pass"] is True
    assert output_path.name == "eval_summary.json"
    assert output_path.parent.parent.name == "demo"
    assert "_AEST_demo_2rows" in output_path.parent.name
    assert captured_batch_kwargs["shared_run_dir"] == output_path.parent
    assert captured_batch_kwargs["use_checkpointer"] is False
    assert captured_batch_kwargs["write_audit_summary"] is False
    assert (output_path.parent / "rows.jsonl").exists()
    assert (output_path.parent / "failed_rows.jsonl").exists()
    assert (output_path.parent / "manifest.json").exists()
    assert not list(output_path.parent.glob("**/checkpoints.db"))


def test_dataset_eval_builtin_scorers(monkeypatch, tmp_path: Path):
    eval_dir = tmp_path / "evals" / "demo"
    eval_dir.mkdir(parents=True)
    (eval_dir / "data.yaml").write_text(
        yaml.safe_dump([{"input": "x", "expected": "done"}]),
        encoding="utf-8",
    )
    config_path = eval_dir / "dataset_eval.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "dataset_path": "data.yaml",
                "input_mapping": {"user_input": "input"},
                "scorers": [
                    {"id": "exact", "type": "exact", "actual": "final_state.label", "expected": "done"},
                    {"id": "bool", "type": "boolean", "actual": "final_state.ok", "expected": True},
                    {
                        "id": "threshold",
                        "type": "numeric_threshold",
                        "actual": "final_state.score",
                        "operator": ">=",
                        "threshold": 0.8,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    def _fake_batch(workflow, items, **kwargs):
        return [
            _fake_result(
                tmp_path,
                final_state={"label": "done", "ok": True, "score": 0.91},
            )
            for _item in items
        ]

    monkeypatch.setattr(dataset, "run_workflow_batch", _fake_batch)

    out = run_dataset_eval(workflow="demo", config_path=config_path, runs_root=tmp_path / "runs")
    assert out["overall"]["passes"] == 1
    assert [item["type"] for item in out["results"][0]["scorers"]] == [
        "exact",
        "boolean",
        "numeric_threshold",
    ]


def test_dataset_eval_config_default_max_concurrency_is_50():
    config = DatasetEvalConfig(dataset_path="data.yaml")
    assert config.max_concurrency == 50


# ---------------------------------------------------------------------------
# map_cmc scorer
# ---------------------------------------------------------------------------

# Shared small gallery for all map_cmc tests:
# IDs: g0..g7, pids: 1,1,1,2,2,3,3,3, camids: 1,2,3,1,2,1,2,3
_GALLERY_IDS = ["g0", "g1", "g2", "g3", "g4", "g5", "g6", "g7"]
_GALLERY_PIDS = [1, 1, 1, 2, 2, 3, 3, 3]
_GALLERY_CAMIDS = [1, 2, 3, 1, 2, 1, 2, 3]

# query: pid=1, camid=1  → junk is g0 (pid=1,cam=1); good are g1,g2 (pid=1,cam≠1)
_QUERY_PID = 1
_QUERY_CAMID = 1


def _reid_attrs(**overrides: str) -> dict[str, str]:
    attrs = {
        "gender": "male",
        "hair": "short",
        "clothing_type": "pants",
        "upper_body_clothes": "short sleeve",
        "lower_body_clothes": "long",
        "hat": "no",
        "backpack": "yes",
        "bag": "no",
        "handbag": "no",
    }
    attrs.update(overrides)
    return attrs


def _base_scorer_cfg() -> dict:
    return {
        "id": "reid",
        "type": "map_cmc",
        "actual": "final_state.ranked_gallery_pids",
        "gallery_ids": "row.gallery_ids",
        "gallery_pids": "row.gallery_pids",
        "gallery_camids": "row.gallery_camids",
        "query_pid": "row.query_pid",
        "query_camid": "row.query_camid",
        "cmc_ks": [1, 5, 10],
    }


def _row() -> dict:
    return {
        "gallery_ids": _GALLERY_IDS,
        "gallery_pids": _GALLERY_PIDS,
        "gallery_camids": _GALLERY_CAMIDS,
        "query_pid": _QUERY_PID,
        "query_camid": _QUERY_CAMID,
    }


def test_map_cmc_perfect_rank1():
    from backend.evals.dataset import _score

    # Rank g1 first (pid=1, cam=2 → relevant), then distractors
    ranked = ["g0", "g1", "g3", "g4", "g5", "g6", "g7", "g2"]
    # g0 is junk (same pid+cam) → filtered out; g1 is relevant at rank 1 after filtering
    result = _score(_base_scorer_cfg(), row=_row(), final_state={"ranked_gallery_pids": ranked})
    assert result["pass"] is True
    assert result["actual"]["cmc_1"] == 1
    assert result["actual"]["cmc_5"] == 1
    # AP: 2 good items (g1,g2); g1 at filtered rank 1, g2 at filtered rank 7 → AP=(1/1 + 2/7)/2
    expected_ap = (1 / 1 + 2 / 7) / 2
    assert abs(result["actual"]["ap"] - expected_ap) < 1e-9
    assert result["expected"] == _QUERY_PID


def test_map_cmc_miss_at_1_hit_at_5():
    from backend.evals.dataset import _score

    # g3,g4,g5,g6 first (all distractors), then g1 (relevant) at filtered rank 5
    ranked = ["g3", "g4", "g5", "g6", "g1", "g2", "g7"]
    result = _score(_base_scorer_cfg(), row=_row(), final_state={"ranked_gallery_pids": ranked})
    assert result["pass"] is False  # cmc_1 == 0
    assert result["actual"]["cmc_1"] == 0
    assert result["actual"]["cmc_5"] == 1
    assert result["actual"]["cmc_10"] == 1


def test_map_cmc_all_wrong():
    from backend.evals.dataset import _score

    # Only distractors in ranked list — no relevant item
    ranked = ["g3", "g4", "g5", "g6", "g7"]
    result = _score(_base_scorer_cfg(), row=_row(), final_state={"ranked_gallery_pids": ranked})
    assert result["pass"] is False
    assert result["actual"]["cmc_1"] == 0
    assert result["actual"]["cmc_5"] == 0
    assert result["actual"]["cmc_10"] == 0
    assert result["actual"]["ap"] == 0.0


def test_map_cmc_junk_filtered_not_counted():
    from backend.evals.dataset import _score

    # g0 is same-cam junk; placing it first should not count as relevant hit
    ranked = ["g0", "g3", "g4", "g5", "g1"]
    result = _score(_base_scorer_cfg(), row=_row(), final_state={"ranked_gallery_pids": ranked})
    assert result["actual"]["cmc_1"] == 0  # g0 was filtered, g3 is distractor
    assert result["actual"]["cmc_5"] == 1  # g1 is relevant at filtered rank 4


def test_map_cmc_json_string_input():
    from backend.evals.dataset import _score
    import json

    # Workflow may store ranked list as a JSON string
    ranked = json.dumps(["g1", "g3", "g5"])
    result = _score(_base_scorer_cfg(), row=_row(), final_state={"ranked_gallery_pids": ranked})
    assert result["actual"]["cmc_1"] == 1


def test_map_cmc_helpers_match_compute_map_cmc():
    """Filtered pairs + per-k helper must agree with ``_compute_map_cmc`` CMC bits."""
    from backend.evals.dataset import (
        _compute_map_cmc,
        map_cmc_filtered_ranked_pairs,
        map_cmc_hit_relevant_within_k,
    )

    row = _row()
    gallery_ids = row["gallery_ids"]
    gallery_pids = row["gallery_pids"]
    gallery_camids = row["gallery_camids"]
    query_pid = row["query_pid"]
    query_camid = row["query_camid"]
    ranked_lists = (
        ["g0", "g1", "g3", "g4", "g5", "g6", "g7", "g2"],
        ["g3", "g4", "g5", "g6", "g1", "g2", "g7"],
        ["g3", "g4", "g5", "g6", "g7"],
        ["g0", "g3", "g4", "g5", "g1"],
    )
    ks = [1, 5, 10, 20]
    for ranked in ranked_lists:
        scored = _compute_map_cmc(
            ranked,
            gallery_ids=gallery_ids,
            gallery_pids=gallery_pids,
            gallery_camids=gallery_camids,
            query_pid=query_pid,
            query_camid=query_camid,
            cmc_ks=ks,
        )
        _, filt = map_cmc_filtered_ranked_pairs(
            ranked,
            gallery_ids=gallery_ids,
            gallery_pids=gallery_pids,
            gallery_camids=gallery_camids,
            query_pid=query_pid,
            query_camid=query_camid,
        )
        for k in ks:
            hit = map_cmc_hit_relevant_within_k(filt, query_pid=query_pid, k=k)
            assert int(scored[f"cmc_{k}"]) == int(hit)


def test_map_cmc_via_dataset_eval(monkeypatch, tmp_path: Path):
    eval_dir = tmp_path / "evals" / "reid"
    eval_dir.mkdir(parents=True)
    (eval_dir / "data.yaml").write_text(
        yaml.safe_dump([{
            "query_id": "q0001_c1s1.jpg",
            "query_pid": 1,
            "query_camid": 1,
            "gallery_ids": _GALLERY_IDS,
            "gallery_pids": _GALLERY_PIDS,
            "gallery_camids": _GALLERY_CAMIDS,
        }]),
        encoding="utf-8",
    )
    config_path = eval_dir / "dataset_eval.yaml"
    config_path.write_text(
        yaml.safe_dump({
            "dataset_path": "data.yaml",
            "input_mapping": {
                "user_input": "query_id",
                "gallery_ids": "gallery_ids",
                "query_pid": "query_pid",
                "query_camid": "query_camid",
                "gallery_pids": "gallery_pids",
                "gallery_camids": "gallery_camids",
            },
            "scorers": [{
                "id": "market1501_map_cmc",
                "type": "map_cmc",
                "actual": "final_state.ranked_gallery_pids",
                "gallery_ids": "row.gallery_ids",
                "gallery_pids": "row.gallery_pids",
                "gallery_camids": "row.gallery_camids",
                "query_pid": "row.query_pid",
                "query_camid": "row.query_camid",
                "cmc_ks": [1, 5, 10],
            }],
        }),
        encoding="utf-8",
    )

    def _fake_batch(workflow, items, **kwargs):
        # Return g1 first → perfect rank-1 hit
        return [_fake_result(tmp_path, final_state={"ranked_gallery_pids": ["g0", "g1", "g3"]})]

    monkeypatch.setattr(dataset, "run_workflow_batch", _fake_batch)
    out = run_dataset_eval(workflow="reid", config_path=config_path, runs_root=tmp_path / "runs")
    assert out["status"] == "ok"
    scorer_out = out["results"][0]["scorers"][0]
    assert scorer_out["type"] == "map_cmc"
    assert scorer_out["actual"]["cmc_1"] == 1
    assert scorer_out["pass"] is True


def test_market1501_dataset_builder_uses_gallery_db_and_full_scoring_metadata(tmp_path: Path):
    from backend.repo_root import resolve_dataset_path_str
    from evals.person_reid_market1501.build_dataset import build_dataset

    market_root = tmp_path / "Market-1501"
    query_dir = market_root / "query"
    gallery_dir = market_root / "bounding_box_test"
    query_dir.mkdir(parents=True)
    gallery_dir.mkdir(parents=True)

    for index in range(100):
        (query_dir / f"{index + 1:04d}_c1s1_{index:06d}_00.jpg").write_bytes(b"query")
    gallery_specs = [
        ("0001_c2s1_000001_00.jpg", 1, 2),
        ("0002_c1s1_000002_00.jpg", 2, 1),
        ("0003_c3s1_000003_00.jpg", 3, 3),
    ]
    for filename, _pid, _camid in gallery_specs:
        (gallery_dir / filename).write_bytes(b"gallery")

    gallery_db = tmp_path / "attributes.sqlite"
    query_db = tmp_path / "query.sqlite"
    gallery_embedding_db = tmp_path / "gallery_embeddings.sqlite"
    query_embedding_db = tmp_path / "query_embeddings.sqlite"
    rows = build_dataset(
        market1501_root=market_root,
        n_queries=100,
        seed=7,
        gallery_db_path=gallery_db,
        query_db_path=query_db,
        gallery_embedding_db_path=gallery_embedding_db,
        query_embedding_db_path=query_embedding_db,
        retrieval_top_k=25,
    )

    assert len(rows) == 100
    assert resolve_dataset_path_str(rows[0]["query_image_path"]).endswith(".jpg")
    assert Path(resolve_dataset_path_str(rows[0]["query_image_path"])).is_absolute()
    assert resolve_dataset_path_str(rows[0]["query_db_path"]) == query_db.resolve().as_posix()
    assert resolve_dataset_path_str(rows[0]["gallery_db_path"]) == gallery_db.resolve().as_posix()
    assert resolve_dataset_path_str(rows[0]["query_embedding_db_path"]) == query_embedding_db.resolve().as_posix()
    assert resolve_dataset_path_str(rows[0]["gallery_embedding_db_path"]) == gallery_embedding_db.resolve().as_posix()
    assert rows[0]["retrieval_top_k"] == 25
    assert rows[0]["gallery_ids"] == [item[0] for item in gallery_specs]
    assert rows[0]["gallery_pids"] == [item[1] for item in gallery_specs]
    assert rows[0]["gallery_camids"] == [item[2] for item in gallery_specs]


def test_market1501_partition_builder_writes_100_query_500_gallery_layout(tmp_path: Path):
    from evals.person_reid_market1501.build_partition import build_partition

    market_root = tmp_path / "Market-1501"
    query_dir = market_root / "query"
    gallery_dir = market_root / "bounding_box_test"
    query_dir.mkdir(parents=True)
    gallery_dir.mkdir(parents=True)

    for index in range(100):
        pid = index + 1
        (query_dir / f"{pid:04d}_c1s1_{index:06d}_00.jpg").write_bytes(b"query")
        (gallery_dir / f"{pid:04d}_c2s1_{index:06d}_00.jpg").write_bytes(b"positive")
    for index in range(400):
        pid = index + 1001
        (gallery_dir / f"{pid:04d}_c3s1_{index:06d}_00.jpg").write_bytes(b"negative")

    output = build_partition(
        market1501_root=market_root,
        output_dir=tmp_path / "partition",
        n_queries=100,
        gallery_size=500,
        seed=42,
    )

    assert len(list((output / "query").glob("*.jpg"))) == 100
    assert len(list((output / "bounding_box_test").glob("*.jpg"))) == 500
    rows = yaml.safe_load((output / "dataset.yaml").read_text(encoding="utf-8"))
    assert len(rows) == 100
    assert len(rows[0]["gallery_ids"]) == 500
    assert len(rows[0]["gallery_pids"]) == 500
    assert len(rows[0]["gallery_camids"]) == 500
    assert rows[0]["query_embedding_db_path"].endswith("query_embeddings.sqlite")
    assert rows[0]["gallery_embedding_db_path"].endswith("gallery_embeddings.sqlite")
    assert rows[0]["query_description_db_path"].endswith("query_descriptions.sqlite")
    assert rows[0]["gallery_description_db_path"].endswith("gallery_descriptions.sqlite")
    assert "query_db_path" not in rows[0]
    assert "gallery_db_path" not in rows[0]
    for row in rows:
        assert any(
            pid == row["query_pid"] and camid != row["query_camid"]
            for pid, camid in zip(row["gallery_pids"], row["gallery_camids"])
        )


def test_market1501_gallery_index_writes_manifest_embeddings_and_id_map(tmp_path: Path):
    from evals.person_reid_market1501.build_dataset import build_gallery_index

    market_root = tmp_path / "Market-1501"
    gallery_dir = market_root / "bounding_box_test"
    gallery_dir.mkdir(parents=True)
    (gallery_dir / "0001_c2s1_000001_00.jpg").write_bytes(b"gallery-one")
    (gallery_dir / "0002_c1s1_000002_00.jpg").write_bytes(b"gallery-two")

    paths = build_gallery_index(market1501_root=market_root, output_dir=tmp_path / "index")

    manifest = yaml.safe_load(paths["manifest"].read_text(encoding="utf-8"))
    embeddings = [
        json.loads(line)
        for line in paths["embeddings"].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    id_to_row = yaml.safe_load(paths["id_to_row"].read_text(encoding="utf-8"))
    assert [item["id"] for item in manifest] == [
        "0001_c2s1_000001_00.jpg",
        "0002_c1s1_000002_00.jpg",
    ]
    assert [item["id"] for item in embeddings] == [item["id"] for item in manifest]
    assert len(embeddings[0]["embedding"]) == 32
    assert id_to_row == {
        "0001_c2s1_000001_00.jpg": 0,
        "0002_c1s1_000002_00.jpg": 1,
    }


def test_market1501_gallery_db_builder_stores_attributes_and_skips_invalid(tmp_path: Path):
    from evals.person_reid_market1501.build_gallery_db import build_gallery_db

    market_root = tmp_path / "Market-1501"
    gallery_dir = market_root / "bounding_box_test"
    gallery_dir.mkdir(parents=True)
    (gallery_dir / "0001_c2s1_000001_00.jpg").write_bytes(b"gallery-one")
    (gallery_dir / "0002_c1s1_000002_00.jpg").write_bytes(b"gallery-two")
    (gallery_dir / "-1_c1s1_000401_03.jpg").write_bytes(b"junk")

    def _extractor(path: Path) -> dict[str, str]:
        return _reid_attrs(gender="male" if path.name.startswith("0001") else "female")

    db_path = build_gallery_db(
        market1501_root=market_root,
        output=tmp_path / "gallery.sqlite",
        max_concurrency=2,
        attribute_extractor=_extractor,
    )

    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            """
            SELECT gallery_id, pid, camid, gender, clothing_type, hair
            FROM gallery_attributes
            ORDER BY gallery_id
            """
        ).fetchall()
    assert rows == [
        ("0001_c2s1_000001_00.jpg", 1, 2, "male", "pants", "short"),
        ("0002_c1s1_000002_00.jpg", 2, 1, "female", "pants", "short"),
    ]


def test_market1501_gallery_db_builder_requires_key_for_live_build(monkeypatch, tmp_path: Path):
    from evals.person_reid_market1501.build_gallery_db import build_gallery_db

    market_root = tmp_path / "Market-1501"
    gallery_dir = market_root / "bounding_box_test"
    gallery_dir.mkdir(parents=True)
    (gallery_dir / "0001_c2s1_000001_00.jpg").write_bytes(b"gallery-one")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr("evals.person_reid_market1501.build_gallery_db.load_dotenv", lambda: None)

    try:
        build_gallery_db(market1501_root=market_root, output=tmp_path / "gallery.sqlite")
    except RuntimeError as exc:
        assert "OPENROUTER_API_KEY" in str(exc)
    else:
        raise AssertionError("expected missing OPENROUTER_API_KEY to fail")


def test_embedding_dbs_builder_builds_query_and_gallery_dbs(tmp_path: Path):
    from evals.person_reid_market1501.build_embedding_dbs import DEFAULT_MODEL, build_embedding_dbs

    partition = tmp_path / "partition"
    query_dir = partition / "query"
    gallery_dir = partition / "bounding_box_test"
    query_dir.mkdir(parents=True)
    gallery_dir.mkdir(parents=True)
    (query_dir / "0001_c1s1_000001_00.jpg").write_bytes(b"query")
    (gallery_dir / "0001_c2s1_000001_00.jpg").write_bytes(b"gallery")
    (gallery_dir / "-1_c1s1_000401_03.jpg").write_bytes(b"junk")

    def _extractor(path: Path) -> list[float]:
        return [1.0, 0.0] if path.name.startswith("0001") else [0.0, 1.0]

    outputs = build_embedding_dbs(
        partition_root=partition,
        output_dir=tmp_path / "embedding_db",
        max_concurrency=2,
        model=DEFAULT_MODEL,
        embedding_extractor=_extractor,
    )

    assert outputs["query"].name == "query_embeddings.sqlite"
    assert outputs["gallery"].name == "gallery_embeddings.sqlite"
    with sqlite3.connect(outputs["query"]) as con:
        query_rows = con.execute("SELECT image_id, embedding_json FROM image_embeddings").fetchall()
    with sqlite3.connect(outputs["gallery"]) as con:
        gallery_rows = con.execute("SELECT gallery_id, embedding_json FROM gallery_embeddings").fetchall()
    assert query_rows == [("0001_c1s1_000001_00.jpg", "[1.0, 0.0]")]
    assert gallery_rows == [("0001_c2s1_000001_00.jpg", "[1.0, 0.0]")]


def _stub_description_record() -> dict:
    return {
        "description": "person in casual clothes",
        "facets": {
            "upper_body": "blue jacket long sleeve",
            "lower_body": "black pants long",
            "shoes": "white sneakers",
            "head_hair": "short hair",
            "carried_items": ["backpack"],
            "gender_presentation": "male",
            "age_presentation": "young adult",
            "pose_view": "front",
            "distinctive_marks": [],
            "uncertainties": [],
        },
    }


def test_description_dbs_builder_builds_query_and_gallery_dbs(tmp_path: Path):
    from evals.person_reid_market1501.build_description_dbs import (
        DEFAULT_DESCRIPTION_MODEL,
        DEFAULT_EMBEDDING_MODEL,
        build_description_dbs,
    )

    partition = tmp_path / "partition"
    query_dir = partition / "query"
    gallery_dir = partition / "bounding_box_test"
    query_dir.mkdir(parents=True)
    gallery_dir.mkdir(parents=True)
    (query_dir / "0001_c1s1_000001_00.jpg").write_bytes(b"query")
    (gallery_dir / "0001_c2s1_000001_00.jpg").write_bytes(b"gallery")
    (gallery_dir / "-1_c1s1_000401_03.jpg").write_bytes(b"junk")

    def _description_extractor(path: Path) -> dict:
        return _stub_description_record()

    def _embedding_extractor(text: str) -> list[float]:
        return [1.0, 0.0] if "casual" in text else [0.0, 1.0]

    outputs = build_description_dbs(
        partition_root=partition,
        output_dir=tmp_path / "description_db",
        max_concurrency=2,
        description_model=DEFAULT_DESCRIPTION_MODEL,
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        description_extractor=_description_extractor,
        embedding_extractor=_embedding_extractor,
    )

    assert outputs["query"].name == "query_descriptions.sqlite"
    assert outputs["gallery"].name == "gallery_descriptions.sqlite"

    with sqlite3.connect(outputs["query"]) as con:
        query_rows = con.execute(
            "SELECT image_id, description, facets_json, tokens_json FROM image_descriptions"
        ).fetchall()
        query_emb_rows = con.execute(
            "SELECT image_id, embedding_json FROM image_embeddings"
        ).fetchall()
    assert query_rows[0][0] == "0001_c1s1_000001_00.jpg"
    assert "casual" in query_rows[0][1]
    facets = json.loads(query_rows[0][2])
    tokens = json.loads(query_rows[0][3])
    assert facets["gender_presentation"] == "male"
    assert tokens["upper_body"]["colors"] == ["blue"]
    assert tokens["upper_body"]["garments"] == ["jacket"]
    assert tokens["lower_body"]["garments"] == ["pants"]
    assert tokens["carried_items"] == ["backpack"]
    assert query_emb_rows == [("0001_c1s1_000001_00.jpg", "[1.0, 0.0]")]

    with sqlite3.connect(outputs["gallery"]) as con:
        gallery_emb_rows = con.execute(
            "SELECT gallery_id, embedding_json FROM gallery_embeddings"
        ).fetchall()
        gallery_desc_rows = con.execute(
            "SELECT gallery_id, description FROM gallery_descriptions"
        ).fetchall()
    assert gallery_emb_rows == [("0001_c2s1_000001_00.jpg", "[1.0, 0.0]")]
    assert gallery_desc_rows[0][0] == "0001_c2s1_000001_00.jpg"


def test_description_db_builder_requires_key_for_live_build(monkeypatch, tmp_path: Path):
    from evals.person_reid_market1501 import build_description_dbs as module

    image_dir = tmp_path / "query"
    image_dir.mkdir()
    (image_dir / "0001_c1s1_000001_00.jpg").write_bytes(b"query")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(module, "load_dotenv", lambda: None)
    captured: dict[str, str] = {}

    def _should_not_call(**kwargs):
        captured["model"] = kwargs.get("model")
        raise AssertionError("provider should not be called without key")

    monkeypatch.setattr(module, "call_provider", _should_not_call)
    monkeypatch.setattr(module, "call_embedding_provider", _should_not_call)

    try:
        module.build_description_db(
            image_dir=image_dir,
            output=tmp_path / "query.sqlite",
            is_gallery=False,
        )
    except RuntimeError as exc:
        assert "OPENROUTER_API_KEY" in str(exc)
    else:
        raise AssertionError("expected missing OPENROUTER_API_KEY to fail")
    assert captured == {}


def test_description_db_builder_uses_default_vision_model(monkeypatch, tmp_path: Path):
    from backend.providers.base import EmbeddingResponse, LLMResponse, Usage
    from evals.person_reid_market1501 import build_description_dbs as module

    image_dir = tmp_path / "query"
    image_dir.mkdir()
    (image_dir / "0001_c1s1_000001_00.jpg").write_bytes(b"query")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(module, "load_dotenv", lambda: None)
    captured_models: list[str] = []

    def _fake_call_provider(_provider: str, **kwargs):
        captured_models.append(kwargs["model"])
        return LLMResponse(
            text=json.dumps(_stub_description_record()),
            usage=Usage(1, 1),
            model=kwargs["model"],
        )

    def _fake_call_embedding_provider(_provider: str, **kwargs):
        captured_models.append(kwargs["model"])
        return EmbeddingResponse(
            embedding=[0.1, 0.2],
            usage=Usage(1, 0),
            model=kwargs["model"],
        )

    monkeypatch.setattr(module, "call_provider", _fake_call_provider)
    monkeypatch.setattr(module, "call_embedding_provider", _fake_call_embedding_provider)
    module.build_description_db(
        image_dir=image_dir,
        output=tmp_path / "query.sqlite",
        is_gallery=False,
        max_concurrency=1,
    )
    assert captured_models[0] == "google/gemma-4-31b-it"
    assert captured_models[1] == "google/gemini-embedding-2-preview"


def test_reid_pt_importer_writes_platform_embedding_schema(tmp_path: Path):
    from evals.person_reid_market1501.import_reid_embedding_pts import extract_reid_rows, write_embedding_db

    payload = {
        "items": [
            "/data/query/0001_c1s1_000001_00.jpg",
            "/data/query/0002_c2s1_000001_00.jpg",
        ],
        "embeddings": [
            [1.0, 0.0],
            [0.0, 1.0],
        ],
    }
    rows = extract_reid_rows(payload, source="torchreid")
    output = write_embedding_db(rows=rows, output=tmp_path / "query.sqlite", split="query")

    with sqlite3.connect(output) as con:
        image_rows = con.execute(
            "SELECT image_id, image_path, pid, camid, embedding_json FROM image_embeddings ORDER BY image_id"
        ).fetchall()
        view_rows = con.execute("SELECT query_id, embedding_json FROM query_embeddings ORDER BY query_id").fetchall()

    assert image_rows == [
        ("0001_c1s1_000001_00.jpg", "/data/query/0001_c1s1_000001_00.jpg", 1, 1, "[1.0, 0.0]"),
        ("0002_c2s1_000001_00.jpg", "/data/query/0002_c2s1_000001_00.jpg", 2, 2, "[0.0, 1.0]"),
    ]
    assert view_rows == [
        ("0001_c1s1_000001_00.jpg", "[1.0, 0.0]"),
        ("0002_c2s1_000001_00.jpg", "[0.0, 1.0]"),
    ]


def test_reid_retrieval_score_db_builder_matches_eval_lookup_format(tmp_path: Path):
    from evals.person_reid_market1501.build_reid_retrieval_score_db import (
        DESCRIPTION_CHANNEL_MAP,
        VISUAL_FASTREID_CHANNEL,
        VISUAL_TORCHREID_CHANNEL,
        build_reid_retrieval_score_db,
    )
    from evals.person_reid_market1501.import_reid_embedding_pts import write_embedding_db

    torch_query = write_embedding_db(
        rows=[("0001_c1s1_000001_00.jpg", "/q/0001_c1s1_000001_00.jpg", [1.0, 0.0])],
        output=tmp_path / "torch_query.sqlite",
        split="query",
    )
    torch_gallery = write_embedding_db(
        rows=[
            ("0001_c2s1_000001_00.jpg", "/g/0001_c2s1_000001_00.jpg", [1.0, 0.0]),
            ("0002_c2s1_000001_00.jpg", "/g/0002_c2s1_000001_00.jpg", [0.0, 1.0]),
        ],
        output=tmp_path / "torch_gallery.sqlite",
        split="gallery",
    )
    fast_query = write_embedding_db(
        rows=[("0001_c1s1_000001_00.jpg", "/q/0001_c1s1_000001_00.jpg", [0.0, 1.0])],
        output=tmp_path / "fast_query.sqlite",
        split="query",
    )
    fast_gallery = write_embedding_db(
        rows=[
            ("0001_c2s1_000001_00.jpg", "/g/0001_c2s1_000001_00.jpg", [1.0, 0.0]),
            ("0002_c2s1_000001_00.jpg", "/g/0002_c2s1_000001_00.jpg", [0.0, 1.0]),
        ],
        output=tmp_path / "fast_gallery.sqlite",
        split="gallery",
    )

    description_source = tmp_path / "descriptions.sqlite"
    with sqlite3.connect(description_source) as con:
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
        con.executemany(
            "INSERT INTO retrieval_rankings VALUES (?, ?, ?, ?, ?)",
            [
                ("0001_c1s1_000001_00.jpg", "visual_image_embedding", 1, "should_not_copy.jpg", 0.99),
                ("0001_c1s1_000001_00.jpg", "description_semantic_text", 1, "0002_c2s1_000001_00.jpg", 0.8),
                ("0001_c1s1_000001_00.jpg", "description_structured_facets", 1, "0001_c2s1_000001_00.jpg", 3.0),
            ],
        )

    output = build_reid_retrieval_score_db(
        output=tmp_path / "rankings.sqlite",
        top_k=2,
        torchreid_query_db=torch_query,
        torchreid_gallery_db=torch_gallery,
        fastreid_query_db=fast_query,
        fastreid_gallery_db=fast_gallery,
        description_source_db=description_source,
        include_descriptions=True,
    )

    with sqlite3.connect(output) as con:
        channels = {
            row[0] for row in con.execute("SELECT DISTINCT channel FROM retrieval_rankings ORDER BY channel")
        }
        torch_ranked = con.execute(
            "SELECT rank, gallery_id FROM retrieval_rankings WHERE channel = ? ORDER BY rank",
            (VISUAL_TORCHREID_CHANNEL,),
        ).fetchall()
        fast_ranked = con.execute(
            "SELECT rank, gallery_id FROM retrieval_rankings WHERE channel = ? ORDER BY rank",
            (VISUAL_FASTREID_CHANNEL,),
        ).fetchall()
        copied_visual = con.execute(
            "SELECT COUNT(*) FROM retrieval_rankings WHERE gallery_id = 'should_not_copy.jpg'"
        ).fetchone()[0]
        metadata_channels = {
            row[0] for row in con.execute("SELECT channel FROM retrieval_channels ORDER BY channel")
        }
        json_rows = con.execute(
            "SELECT ranked_gallery_ids_json FROM retrieval_rankings_json WHERE channel = ?",
            (VISUAL_TORCHREID_CHANNEL,),
        ).fetchall()

    assert VISUAL_TORCHREID_CHANNEL in channels
    assert VISUAL_FASTREID_CHANNEL in channels
    assert DESCRIPTION_CHANNEL_MAP["description_semantic_text"] in channels
    assert DESCRIPTION_CHANNEL_MAP["description_structured_facets"] in channels
    assert "visual_image_embedding" not in channels
    assert torch_ranked[0] == (1, "0001_c2s1_000001_00.jpg")
    assert fast_ranked[0] == (1, "0002_c2s1_000001_00.jpg")
    assert copied_visual == 0
    assert channels <= metadata_channels
    assert json_rows
