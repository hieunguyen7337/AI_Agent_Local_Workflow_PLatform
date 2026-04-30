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
                        "llm_attribute_ranked": "llm_attribute_ranked",
                        "reid_multimodal_embedding_ranked": "reid_multimodal_embedding_ranked",
                        "fusion_weight_analysis": "fusion_weight_analysis",
                    },
                    "output_state_key": "reciprocal_rank_fused_ranking",
                }
            ],
            "edges": [{"from": "fusion", "to": "__end__"}],
        })
    node = spec.nodes[0]
    assert node.kind == "python_tool"
    assert node.callable_path == "backend.tools.reid_specialists.weighted_reciprocal_rank_fusion"
    assert node.inputs == {
        "llm_attribute_ranked": "llm_attribute_ranked",
        "reid_multimodal_embedding_ranked": "reid_multimodal_embedding_ranked",
        "fusion_weight_analysis": "fusion_weight_analysis",
    }
    assert node.output_state_key == "reciprocal_rank_fused_ranking"


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
# ReID attribute and embedding retriever functions
# ---------------------------------------------------------------------------

def _make_gallery_db(path: Path) -> Path:
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE gallery_attributes (
                gallery_id TEXT PRIMARY KEY,
                image_path TEXT NOT NULL,
                pid INTEGER NOT NULL,
                camid INTEGER NOT NULL,
                attributes_json TEXT NOT NULL,
                gender TEXT NOT NULL,
                hair TEXT NOT NULL,
                age TEXT NOT NULL,
                clothing_type TEXT NOT NULL,
                upper_body_clothes TEXT NOT NULL,
                lower_body_clothes TEXT NOT NULL,
                hat TEXT NOT NULL,
                backpack TEXT NOT NULL,
                bag TEXT NOT NULL,
                handbag TEXT NOT NULL,
                upper_body_clothes_color TEXT NOT NULL,
                lower_body_clothes_color TEXT NOT NULL
            )
            """
        )
        con.executemany(
            """
            INSERT INTO gallery_attributes (
                gallery_id, image_path, pid, camid, attributes_json,
                gender, hair, age, clothing_type, upper_body_clothes,
                lower_body_clothes, hat, backpack, bag, handbag,
                upper_body_clothes_color, lower_body_clothes_color
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("g1.jpg", "/gallery/g1.jpg", 1, 1, "{}", "male", "short", "adult", "pants", "short sleeve", "long", "no", "yes", "no", "no", "red", "blue"),
                ("g2.jpg", "/gallery/g2.jpg", 2, 2, "{}", "female", "long", "adult", "dress", "short sleeve", "long", "no", "no", "yes", "no", "blue", "black"),
                ("g3.jpg", "/gallery/g3.jpg", 3, 3, "{}", "male", "short", "adult", "pants", "short sleeve", "long", "no", "yes", "no", "no", "red", "blue"),
            ],
        )
    return path


