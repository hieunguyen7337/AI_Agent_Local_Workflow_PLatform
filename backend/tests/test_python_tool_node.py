"""Tests for python_tool node kind: spec validation, allowlist, runtime, executor wiring."""
from __future__ import annotations

import importlib
import inspect
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

import backend.builder.python_tool_allowlist as _allowlist_mod
from backend.builder.nodes import PythonToolNodeConfig
from backend.builder.python_tool_allowlist import _clear_cache, load_allowlist
from backend.graphspec import GraphSpec
from backend.runtime.nodes.python_tool import _resolve_callable, make_python_tool_node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _with_allowlist(callables: list[str]):
    """Monkeypatch load_allowlist to return the given set."""
    return patch.object(_allowlist_mod, "_cache", frozenset(callables))


def _make_cfg(callable_path: str, inputs: dict | None = None, output_state_key: str = "result") -> PythonToolNodeConfig:
    # _with_allowlist patches _cache directly; do NOT call _clear_cache() inside it
    with _with_allowlist([callable_path]):
        return PythonToolNodeConfig(
            id="test_node",
            kind="python_tool",
            callable_path=callable_path,
            inputs=inputs or {},
            output_state_key=output_state_key,
        )


def _dummy_func(x: str, y: str) -> str:
    return f"{x}+{y}"


def _dummy_list_func(items: list) -> list:
    return list(reversed(items))


# ---------------------------------------------------------------------------
# Spec validation
# ---------------------------------------------------------------------------

def test_python_tool_config_accepted_when_allowlisted():
    _clear_cache()
    with _with_allowlist(["mymodule.my_func"]):
        cfg = PythonToolNodeConfig(
            id="n1",
            callable_path="mymodule.my_func",
            inputs={"a": "user_input"},
            output_state_key="out",
        )
    assert cfg.callable_path == "mymodule.my_func"
    assert cfg.inputs == {"a": "user_input"}
    assert cfg.output_state_key == "out"


def test_python_tool_config_rejected_when_not_allowlisted():
    _clear_cache()
    with _with_allowlist([]):
        with pytest.raises(ValidationError, match="allowlist"):
            PythonToolNodeConfig(
                id="n1",
                callable_path="evil.module.bad",
                inputs={},
                output_state_key="out",
            )


def test_python_tool_config_missing_callable_path_rejected():
    _clear_cache()
    with _with_allowlist(["mymodule.my_func"]):
        with pytest.raises(ValidationError):
            PythonToolNodeConfig(
                id="n1",
                callable_path="",
                inputs={},
                output_state_key="out",
            )


def test_python_tool_config_missing_output_state_key_rejected():
    _clear_cache()
    with _with_allowlist(["mymodule.my_func"]):
        with pytest.raises(ValidationError):
            PythonToolNodeConfig(
                id="n1",
                callable_path="mymodule.my_func",
                inputs={},
                output_state_key="",
            )


# ---------------------------------------------------------------------------
# GraphSpec round-trip
# ---------------------------------------------------------------------------

def test_graphspec_accepts_python_tool_node():
    _clear_cache()
    with _with_allowlist(["backend.tools.reid_specialists.weighted_reciprocal_rank_fusion"]):
        spec = GraphSpec.model_validate({
            "name": "reid_test",
            "budget": {"cost_usd": 0.5, "latency_ms": 30000},
            "entry": "fusion",
            "nodes": [
                {
                    "id": "fusion",
                    "kind": "python_tool",
                    "callable_path": "backend.tools.reid_specialists.weighted_reciprocal_rank_fusion",
                    "inputs": {
                        "reid_multimodal_embedding_ranked": "reid_multimodal_embedding_ranked",
                        "description_semantic_ranked": "description_semantic_ranked",
                        "description_facets_ranked": "description_facets_ranked",
                    },
                    "output_state_key": "ranked_gallery_ids",
                }
            ],
            "edges": [{"from": "fusion", "to": "__end__"}],
        })
    node = spec.nodes[0]
    assert node.kind == "python_tool"
    assert node.callable_path == "backend.tools.reid_specialists.weighted_reciprocal_rank_fusion"
    assert node.inputs == {
        "reid_multimodal_embedding_ranked": "reid_multimodal_embedding_ranked",
        "description_semantic_ranked": "description_semantic_ranked",
        "description_facets_ranked": "description_facets_ranked",
    }
    assert node.output_state_key == "ranked_gallery_ids"


def test_graphspec_rejects_unknown_callable():
    _clear_cache()
    with _with_allowlist([]):  # empty allowlist
        with pytest.raises((ValidationError, ValueError)):
            GraphSpec.model_validate({
                "name": "bad",
                "budget": {"cost_usd": 0.1, "latency_ms": 1000},
                "entry": "n",
                "nodes": [
                    {
                        "id": "n",
                        "kind": "python_tool",
                        "callable_path": "evil.module",
                        "output_state_key": "out",
                    }
                ],
                "edges": [{"from": "n", "to": "__end__"}],
            })


