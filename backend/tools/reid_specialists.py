"""Local tools for the person_reid_market1501 workflows.

Hosted model calls belong in first-class workflow node kinds. The functions in
this module are local deterministic helpers for state fan-out, SQLite lookup,
SQLite retrieval, weighted reciprocal-rank fusion, and final output parsing.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_RRF_K = 60

ATTRIBUTE_KEYS: tuple[str, ...] = (
    "gender",
    "hair",
    "clothing_type",
    "upper_body_clothes",
    "lower_body_clothes",
    "hat",
    "backpack",
    "bag",
    "handbag",
)


def mark_workflow_start(user_input: str = "") -> str:
    """Mark the explicit workflow start node without changing user state."""
    return str(user_input or "")


def _json_from_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        match = _JSON_OBJECT_RE.search(value)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def _parse_attributes(value: Any) -> dict[str, str]:
    value = _json_from_text(value)
    if not isinstance(value, dict):
        return {key: "" for key in ATTRIBUTE_KEYS}
    attrs = value.get("attributes") if isinstance(value.get("attributes"), dict) else value
    return {key: str(attrs.get(key, "")).strip().lower() for key in ATTRIBUTE_KEYS}


def lookup_query_attributes_from_eval_db(query_id: str, query_db_path: str) -> dict[str, str]:
    """Load precomputed query attributes from an offline eval query DB."""
    db_path = Path(query_db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"query attribute database not found: {query_db_path}")

    select_cols = ", ".join(ATTRIBUTE_KEYS)
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            f"""
            SELECT attributes_json, {select_cols}
            FROM image_attributes
            WHERE image_id = ?
            """,
            (query_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"query_id {query_id!r} not found in {query_db_path}")

    parsed = _parse_attributes(row[0])
    for key, value in zip(ATTRIBUTE_KEYS, row[1:]):
        if value:
            parsed[key] = str(value).strip().lower()
    return parsed


def lookup_query_reid_embedding_from_eval_db(
    query_id: str,
    query_embedding_db_path: str,
) -> list[float]:
    """Load a precomputed query embedding from an offline eval embedding DB."""
    db_path = Path(query_embedding_db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"query embedding database not found: {query_embedding_db_path}")
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            """
            SELECT embedding_json
            FROM image_embeddings
            WHERE image_id = ?
            """,
            (query_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"query_id {query_id!r} not found in {query_embedding_db_path}")
    embedding = json.loads(row[0])
    if not isinstance(embedding, list):
        raise ValueError(f"embedding for {query_id!r} is not a JSON list")
    return [float(value) for value in embedding]


def retrieve_gallery_by_attribute_similarity(
    query_attributes: str | dict[str, Any],
    gallery_db_path: str,
    top_k: int,
) -> list[str]:
    """Rank gallery IDs by exact-match similarity over the flat attributes."""
    attrs = _parse_attributes(query_attributes)
    db_path = Path(gallery_db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"gallery database not found: {gallery_db_path}")

    select_cols = ", ".join(ATTRIBUTE_KEYS)
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            f"""
            SELECT gallery_id, {select_cols}
            FROM gallery_attributes
            ORDER BY gallery_id
            """
        ).fetchall()

    scored: list[tuple[float, str]] = []
    for row in rows:
        gallery_id = str(row[0])
        score = 0.0
        for key, gallery_value in zip(ATTRIBUTE_KEYS, row[1:]):
            query_value = attrs.get(key, "")
            if query_value and query_value == str(gallery_value or "").strip().lower():
                score += 1.0
        scored.append((score, gallery_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [gallery_id for _score, gallery_id in scored[: int(top_k)]]


def _parse_fusion_weights(value: Any) -> dict[str, float]:
    defaults = {"llm_attribute": 0.1, "reid_multimodal_embedding": 0.9}
    parsed = _json_from_text(value)
    if not isinstance(parsed, dict):
        return defaults
    raw_weights = parsed.get("rrf_weights", parsed)
    if not isinstance(raw_weights, dict):
        return defaults

    weights: dict[str, float] = {}
    for key in defaults:
        try:
            weight = float(raw_weights.get(key, defaults[key]))
        except (TypeError, ValueError):
            return defaults
        if weight < 0:
            return defaults
        weights[key] = weight
    if sum(weights.values()) <= 0:
        return defaults
    return weights


def weighted_reciprocal_rank_fusion(
    llm_attribute_ranked: list[str],
    reid_multimodal_embedding_ranked: list[str],
    fusion_weight_analysis: str | dict[str, Any] | None = None,
) -> list[str]:
    """Fuse attribute and ReID embedding rankings using weighted RRF.

    Default weights are emb=0.9 / attr=0.1 — embedding-heavy, reflecting that the
    embedding branch consistently outperforms the attribute branch on Market-1501
    crops.  Pass an explicit ``fusion_weight_analysis`` JSON only when you have a
    reliable signal to override the defaults.
    """
    weights = _parse_fusion_weights(fusion_weight_analysis)
    ranked_inputs = [
        (llm_attribute_ranked, weights["llm_attribute"]),
        (reid_multimodal_embedding_ranked, weights["reid_multimodal_embedding"]),
    ]
    scores: dict[str, float] = {}
    for ranked, weight in ranked_inputs:
        for rank, gallery_id in enumerate(ranked or [], start=1):
            gid = str(gallery_id)
            scores[gid] = scores.get(gid, 0.0) + weight / (_RRF_K + rank)
    return sorted(scores, key=lambda gid: (-scores[gid], gid))[:20]