def _make_query_db(path: Path) -> Path:
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE image_attributes (
                image_id TEXT PRIMARY KEY,
                image_path TEXT NOT NULL,
                pid INTEGER NOT NULL,
                camid INTEGER NOT NULL,
                attributes_json TEXT NOT NULL,
                gender TEXT NOT NULL,
                hair TEXT NOT NULL,
                age TEXT NOT NULL,
                clothing_type TEXT NOT NULL,
                upper_body_clothes TEXT NOT NULL,
                lower_body_clothes TEXT NOT NULL,
                hat TEXT NOT NULL,
                backpack TEXT NOT NULL,
                bag TEXT NOT NULL,
                handbag TEXT NOT NULL,
                upper_body_clothes_color TEXT NOT NULL,
                lower_body_clothes_color TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO image_attributes (
                image_id, image_path, pid, camid, attributes_json,
                gender, hair, age, clothing_type, upper_body_clothes,
                lower_body_clothes, hat, backpack, bag, handbag,
                upper_body_clothes_color, lower_body_clothes_color
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "q1.jpg",
                "/query/q1.jpg",
                1,
                1,
                "{}",
                "male",
                "short",
                "adult",
                "pants",
                "short sleeve",
                "long",
                "no",
                "yes",
                "no",
                "no",
                "red",
                "blue",
            ),
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


def test_query_llm_attribute_lookup_reads_query_db(tmp_path: Path):
    from backend.tools.reid_specialists import query_llm_attribute_lookup

    db_path = _make_query_db(tmp_path / "query.sqlite")
    result = query_llm_attribute_lookup("q1.jpg", str(db_path))
    assert result == {
        "gender": "male",
        "hair": "short",
        "age": "adult",
        "clothing_type": "pants",
        "upper_body_clothes": "short sleeve",
        "lower_body_clothes": "long",
        "hat": "no",
        "backpack": "yes",
        "bag": "no",
        "handbag": "no",
        "upper_body_clothes_color": "red",
        "lower_body_clothes_color": "blue",
    }


def test_llm_attribute_gallery_retriever_ranks_matching_attributes(tmp_path: Path):
    from backend.tools.reid_specialists import llm_attribute_gallery_retriever

    db_path = _make_gallery_db(tmp_path / "gallery.sqlite")
    result = llm_attribute_gallery_retriever(
        query_attributes='{"gender": "male", "hair": "short", "age": "adult", "clothing_type": "pants", "upper_body_clothes": "short sleeve", "lower_body_clothes": "long", "hat": "no", "backpack": "yes", "bag": "no", "handbag": "no", "upper_body_clothes_color": "red", "lower_body_clothes_color": "blue"}',
        gallery_db_path=str(db_path),
        top_k=2,
    )
    assert result == ["g1.jpg", "g3.jpg"]


def test_multimodal_embedding_lookup_reads_query_db(tmp_path: Path):
    from backend.tools.reid_specialists import multimodal_embedding_lookup

    db_path = _make_query_embedding_db(tmp_path / "query_embeddings.sqlite")
    assert multimodal_embedding_lookup("q1.jpg", str(db_path)) == [0.1, 0.2, 0.3]


def test_weighted_reciprocal_rank_fusion_uses_weights():
    from backend.tools.reid_specialists import weighted_reciprocal_rank_fusion

    result = weighted_reciprocal_rank_fusion(
        llm_attribute_ranked=["b", "d"],
        reid_multimodal_embedding_ranked=["c", "a"],
        fusion_weight_analysis='{"rrf_weights": {"llm_attribute": 0.1, "reid_multimodal_embedding": 0.9}}',
    )
    assert result[:2] == ["c", "a"]


# ---------------------------------------------------------------------------
# person_reid_market1501 workflow spec loads without error
# ---------------------------------------------------------------------------

def test_person_reid_workflow_spec_loads():
    """Confirm the updated workflow YAML passes GraphSpec validation."""
    from backend.graphspec import load_graph_spec

    spec = load_graph_spec("person_reid_market1501")
    assert spec.name == "person_reid_market1501"
    node_kinds = {n.id: n.kind for n in spec.nodes}
    assert "visual_specialist" not in node_kinds
    assert "text_specialist" not in node_kinds
    assert "body_shape_specialist" not in node_kinds
    assert "boss_orchestrator" not in node_kinds
    assert "rrf_precompute" not in node_kinds
    assert node_kinds["start"] == "python_tool"
    assert node_kinds["llm_attribute_parser"] == "llm"
    assert node_kinds["llm_attribute_retriever"] == "python_tool"
    assert node_kinds["reid_multimodal_embedding"] == "embedding"
    assert node_kinds["reid_multimodal_embedding_retriever"] == "vector_retriever"
    assert node_kinds["fusion_weight_analyser"] == "llm"
    assert node_kinds["weighted_reciprocal_rank_fusion"] == "python_tool"
    assert node_kinds["final_ranker"] == "llm"
    analyser = next(n for n in spec.nodes if n.id == "fusion_weight_analyser")
    attribute = next(n for n in spec.nodes if n.id == "llm_attribute_parser")
    embedding = next(n for n in spec.nodes if n.id == "reid_multimodal_embedding")
    final_ranker = next(n for n in spec.nodes if n.id == "final_ranker")
    assert analyser.model == "google/gemma-4-31b-it"
    assert attribute.model == "qwen/qwen3.5-9b"
    assert embedding.model == "google/gemini-embedding-2-preview"
    assert final_ranker.model == "google/gemma-4-31b-it"
    assert analyser.image_inputs[0].state_key == "query_image_path"
    assert attribute.image_inputs[0].state_key == "query_image_path"
    assert embedding.input_template == ""


def test_person_reid_eval_workflow_spec_loads():
    from backend.graphspec import load_graph_spec

    spec = load_graph_spec("person_reid_market1501_eval")
    node_kinds = {n.id: n.kind for n in spec.nodes}
    assert "boss_orchestrator" not in node_kinds
    assert "rrf_precompute" not in node_kinds
    assert node_kinds["start"] == "python_tool"
    assert node_kinds["fusion_weight_analyser"] == "llm"
    assert node_kinds["llm_attribute_lookup"] == "python_tool"
    assert node_kinds["multimodal_embedding_lookup"] == "python_tool"
    assert node_kinds["reid_multimodal_embedding_retriever"] == "vector_retriever"
    assert node_kinds["weighted_reciprocal_rank_fusion"] == "python_tool"
    assert node_kinds["final_ranker"] == "llm"
    analyser = next(n for n in spec.nodes if n.id == "fusion_weight_analyser")
    attribute = next(n for n in spec.nodes if n.id == "llm_attribute_lookup")
    embedding = next(n for n in spec.nodes if n.id == "multimodal_embedding_lookup")
    final_ranker = next(n for n in spec.nodes if n.id == "final_ranker")
    assert analyser.model == "google/gemma-4-31b-it"
    assert attribute.callable_path == "backend.tools.reid_specialists.query_llm_attribute_lookup"
    assert embedding.callable_path == "backend.tools.reid_specialists.multimodal_embedding_lookup"
    assert final_ranker.model == "google/gemma-4-31b-it"
    assert final_ranker.output_state_key == "ranked_gallery_pids_raw"
    assert node_kinds["parse_final_ranking"] == "python_tool"