# ---------------------------------------------------------------------------
# Allowlist loader
# ---------------------------------------------------------------------------

def test_load_allowlist_returns_entries_from_file(tmp_path: Path):
    _clear_cache()
    allowlist_file = tmp_path / "python_tools.yaml"
    allowlist_file.write_text(
        "allowed_callables:\n  - a.b.c\n  - x.y.z\n", encoding="utf-8"
    )
    with patch.object(_allowlist_mod, "_ALLOWLIST_PATH", allowlist_file):
        _clear_cache()
        result = load_allowlist()
    assert "a.b.c" in result
    assert "x.y.z" in result


def test_load_allowlist_empty_when_file_missing(tmp_path: Path):
    _clear_cache()
    with patch.object(_allowlist_mod, "_ALLOWLIST_PATH", tmp_path / "nonexistent.yaml"):
        _clear_cache()
        result = load_allowlist()
    assert result == frozenset()


def test_load_allowlist_cached():
    _clear_cache()
    with _with_allowlist(["a.b"]):
        r1 = load_allowlist()
        r2 = load_allowlist()
    assert r1 is r2  # same frozenset object (cache hit)


def test_allowlisted_python_tools_do_not_call_hosted_model_apis():
    _clear_cache()
    forbidden = ("httpx.", "requests.", "openrouter.ai/api", "call_provider", "stream_provider")
    for callable_path in load_allowlist():
        module_name, func_name = callable_path.rsplit(".", 1)
        func = getattr(importlib.import_module(module_name), func_name)
        source = inspect.getsource(func)
        assert not any(token in source for token in forbidden), callable_path


# ---------------------------------------------------------------------------
# Runtime factory
# ---------------------------------------------------------------------------

def test_make_python_tool_node_executes_func():
    callable_path = f"{__name__}._dummy_func"
    _clear_cache()
    with _with_allowlist([callable_path]):
        cfg = _make_cfg(callable_path, inputs={"x": "a", "y": "b"}, output_state_key="out")
        node = make_python_tool_node(cfg, run_id="r1", graph_name="g")
        result = node({"a": "hello", "b": "world"})
    assert result == {"out": "hello+world"}


def test_make_python_tool_node_list_output():
    callable_path = f"{__name__}._dummy_list_func"
    _clear_cache()
    with _with_allowlist([callable_path]):
        cfg = _make_cfg(callable_path, inputs={"items": "gallery_ids"}, output_state_key="ranked")
        node = make_python_tool_node(cfg, run_id="r2", graph_name="g")
        result = node({"gallery_ids": ["a", "b", "c"]})
    assert result == {"ranked": ["c", "b", "a"]}


def test_make_python_tool_node_propagates_exception():
    def _bad(): raise ValueError("intentional failure")
    callable_path = f"{__name__}._bad_func_placeholder"
    _clear_cache()

    with _with_allowlist([callable_path]):
        cfg = _make_cfg(callable_path, inputs={}, output_state_key="out")
        # Patch the resolve step to return our bad function
        with patch("backend.runtime.nodes.python_tool._resolve_callable", return_value=_bad):
            node = make_python_tool_node(cfg, run_id="r3", graph_name="g")
            with pytest.raises(ValueError, match="intentional failure"):
                node({})


def test_make_python_tool_node_captures_stdout(capsys):
    def _noisy(x: str) -> str:
        print(f"processing {x}")
        return x.upper()

    callable_path = f"{__name__}._noisy_func_placeholder"
    _clear_cache()
    with _with_allowlist([callable_path]):
        cfg = _make_cfg(callable_path, inputs={"x": "inp"}, output_state_key="out")
        with patch("backend.runtime.nodes.python_tool._resolve_callable", return_value=_noisy):
            node = make_python_tool_node(cfg, run_id="r4", graph_name="g")
            result = node({"inp": "hello"})
    # stdout captured by the node — should NOT appear in test stdout
    captured = capsys.readouterr()
    assert "processing" not in captured.out
    assert result == {"out": "HELLO"}


def test_resolve_callable_rejects_non_allowlisted():
    _clear_cache()
    with _with_allowlist([]):
        with pytest.raises(PermissionError, match="allowlist"):
            _resolve_callable("os.system")


def test_resolve_callable_rejects_non_importable():
    callable_path = "totally.nonexistent.module.func"
    _clear_cache()
    with _with_allowlist([callable_path]):
        with pytest.raises(ModuleNotFoundError):
            _resolve_callable(callable_path)


# ---------------------------------------------------------------------------
# ReID description and embedding retriever functions
# ---------------------------------------------------------------------------

