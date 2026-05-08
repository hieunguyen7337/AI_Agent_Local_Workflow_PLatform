"""Local tools for the person_reid_market1501 workflows.

Hosted model calls belong in first-class workflow node kinds. The functions in
this module are local deterministic helpers for state fan-out, SQLite lookup,
description-based gallery scoring, and weighted reciprocal-rank fusion.
"""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any

from backend.repo_root import resolve_dataset_path_str

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_RRF_K = 60

# Description schema (kept in sync with build_description_dbs.FACET_KEYS).
DESCRIPTION_FACET_KEYS: tuple[str, ...] = (
    "upper_body",
    "lower_body",
    "shoes",
    "head_hair",
    "carried_items",
    "gender_presentation",
    "age_presentation",
    "pose_view",
    "distinctive_marks",
    "uncertainties",
)

# Default region-aware weights for facet scoring. Higher = more discriminative.
# Tuned by intuition: clothing color/garment dominate; pose/uncertainty ignored.
DEFAULT_FACET_WEIGHTS: dict[str, float] = {
    "upper_body_color": 3.0,
    "upper_body_garment": 1.5,
    "upper_body_sleeve": 0.5,
    "upper_body_pattern": 0.5,
    "lower_body_color": 2.0,
    "lower_body_garment": 1.0,
    "lower_body_length": 0.3,
    "lower_body_pattern": 0.3,
    "shoes_color": 0.4,
    "shoes_style": 0.4,
    "head_hair_hair": 0.6,
    "head_hair_headwear": 1.0,
    "carried_item": 1.5,           # per matched item
    "distinctive_mark": 3.0,       # per matched mark (substring overlap)
    "gender_presentation": 0.4,
    "age_presentation": 0.2,
}

# Penalties when both sides assert conflicting strong evidence.
DEFAULT_FACET_PENALTIES: dict[str, float] = {
    "upper_body_color": 1.5,
    "lower_body_color": 1.0,
    "carried_item": 0.8,
    "gender_presentation": 0.5,
}


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


def _coerce_dict(value: Any) -> dict[str, Any]:
    parsed = _json_from_text(value)
    return parsed if isinstance(parsed, dict) else {}