import json as _json


def _make_gallery_description_db(path: Path) -> Path:
    """Build a small gallery DB with description rows and a `gallery_descriptions` view.

    Tokens are precomputed deterministically per row so scoring is stable.
    """
    rows = [
        # gallery_id, pid, camid, description, facets, tokens
        (
            "g1.jpg", 1, 1,
            "man in blue jacket and black pants with a backpack",
            {
                "upper_body": "blue jacket long sleeve",
                "lower_body": "black pants long",
                "shoes": "white sneakers",
                "head_hair": "short hair no hat",
                "carried_items": ["backpack"],
                "gender_presentation": "male",
                "age_presentation": "young adult",
            },
            {
                "upper_body": {"colors": ["blue"], "garments": ["jacket"], "sleeves": ["long sleeve"], "patterns": []},
                "lower_body": {"colors": ["black"], "garments": ["pants"], "lengths": ["long"], "patterns": []},
                "shoes": {"colors": ["white"], "styles": ["sneakers"]},
                "head_hair": {"hair": ["short"], "headwear": []},
                "carried_items": ["backpack"],
                "distinctive_marks": [],
                "gender_presentation": "male",
                "age_presentation": "young adult",
            },
        ),
        (
            "g2.jpg", 2, 2,
            "woman in red dress with a handbag",
            {
                "upper_body": "red dress short sleeve",
                "lower_body": "red dress long",
                "shoes": "black heels",
                "head_hair": "long hair no hat",
                "carried_items": ["handbag"],
                "gender_presentation": "female",
                "age_presentation": "adult",
            },
            {
                "upper_body": {"colors": ["red"], "garments": ["dress"], "sleeves": ["short sleeve"], "patterns": []},
                "lower_body": {"colors": ["red"], "garments": [], "lengths": ["long"], "patterns": []},
                "shoes": {"colors": ["black"], "styles": ["heels"]},
                "head_hair": {"hair": ["long"], "headwear": []},
                "carried_items": ["handbag"],
                "distinctive_marks": [],
                "gender_presentation": "female",
                "age_presentation": "adult",
            },
        ),
        (
            "g3.jpg", 3, 3,
            "man in blue jacket and black jeans with a backpack",
            {
                "upper_body": "blue jacket long sleeve",
                "lower_body": "black jeans long",
                "shoes": "black sneakers",
                "head_hair": "short hair no hat",
                "carried_items": ["backpack"],
                "gender_presentation": "male",
                "age_presentation": "adult",
            },
            {
                "upper_body": {"colors": ["blue"], "garments": ["jacket"], "sleeves": ["long sleeve"], "patterns": []},
                "lower_body": {"colors": ["black"], "garments": ["jeans"], "lengths": ["long"], "patterns": []},
                "shoes": {"colors": ["black"], "styles": ["sneakers"]},
                "head_hair": {"hair": ["short"], "headwear": []},
                "carried_items": ["backpack"],
                "distinctive_marks": [],
                "gender_presentation": "male",
                "age_presentation": "adult",
            },
        ),
    ]
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE image_descriptions (
                image_id TEXT PRIMARY KEY,
                image_path TEXT NOT NULL,
                pid INTEGER NOT NULL,
                camid INTEGER NOT NULL,
                description TEXT NOT NULL,
                description_json TEXT NOT NULL,
                facets_json TEXT NOT NULL,
                tokens_json TEXT NOT NULL
            )
            """
        )
        con.executemany(
            """
            INSERT INTO image_descriptions (
                image_id, image_path, pid, camid,
                description, description_json, facets_json, tokens_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    gid, f"/gallery/{gid}", pid, camid,
                    description,
                    _json.dumps({"description": description, "facets": facets}, sort_keys=True),
                    _json.dumps(facets, sort_keys=True),
                    _json.dumps(tokens, sort_keys=True),
                )
                for gid, pid, camid, description, facets, tokens in rows
            ],
        )
        con.execute(
            """
            CREATE VIEW gallery_descriptions AS
            SELECT image_id AS gallery_id, image_path, pid, camid,
                   description, description_json, facets_json, tokens_json
            FROM image_descriptions
            """
        )
    return path


def _make_query_description_db(path: Path) -> Path:
    facets = {
        "upper_body": "blue jacket long sleeve",
        "lower_body": "black pants long",
        "shoes": "white sneakers",
        "head_hair": "short hair no hat",
        "carried_items": ["backpack"],
        "gender_presentation": "male",
        "age_presentation": "young adult",
        "pose_view": "front",
        "distinctive_marks": [],
        "uncertainties": [],
    }
    tokens = {
        "upper_body": {"colors": ["blue"], "garments": ["jacket"], "sleeves": ["long sleeve"], "patterns": []},
        "lower_body": {"colors": ["black"], "garments": ["pants"], "lengths": ["long"], "patterns": []},
        "shoes": {"colors": ["white"], "styles": ["sneakers"]},
        "head_hair": {"hair": ["short"], "headwear": []},
        "carried_items": ["backpack"],
        "distinctive_marks": [],
        "gender_presentation": "male",
        "age_presentation": "young adult",
    }
    description_record = {
        "description": "young man in blue jacket, black pants, white sneakers, with a backpack",
        "facets": facets,
    }
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE image_descriptions (
                image_id TEXT PRIMARY KEY,
                image_path TEXT NOT NULL,
                pid INTEGER NOT NULL,
                camid INTEGER NOT NULL,
                description TEXT NOT NULL,
                description_json TEXT NOT NULL,
                facets_json TEXT NOT NULL,
                tokens_json TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE image_embeddings (
                image_id TEXT PRIMARY KEY,
                image_path TEXT NOT NULL,
                pid INTEGER NOT NULL,
                camid INTEGER NOT NULL,
                embedding_json TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO image_descriptions (
                image_id, image_path, pid, camid,
                description, description_json, facets_json, tokens_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "q1.jpg",
                "/query/q1.jpg",
                1,
                1,
                description_record["description"],
                _json.dumps(description_record, sort_keys=True),
                _json.dumps(facets, sort_keys=True),
                _json.dumps(tokens, sort_keys=True),
            ),
        )
        con.execute(
            "INSERT INTO image_embeddings (image_id, image_path, pid, camid, embedding_json) VALUES (?, ?, ?, ?, ?)",
            ("q1.jpg", "/query/q1.jpg", 1, 1, "[0.4, 0.5, 0.6]"),
        )
    return path


def _make_query_embedding_db(path: Path) -> Path:
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE image_embeddings (
                image_id TEXT PRIMARY KEY,
                embedding_json TEXT NOT NULL
            )
            """
        )
        con.execute(
            "INSERT INTO image_embeddings (image_id, embedding_json) VALUES (?, ?)",
            ("q1.jpg", "[0.1, 0.2, 0.3]"),
        )
    return path


def test_lookup_query_description_from_eval_db_reads_query_db(tmp_path: Path):
    from backend.tools.reid_specialists import lookup_query_description_from_eval_db

    db_path = _make_query_description_db(tmp_path / "query.sqlite")
    result = lookup_query_description_from_eval_db("q1.jpg", str(db_path))
    assert "blue jacket" in result["description"]
    assert result["facets"]["gender_presentation"] == "male"
    assert result["facets"]["upper_body"] == "blue jacket long sleeve"
    assert result["tokens"]["upper_body"]["colors"] == ["blue"]
    assert result["tokens"]["carried_items"] == ["backpack"]


def test_lookup_query_description_embedding_from_eval_db_reads_embedding(tmp_path: Path):
    from backend.tools.reid_specialists import lookup_query_description_embedding_from_eval_db

    db_path = _make_query_description_db(tmp_path / "query.sqlite")
    assert lookup_query_description_embedding_from_eval_db("q1.jpg", str(db_path)) == [0.4, 0.5, 0.6]


def test_retrieve_gallery_by_description_facets_prefers_matching_facets(tmp_path: Path):
    from backend.tools.reid_specialists import retrieve_gallery_by_description_facets

    db_path = _make_gallery_description_db(tmp_path / "gallery.sqlite")
    query_record = {
        "description": "young man in blue jacket and black pants with a backpack",
        "facets": {
            "upper_body": "blue jacket long sleeve",
            "lower_body": "black pants long",
            "shoes": "white sneakers",
            "head_hair": "short hair no hat",
            "carried_items": ["backpack"],
            "gender_presentation": "male",
            "age_presentation": "young adult",
            "pose_view": "front",
            "distinctive_marks": [],
            "uncertainties": [],
        },
        "tokens": {
            "upper_body": {"colors": ["blue"], "garments": ["jacket"], "sleeves": ["long sleeve"], "patterns": []},
            "lower_body": {"colors": ["black"], "garments": ["pants"], "lengths": ["long"], "patterns": []},
            "shoes": {"colors": ["white"], "styles": ["sneakers"]},
            "head_hair": {"hair": ["short"], "headwear": []},
            "carried_items": ["backpack"],
            "distinctive_marks": [],
            "gender_presentation": "male",
            "age_presentation": "young adult",
        },
    }
    result = retrieve_gallery_by_description_facets(
        query_description=query_record,
        gallery_description_db_path=str(db_path),
        top_k=3,
    )
    # g1 matches everything (lower garment "pants" too); g3 matches color/garment/items but not lower garment;
    # g2 is a heavy mismatch (woman, red dress, handbag).
    assert result[0] == "g1.jpg"
    assert result[1] == "g3.jpg"
    assert result[2] == "g2.jpg"


def test_retrieve_gallery_by_description_facets_penalises_color_conflict(tmp_path: Path):
    from backend.tools.reid_specialists import retrieve_gallery_by_description_facets

    db_path = _make_gallery_description_db(tmp_path / "gallery.sqlite")
    # Query says "red upper" and "skirt lower". g1/g3 both conflict (blue upper); g2 partially matches red upper.
    query_record = {
        "description": "woman in red dress",
        "facets": {
            "upper_body": "red dress short sleeve",
            "lower_body": "red skirt long",
            "shoes": "black heels",
            "head_hair": "long hair",
            "carried_items": ["handbag"],
            "gender_presentation": "female",
            "age_presentation": "adult",
            "pose_view": "front",
            "distinctive_marks": [],
            "uncertainties": [],
        },
        "tokens": {
            "upper_body": {"colors": ["red"], "garments": ["dress"], "sleeves": ["short sleeve"], "patterns": []},
            "lower_body": {"colors": ["red"], "garments": ["skirt"], "lengths": ["long"], "patterns": []},
            "shoes": {"colors": ["black"], "styles": ["heels"]},
            "head_hair": {"hair": ["long"], "headwear": []},
            "carried_items": ["handbag"],
            "distinctive_marks": [],
            "gender_presentation": "female",
            "age_presentation": "adult",
        },
    }
    result = retrieve_gallery_by_description_facets(
        query_description=query_record,
        gallery_description_db_path=str(db_path),
        top_k=3,
    )
    # g2 should be ranked first (matches color, garment, items, gender)
    assert result[0] == "g2.jpg"


def test_lookup_query_reid_embedding_from_eval_db_reads_query_db(tmp_path: Path):
    from backend.tools.reid_specialists import lookup_query_reid_embedding_from_eval_db

    db_path = _make_query_embedding_db(tmp_path / "query_embeddings.sqlite")
    assert lookup_query_reid_embedding_from_eval_db("q1.jpg", str(db_path)) == [0.1, 0.2, 0.3]


def test_weighted_reciprocal_rank_fusion_uses_visual_heavy_defaults():
    from backend.tools.reid_specialists import weighted_reciprocal_rank_fusion

    # Defaults: visual=0.80 / desc_semantic=0.13 / desc_facets=0.07
    result = weighted_reciprocal_rank_fusion(
        reid_multimodal_embedding_ranked=["c", "a"],
        description_semantic_ranked=["b", "d"],
        description_facets_ranked=["d", "e"],
    )
    assert result[:2] == ["c", "a"]


def test_weighted_reciprocal_rank_fusion_supports_three_channels():
    from backend.tools.reid_specialists import weighted_reciprocal_rank_fusion

    result = weighted_reciprocal_rank_fusion(
        reid_multimodal_embedding_ranked=["x"],
        description_semantic_ranked=["y"],
        description_facets_ranked=["z"],
    )
    assert set(result[:3]) == {"x", "y", "z"}


def test_weighted_reciprocal_rank_fusion_respects_explicit_weights():
    from backend.tools.reid_specialists import weighted_reciprocal_rank_fusion

    # Description-heavy weights flip the order toward description channels.
    result = weighted_reciprocal_rank_fusion(
        reid_multimodal_embedding_ranked=["c", "a"],
        description_semantic_ranked=["b", "d"],
        description_facets_ranked=["b", "d"],
        fusion_weight_analysis='{"rrf_weights": {"reid_multimodal_embedding": 0.05, "description_semantic": 0.50, "description_facets": 0.45}}',
    )
    assert result[:2] == ["b", "d"]


def test_weighted_reciprocal_rank_fusion_zero_weight_drops_channel():
    from backend.tools.reid_specialists import weighted_reciprocal_rank_fusion

    result = weighted_reciprocal_rank_fusion(
        reid_multimodal_embedding_ranked=["a"],
        description_semantic_ranked=["b"],
        description_facets_ranked=["c"],
        fusion_weight_analysis='{"rrf_weights": {"reid_multimodal_embedding": 1.0, "description_semantic": 0.0, "description_facets": 0.0}}',
    )
    assert result == ["a"]


def test_decide_reid_fusion_weights_switches_when_description_agrees_and_visual_misses():
    from backend.tools.reid_specialists import decide_reid_fusion_weights

    result = decide_reid_fusion_weights(
        reid_multimodal_embedding_ranked=["v1", "v2", "v3"],
        description_semantic_ranked=["d1", "x", "y"],
        description_facets_ranked=["d1", "z", "w"],
    )
    assert result["decision"] == "description_heavy"
    assert result["rrf_weights"]["description_semantic"] > result["rrf_weights"]["reid_multimodal_embedding"]
    assert result["top_description_candidate"] == "d1"


def test_decide_reid_fusion_weights_keeps_default_when_visual_agrees():
    from backend.tools.reid_specialists import decide_reid_fusion_weights

    result = decide_reid_fusion_weights(
        reid_multimodal_embedding_ranked=["d1", "v2", "v3"],
        description_semantic_ranked=["d1", "x", "y"],
        description_facets_ranked=["d1", "z", "w"],
    )
    assert result["decision"] == "visual_heavy_default"
    assert result["rrf_weights"]["reid_multimodal_embedding"] == 0.80


def test_lookup_precomputed_retrieval_ranking_reads_channel_rows(tmp_path: Path):
    from backend.tools.reid_specialists import lookup_precomputed_retrieval_ranking

    db_path = tmp_path / "retrieval_scores.sqlite"
    with sqlite3.connect(db_path) as con:
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
                ("q1.jpg", "visual_image_embedding", 2, "g2.jpg", 0.8),
                ("q1.jpg", "visual_image_embedding", 1, "g1.jpg", 0.9),
                ("q1.jpg", "description_semantic_text", 1, "d1.jpg", 0.7),
            ],
        )

    assert lookup_precomputed_retrieval_ranking(
        query_id="q1.jpg",
        retrieval_score_db_path=str(db_path),
        channel="visual_image_embedding",
        top_k=2,
    ) == ["g1.jpg", "g2.jpg"]


def test_workflow_state_declares_description_keys():
    from backend.runtime.state import WorkflowState

    assert "ranked_gallery_ids" in WorkflowState.__annotations__
    assert "query_llm_description" in WorkflowState.__annotations__
    assert "query_description_embedding" in WorkflowState.__annotations__
    assert "description_facets_ranked" in WorkflowState.__annotations__
    assert "description_semantic_ranked" in WorkflowState.__annotations__
    assert "torchreid_visual_ranked" in WorkflowState.__annotations__
    assert "fastreid_visual_ranked" in WorkflowState.__annotations__
    assert "fusion_weight_analysis" in WorkflowState.__annotations__
    assert "fusion_weight_config" in WorkflowState.__annotations__
    assert "query_description_db_path" in WorkflowState.__annotations__
    assert "gallery_description_db_path" in WorkflowState.__annotations__
    assert "retrieval_score_db_path" in WorkflowState.__annotations__
    assert "retrieval_score_top_k" in WorkflowState.__annotations__
    assert "channel_torchreid_visual_embedding" in WorkflowState.__annotations__
    assert "channel_fastreid_visual_embedding" in WorkflowState.__annotations__
    assert "query_llm_attributes" not in WorkflowState.__annotations__
    assert "llm_attribute_ranked" not in WorkflowState.__annotations__


# ---------------------------------------------------------------------------
# person_reid_market1501 workflow spec loads without error
# ---------------------------------------------------------------------------

def test_person_reid_workflow_spec_loads():
    """Confirm the updated workflow YAML passes GraphSpec validation."""
    from backend.graphspec import load_graph_spec

    spec = load_graph_spec("person_reid_market1501")
    assert spec.name == "person_reid_market1501"
    node_kinds = {n.id: n.kind for n in spec.nodes}
    assert "llm_attribute_parser" not in node_kinds
    assert "llm_attribute_retriever" not in node_kinds
    assert "fusion_weight_analyser" not in node_kinds
    assert "alias_gallery_ids_for_ranker" not in node_kinds
    assert "final_ranker" not in node_kinds
    assert "parse_final_ranking" not in node_kinds
    assert "boss_orchestrator" not in node_kinds
    assert "rrf_precompute" not in node_kinds
    assert "reid_multimodal_embedding" not in node_kinds
    assert "reid_multimodal_embedding_retriever" not in node_kinds
    assert node_kinds["start"] == "python_tool"
    assert node_kinds["torchreid_visual_lookup"] == "python_tool"
    assert node_kinds["fastreid_visual_lookup"] == "python_tool"
    assert node_kinds["llm_description_parser"] == "llm"
    assert node_kinds["description_facets_retriever"] == "python_tool"
    assert node_kinds["description_semantic_embedding"] == "embedding"
    assert node_kinds["description_semantic_retriever"] == "vector_retriever"
    assert node_kinds["weighted_dual_reid_reciprocal_rank_fusion"] == "python_tool"
    for node in spec.nodes:
        if node.kind == "python_tool":
            assert node.name.strip()
            assert node.description.strip()
    description = next(n for n in spec.nodes if n.id == "llm_description_parser")
    facets_retriever = next(n for n in spec.nodes if n.id == "description_facets_retriever")
    torch_lookup = next(n for n in spec.nodes if n.id == "torchreid_visual_lookup")
    fast_lookup = next(n for n in spec.nodes if n.id == "fastreid_visual_lookup")
    desc_embedding = next(n for n in spec.nodes if n.id == "description_semantic_embedding")
    desc_semantic = next(n for n in spec.nodes if n.id == "description_semantic_retriever")
    rrf = next(n for n in spec.nodes if n.id == "weighted_dual_reid_reciprocal_rank_fusion")
    assert description.model == "google/gemma-4-31b-it"
    assert torch_lookup.inputs["channel"] == "channel_torchreid_visual_embedding"
    assert fast_lookup.inputs["channel"] == "channel_fastreid_visual_embedding"
    assert desc_embedding.model == "google/gemini-embedding-2-preview"
    assert desc_embedding.input_state_key == "query_llm_description"
    assert desc_semantic.index_path == "{gallery_description_db_path}"
    assert desc_semantic.query_embedding_state_key == "query_description_embedding"
    assert desc_semantic.top_k == 200
    assert description.image_inputs[0].state_key == "query_image_path"
    assert "Describe ONLY the visible person" in description.user_prompt_template
    assert "background" in description.user_prompt_template
    assert facets_retriever.callable_path == "backend.tools.reid_specialists.retrieve_gallery_by_description_facets"
    assert facets_retriever.inputs.get("query_description") == "query_llm_description"
    assert facets_retriever.inputs.get("gallery_description_db_path") == "gallery_description_db_path"
    assert rrf.output_state_key == "ranked_gallery_ids"
    assert set((rrf.inputs or {}).keys()) == {
        "torchreid_visual_ranked",
        "fastreid_visual_ranked",
        "description_semantic_ranked",
        "description_facets_ranked",
        "fusion_weight_config",
    }


def test_person_reid_eval_workflow_spec_loads():
    from backend.graphspec import load_graph_spec

    spec = load_graph_spec("person_reid_market1501_eval")
    node_kinds = {n.id: n.kind for n in spec.nodes}
    assert "llm_attribute_lookup" not in node_kinds
    assert "llm_attribute_retriever" not in node_kinds
    assert "fusion_weight_analyser" not in node_kinds
    assert "alias_gallery_ids_for_ranker" not in node_kinds
    assert "final_ranker" not in node_kinds
    assert "parse_final_ranking" not in node_kinds
    assert "boss_orchestrator" not in node_kinds
    assert "rrf_precompute" not in node_kinds
    assert node_kinds["start"] == "python_tool"
    assert node_kinds["torchreid_visual_lookup"] == "python_tool"
    assert node_kinds["fastreid_visual_lookup"] == "python_tool"
    assert "reid_multimodal_embedding_retriever" not in node_kinds
    assert node_kinds["description_embedding_lookup"] == "python_tool"
    assert node_kinds["description_lookup"] == "python_tool"
    assert "description_semantic_retriever" not in node_kinds
    assert "description_facets_retriever" not in node_kinds
    assert node_kinds["weighted_dual_reid_reciprocal_rank_fusion"] == "python_tool"
    for node in spec.nodes:
        if node.kind == "python_tool":
            assert node.name.strip()
            assert node.description.strip()
    torch_lookup = next(n for n in spec.nodes if n.id == "torchreid_visual_lookup")
    fast_lookup = next(n for n in spec.nodes if n.id == "fastreid_visual_lookup")
    desc_lookup = next(n for n in spec.nodes if n.id == "description_lookup")
    desc_embedding_lookup = next(n for n in spec.nodes if n.id == "description_embedding_lookup")
    rrf = next(n for n in spec.nodes if n.id == "weighted_dual_reid_reciprocal_rank_fusion")
    assert torch_lookup.callable_path == "backend.tools.reid_specialists.lookup_precomputed_retrieval_ranking"
    assert torch_lookup.inputs["channel"] == "channel_torchreid_visual_embedding"
    assert fast_lookup.callable_path == "backend.tools.reid_specialists.lookup_precomputed_retrieval_ranking"
    assert fast_lookup.inputs["channel"] == "channel_fastreid_visual_embedding"
    assert desc_lookup.callable_path == "backend.tools.reid_specialists.lookup_precomputed_retrieval_ranking"
    assert desc_lookup.inputs["channel"] == "channel_description_structured_facets"
    assert desc_embedding_lookup.callable_path == "backend.tools.reid_specialists.lookup_precomputed_retrieval_ranking"
    assert desc_embedding_lookup.inputs["channel"] == "channel_description_semantic_text"
    assert rrf.output_state_key == "ranked_gallery_ids"
    assert set((rrf.inputs or {}).keys()) == {
        "torchreid_visual_ranked",
        "fastreid_visual_ranked",
        "description_semantic_ranked",
        "description_facets_ranked",
        "fusion_weight_config",
    }


def test_person_reid_gemini_legacy_workflows_keep_specific_names():
    from backend.graphspec import load_graph_spec

    normal = load_graph_spec("person_reid_market1501_gemini")
    normal_kinds = {n.id: n.kind for n in normal.nodes}
    assert normal_kinds["reid_multimodal_embedding"] == "embedding"
    assert normal_kinds["reid_multimodal_embedding_retriever"] == "vector_retriever"
    visual_embedding = next(n for n in normal.nodes if n.id == "reid_multimodal_embedding")
    assert visual_embedding.model == "google/gemini-embedding-2-preview"

    eval_spec = load_graph_spec("person_reid_market1501_gemini_eval")
    eval_kinds = {n.id: n.kind for n in eval_spec.nodes}
    assert eval_kinds["multimodal_embedding_lookup"] == "python_tool"
    assert eval_kinds["weighted_reciprocal_rank_fusion"] == "python_tool"


def test_person_reid_reid_eval_workflow_variants_load():
    from backend.graphspec import load_graph_spec

    for workflow_id, lookup_id in (
        ("person_reid_market1501_torchreid_eval", "torchreid_visual_lookup"),
        ("person_reid_market1501_fastreid_eval", "fastreid_visual_lookup"),
    ):
        spec = load_graph_spec(workflow_id)
        node_kinds = {n.id: n.kind for n in spec.nodes}
        assert all(kind != "embedding" for kind in node_kinds.values())
        assert all(kind != "vector_retriever" for kind in node_kinds.values())
        assert node_kinds["start"] == "python_tool"
        assert node_kinds[lookup_id] == "python_tool"
        lookup = next(n for n in spec.nodes if n.id == lookup_id)
        assert lookup.callable_path == "backend.tools.reid_specialists.lookup_precomputed_retrieval_ranking"
        assert lookup.inputs["channel"] == "channel_visual_image_embedding"
        assert lookup.output_state_key == "ranked_gallery_ids"


def test_person_reid_reid_fusion_eval_workflow_variants_load():
    from backend.graphspec import load_graph_spec

    for workflow_id, lookup_id in (
        ("person_reid_market1501_torchreid_fusion_eval", "torchreid_visual_lookup"),
        ("person_reid_market1501_fastreid_fusion_eval", "fastreid_visual_lookup"),
    ):
        spec = load_graph_spec(workflow_id)
        node_kinds = {n.id: n.kind for n in spec.nodes}
        assert all(kind != "embedding" for kind in node_kinds.values())
        assert all(kind != "vector_retriever" for kind in node_kinds.values())
        assert node_kinds[lookup_id] == "python_tool"
        assert node_kinds["description_embedding_lookup"] == "python_tool"
        assert node_kinds["description_lookup"] == "python_tool"
        assert node_kinds["weighted_reciprocal_rank_fusion"] == "python_tool"
        lookup = next(n for n in spec.nodes if n.id == lookup_id)
        assert lookup.inputs["channel"] == "channel_visual_image_embedding"
        rrf = next(n for n in spec.nodes if n.id == "weighted_reciprocal_rank_fusion")
        assert rrf.output_state_key == "ranked_gallery_ids"


def test_person_reid_reid_normal_workflow_variants_replace_visual_embedding():
    from backend.graphspec import load_graph_spec

    for workflow_id, lookup_id in (
        ("person_reid_market1501_torchreid", "torchreid_visual_lookup"),
        ("person_reid_market1501_fastreid", "fastreid_visual_lookup"),
    ):
        spec = load_graph_spec(workflow_id)
        node_kinds = {n.id: n.kind for n in spec.nodes}
        assert "reid_multimodal_embedding" not in node_kinds
        assert "reid_multimodal_embedding_retriever" not in node_kinds
        assert node_kinds[lookup_id] == "python_tool"
        assert node_kinds["llm_description_parser"] == "llm"
        assert node_kinds["description_semantic_embedding"] == "embedding"
        lookup = next(n for n in spec.nodes if n.id == lookup_id)
        assert lookup.callable_path == "backend.tools.reid_specialists.lookup_precomputed_retrieval_ranking"
        assert lookup.output_state_key == "reid_multimodal_embedding_ranked"


def test_person_reid_llm_prompts_with_literal_json_format_cleanly():
    from backend.graphspec import load_graph_spec

    class _SafeDict(dict):
        def __missing__(self, key):
            return ""

    state = _SafeDict(
        user_input="query.jpg",
        query_image_path="/tmp/query.jpg",
        query_llm_description={"description": "a person"},
    )

    spec = load_graph_spec("person_reid_market1501")
    for node in spec.nodes:
        if node.kind == "llm":
            rendered = node.user_prompt_template.format_map(state)
            assert "{" in rendered or "[" in rendered