def _normalise_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parsed = _json_from_text(value)
        if isinstance(parsed, list):
            return [str(item).strip().lower() for item in parsed if str(item).strip()]
        return [piece.strip().lower() for piece in re.split(r"[,;/]+", value) if piece.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return [str(value).strip().lower()]


def _query_description_record(value: Any) -> dict[str, Any]:
    """Coerce arbitrary state input into a `{description, facets, tokens}` dict."""
    record = _coerce_dict(value)
    facets_raw = record.get("facets") if isinstance(record.get("facets"), dict) else {}
    facets: dict[str, Any] = {}
    for key in DESCRIPTION_FACET_KEYS:
        raw = facets_raw.get(key)
        if key in {"carried_items", "distinctive_marks", "uncertainties"}:
            facets[key] = _normalise_str_list(raw)
        else:
            facets[key] = "" if raw is None else str(raw).strip().lower()
    tokens = record.get("tokens") if isinstance(record.get("tokens"), dict) else None
    return {
        "description": str(record.get("description") or "").strip(),
        "facets": facets,
        "tokens": tokens or {},
    }


def _resolve_tool_db_path(path_str: str) -> Path:
    return Path(resolve_dataset_path_str(path_str))


def lookup_query_description_from_eval_db(
    query_id: str,
    query_description_db_path: str,
) -> dict[str, Any]:
    """Load the precomputed query description record from the offline DB."""
    db_path = _resolve_tool_db_path(query_description_db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"query description database not found: {query_description_db_path}")

    with sqlite3.connect(db_path) as con:
        row = con.execute(
            """
            SELECT description, description_json, facets_json, tokens_json
            FROM image_descriptions
            WHERE image_id = ?
            """,
            (query_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"query_id {query_id!r} not found in {query_description_db_path}")

    description, description_json, facets_json, tokens_json = row
    record = _coerce_dict(description_json)
    facets = _coerce_dict(facets_json)
    tokens = _coerce_dict(tokens_json)
    record["description"] = str(record.get("description") or description or "")
    record["facets"] = facets
    record["tokens"] = tokens
    return record


def lookup_query_description_embedding_from_eval_db(
    query_id: str,
    query_description_db_path: str,
) -> list[float]:
    """Load the precomputed text embedding of the query description."""
    db_path = _resolve_tool_db_path(query_description_db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"query description database not found: {query_description_db_path}")
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
        raise KeyError(f"query_id {query_id!r} not found in {query_description_db_path}")
    embedding = json.loads(row[0])
    if not isinstance(embedding, list):
        raise ValueError(f"description embedding for {query_id!r} is not a JSON list")
    return [float(value) for value in embedding]


def lookup_query_reid_embedding_from_eval_db(
    query_id: str,
    query_embedding_db_path: str,
) -> list[float]:
    """Load a precomputed query visual embedding from an offline eval embedding DB."""
    db_path = _resolve_tool_db_path(query_embedding_db_path)
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


def lookup_precomputed_retrieval_ranking(
    query_id: str,
    retrieval_score_db_path: str,
    channel: str,
    top_k: int = 200,
) -> list[str]:
    """Read a precomputed ranked gallery list for one eval retrieval channel."""
    db_path = _resolve_tool_db_path(retrieval_score_db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"retrieval score database not found: {retrieval_score_db_path}")
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            """
            SELECT gallery_id
            FROM retrieval_rankings
            WHERE query_id = ? AND channel = ?
            ORDER BY rank
            LIMIT ?
            """,
            (query_id, channel, int(top_k)),
        ).fetchall()
    if not rows:
        raise KeyError(f"query_id {query_id!r} channel {channel!r} not found in {retrieval_score_db_path}")
    return [str(row[0]) for row in rows]


# ---------------------------------------------------------------------------
# Region-aware facet scoring
# ---------------------------------------------------------------------------

def _set_overlap_score(query: list[str], gallery: list[str]) -> float:
    if not query or not gallery:
        return 0.0
    qs, gs = set(query), set(gallery)
    return float(len(qs & gs))


def _set_conflict(query: list[str], gallery: list[str]) -> bool:
    """Both sides claim at least one item, with no overlap."""
    return bool(query) and bool(gallery) and not (set(query) & set(gallery))


def _value_match(query: str, gallery: str, *, ignore: set[str]) -> int:
    """Return 1 on equal informative values, -1 on conflict, 0 otherwise."""
    if not query or not gallery:
        return 0
    if query in ignore or gallery in ignore:
        return 0
    return 1 if query == gallery else -1


def _distinctive_mark_score(query: list[str], gallery: list[str]) -> float:
    if not query or not gallery:
        return 0.0
    score = 0.0
    for q_mark in query:
        q_low = q_mark.lower()
        if not q_low:
            continue
        for g_mark in gallery:
            g_low = (g_mark or "").lower()
            if not g_low:
                continue
            if q_low == g_low or q_low in g_low or g_low in q_low:
                score += 1.0
                break
    return score


def _facet_pair_score(
    query_tokens: dict[str, Any],
    gallery_tokens: dict[str, Any],
    weights: dict[str, float],
    penalties: dict[str, float],
) -> float:
    score = 0.0

    for region, sub_keys in (
        ("upper_body", ("color", "garment", "sleeve", "pattern")),
        ("lower_body", ("color", "garment", "length", "pattern")),
        ("shoes", ("color", "style")),
        ("head_hair", ("hair", "headwear")),
    ):
        q_region = query_tokens.get(region) if isinstance(query_tokens.get(region), dict) else {}
        g_region = gallery_tokens.get(region) if isinstance(gallery_tokens.get(region), dict) else {}
        for sub in sub_keys:
            weight_key = f"{region}_{sub}"
            penalty_key = weight_key
            weight = float(weights.get(weight_key, 0.0))
            penalty = float(penalties.get(penalty_key, 0.0))
            q_list = list(q_region.get(f"{sub}s") or [])
            g_list = list(g_region.get(f"{sub}s") or [])
            if weight:
                score += weight * _set_overlap_score(q_list, g_list)
            if penalty and _set_conflict(q_list, g_list):
                score -= penalty

    item_weight = float(weights.get("carried_item", 0.0))
    item_penalty = float(penalties.get("carried_item", 0.0))
    q_items = list(query_tokens.get("carried_items") or [])
    g_items = list(gallery_tokens.get("carried_items") or [])
    if item_weight:
        score += item_weight * _set_overlap_score(q_items, g_items)
    if item_penalty and _set_conflict(q_items, g_items):
        score -= item_penalty

    mark_weight = float(weights.get("distinctive_mark", 0.0))
    if mark_weight:
        score += mark_weight * _distinctive_mark_score(
            list(query_tokens.get("distinctive_marks") or []),
            list(gallery_tokens.get("distinctive_marks") or []),
        )

    ignore_values = {"", "unclear", "none"}
    gender_match = _value_match(
        str(query_tokens.get("gender_presentation") or ""),
        str(gallery_tokens.get("gender_presentation") or ""),
        ignore=ignore_values,
    )
    if gender_match > 0:
        score += float(weights.get("gender_presentation", 0.0))
    elif gender_match < 0:
        score -= float(penalties.get("gender_presentation", 0.0))

    age_match = _value_match(
        str(query_tokens.get("age_presentation") or ""),
        str(gallery_tokens.get("age_presentation") or ""),
        ignore=ignore_values,
    )
    if age_match > 0:
        score += float(weights.get("age_presentation", 0.0))

    return score


def retrieve_gallery_by_description_facets(
    query_description: str | dict[str, Any],
    gallery_description_db_path: str,
    top_k: int,
    facet_weights: str | dict[str, float] | None = None,
    facet_penalties: str | dict[str, float] | None = None,
) -> list[str]:
    """Rank gallery IDs by region-aware facet/token overlap with contradiction penalty."""
    record = _query_description_record(query_description)
    query_tokens = record.get("tokens") or {}
    weights = _coerce_weight_map(facet_weights, DEFAULT_FACET_WEIGHTS)
    penalties = _coerce_weight_map(facet_penalties, DEFAULT_FACET_PENALTIES)

    db_path = _resolve_tool_db_path(gallery_description_db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"gallery description database not found: {gallery_description_db_path}")

    stat = db_path.stat()
    rows = _load_gallery_description_tokens_cached(
        db_path.resolve().as_posix(),
        stat.st_mtime_ns,
        stat.st_size,
    )

    scored: list[tuple[float, str]] = []
    for gallery_id, gallery_tokens in rows:
        score = _facet_pair_score(query_tokens, gallery_tokens, weights, penalties)
        scored.append((score, str(gallery_id)))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [gallery_id for _score, gallery_id in scored[: int(top_k)]]


@lru_cache(maxsize=8)
def _load_gallery_description_tokens_cached(
    path_key: str,
    _mtime_ns: int,
    _size: int,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    with sqlite3.connect(Path(path_key)) as con:
        rows = con.execute(
            """
            SELECT image_id, tokens_json
            FROM image_descriptions
            ORDER BY image_id
            """
        ).fetchall()
    return tuple((str(gallery_id), _coerce_dict(tokens_json)) for gallery_id, tokens_json in rows)


def _coerce_weight_map(value: Any, defaults: dict[str, float]) -> dict[str, float]:
    if value is None:
        return dict(defaults)
    parsed = value if isinstance(value, dict) else _json_from_text(value)
    if not isinstance(parsed, dict):
        return dict(defaults)
    out = dict(defaults)
    for key, raw in parsed.items():
        try:
            out[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# Weighted reciprocal-rank fusion across visual + description channels
# ---------------------------------------------------------------------------

DEFAULT_FUSION_WEIGHTS: dict[str, float] = {
    "reid_multimodal_embedding": 0.80,
    "description_semantic": 0.13,
    "description_facets": 0.07,
}

DESCRIPTION_HEAVY_FUSION_WEIGHTS: dict[str, float] = {
    "reid_multimodal_embedding": 0.10,
    "description_semantic": 0.60,
    "description_facets": 0.30,
}


def _parse_fusion_weights(value: Any) -> dict[str, float]:
    parsed = _json_from_text(value)
    if not isinstance(parsed, dict):
        return dict(DEFAULT_FUSION_WEIGHTS)
    raw_weights = parsed.get("rrf_weights", parsed)
    if not isinstance(raw_weights, dict):
        return dict(DEFAULT_FUSION_WEIGHTS)

    weights: dict[str, float] = {}
    for key, default in DEFAULT_FUSION_WEIGHTS.items():
        try:
            weight = float(raw_weights.get(key, default))
        except (TypeError, ValueError):
            return dict(DEFAULT_FUSION_WEIGHTS)
        if weight < 0:
            return dict(DEFAULT_FUSION_WEIGHTS)
        weights[key] = weight
    if sum(weights.values()) <= 0:
        return dict(DEFAULT_FUSION_WEIGHTS)
    return weights


def weighted_reciprocal_rank_fusion(
    reid_multimodal_embedding_ranked: list[str] | None = None,
    description_semantic_ranked: list[str] | None = None,
    description_facets_ranked: list[str] | None = None,
    fusion_weight_analysis: str | dict[str, Any] | None = None,
    top_k: int = 20,
) -> list[str]:
    """Fuse visual ReID embeddings with description-based channels via weighted RRF.

    Default weights are visual=0.80 / description_semantic=0.13 / description_facets=0.07.
    The visual channel dominates because in person ReID natural-language captions
    routinely collapse fine visual detail; description channels are auxiliary.
    """
    weights = _parse_fusion_weights(fusion_weight_analysis)
    ranked_inputs = [
        (reid_multimodal_embedding_ranked or [], weights["reid_multimodal_embedding"]),
        (description_semantic_ranked or [], weights["description_semantic"]),
        (description_facets_ranked or [], weights["description_facets"]),
    ]
    scores: dict[str, float] = {}
    for ranked, weight in ranked_inputs:
        if weight <= 0 or not ranked:
            continue
        for rank, gallery_id in enumerate(ranked, start=1):
            gid = str(gallery_id)
            scores[gid] = scores.get(gid, 0.0) + weight / (_RRF_K + rank)
    return sorted(scores, key=lambda gid: (-scores[gid], gid))[: int(top_k)]


def _rank_position(ranked: list[str] | None, gallery_id: str) -> int | None:
    for index, item in enumerate(ranked or [], start=1):
        if str(item) == gallery_id:
            return index
    return None


def _weighted_scores(rankings: list[tuple[list[str], float]]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranked, weight in rankings:
        if weight <= 0:
            continue
        for rank, gallery_id in enumerate(ranked, start=1):
            gid = str(gallery_id)
            scores[gid] = scores.get(gid, 0.0) + weight / (_RRF_K + rank)
    return scores


def decide_reid_fusion_weights(
    reid_multimodal_embedding_ranked: list[str] | None = None,
    description_semantic_ranked: list[str] | None = None,
    description_facets_ranked: list[str] | None = None,
    semantic_agreement_rank: int = 3,
    facets_agreement_rank: int = 5,
    visual_disagreement_rank: int = 20,
    min_description_margin: float = 0.00008,
) -> dict[str, Any]:
    """Choose visual-heavy or description-heavy RRF weights from rank agreement only.

    The rule is intentionally label-free. It treats the description branch as
    likely reliable when semantic retrieval and facet retrieval independently
    agree on the same top candidate, and treats the visual embedding branch as
    likely wrong when that consensus candidate is not present in the visual top
    `visual_disagreement_rank`.
    """
    semantic = [str(item) for item in (description_semantic_ranked or [])]
    facets = [str(item) for item in (description_facets_ranked or [])]
    visual = [str(item) for item in (reid_multimodal_embedding_ranked or [])]

    desc_scores = _weighted_scores(
        [
            (semantic, DEFAULT_FUSION_WEIGHTS["description_semantic"]),
            (facets, DEFAULT_FUSION_WEIGHTS["description_facets"]),
        ]
    )
    if not desc_scores:
        return {
            "decision": "visual_heavy_default",
            "reason": "description channels are empty",
            "rrf_weights": dict(DEFAULT_FUSION_WEIGHTS),
        }

    ordered = sorted(desc_scores, key=lambda gid: (-desc_scores[gid], gid))
    top_desc = ordered[0]
    second_score = desc_scores[ordered[1]] if len(ordered) > 1 else 0.0
    margin = desc_scores[top_desc] - second_score
    semantic_rank = _rank_position(semantic, top_desc)
    facets_rank = _rank_position(facets, top_desc)
    visual_rank = _rank_position(visual, top_desc)

    description_consensus = (
        semantic_rank is not None
        and semantic_rank <= int(semantic_agreement_rank)
        and facets_rank is not None
        and facets_rank <= int(facets_agreement_rank)
        and margin >= float(min_description_margin)
    )
    visual_disagrees = visual_rank is None or visual_rank > int(visual_disagreement_rank)

    if description_consensus and visual_disagrees:
        return {
            "decision": "description_heavy",
            "reason": (
                "description semantic/facet consensus is strong and the consensus "
                "candidate is weak or absent in visual ranking"
            ),
            "rrf_weights": dict(DESCRIPTION_HEAVY_FUSION_WEIGHTS),
            "top_description_candidate": top_desc,
            "semantic_rank": semantic_rank,
            "facets_rank": facets_rank,
            "visual_rank": visual_rank,
            "description_rrf_margin": margin,
        }

    return {
        "decision": "visual_heavy_default",
        "reason": "description consensus was not strong enough or visual already agreed",
        "rrf_weights": dict(DEFAULT_FUSION_WEIGHTS),
        "top_description_candidate": top_desc,
        "semantic_rank": semantic_rank,
        "facets_rank": facets_rank,
        "visual_rank": visual_rank,
        "description_rrf_margin": margin,
    }
