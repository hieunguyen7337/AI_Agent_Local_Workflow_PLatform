"""Build offline query and gallery person-only description databases for a partition.

Each image gets:
  * `description`           - one concise person-only caption from a vision LLM.
  * `facets`                - structured per-region facets (upper/lower/shoes/hair/...).
  * `tokens`                - canonical tokens extracted locally for fast scoring.
  * `description_embedding` - text embedding of the description for semantic retrieval.

The resulting sqlite DB exposes both `image_descriptions` (descriptions/facets/tokens)
and `image_embeddings` (embedding_json) so the existing `vector_retriever` node
can also consume the same file. The gallery DB additionally exposes
`gallery_descriptions` and `gallery_embeddings` views for the same convenience.
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.providers import call_embedding_provider, call_provider

_FILENAME_RE = re.compile(r"^(\d+)_c(\d+)s\d+_\d+_\d+\.jpg$")
_DEFAULT_PARTITION = Path(__file__).parent / "partition_1000q_5000g"
_DEFAULT_OUTPUT_DIR = Path(__file__).parent / "description_db_google_gemma_4_31b_it"
DEFAULT_DESCRIPTION_MODEL = "google/gemma-4-31b-it"
DEFAULT_EMBEDDING_MODEL = "google/gemini-embedding-2-preview"
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


FACET_KEYS: tuple[str, ...] = (
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

DESCRIPTION_PROMPT = """Describe ONLY the visible person in this image.
Ignore the background, scene, camera angle, image quality, shadows, weather, and any other people.
Focus strictly on the main pedestrian: garments, accessories, hair, and visible body presentation.
Do not invent details that aren't visible. If a body region is occluded, cropped, or unclear, write "unclear".

Return only a raw JSON object with these exact keys:

{
  "description":          "<one concise person-only caption, 1-2 sentences>",
  "facets": {
    "upper_body":           "<color + garment type + sleeve length, or 'unclear'>",
    "lower_body":           "<color + garment type + length, or 'unclear'>",
    "shoes":                "<color + style, or 'unclear'>",
    "head_hair":            "<hair length/style + headwear, or 'unclear'>",
    "carried_items":        ["<each visible item: backpack | handbag | shoulder bag | tote | plastic bag | box | umbrella | phone | briefcase | none>"],
    "gender_presentation":  "male | female | unclear",
    "age_presentation":     "child | young adult | adult | older adult | unclear",
    "pose_view":            "front | back | left | right | unclear",
    "distinctive_marks":    ["<each short specific visible cue, e.g. 'red logo on chest'>"],
    "uncertainties":        ["<each region that is occluded/cropped/blurred>"]
  }
}

No markdown, no explanation - raw JSON only."""

# Curated vocabularies for canonical token extraction.
_COLOR_TOKENS: tuple[str, ...] = (
    "black", "white", "gray", "grey", "blue", "navy", "red", "pink", "green",
    "yellow", "orange", "brown", "beige", "purple", "tan", "cream", "khaki",
    "maroon", "teal", "silver", "gold", "dark", "light",
)
_UPPER_GARMENT_TOKENS: tuple[str, ...] = (
    "t-shirt", "tshirt", "shirt", "polo", "jacket", "coat", "hoodie", "sweater",
    "vest", "dress", "jersey", "blouse", "suit", "uniform", "tank",
)
_LOWER_GARMENT_TOKENS: tuple[str, ...] = (
    "pants", "trousers", "jeans", "shorts", "skirt", "leggings", "tights",
)
_SHOES_TOKENS: tuple[str, ...] = (
    "sneakers", "shoes", "boots", "sandals", "heels", "flats", "loafers",
)
_LENGTH_TOKENS: tuple[str, ...] = ("short", "long", "mid", "midi", "knee")
_SLEEVE_TOKENS: tuple[str, ...] = ("short sleeve", "long sleeve", "sleeveless")
_PATTERN_TOKENS: tuple[str, ...] = (
    "striped", "stripes", "plaid", "checkered", "solid", "floral",
    "logo", "graphic", "polka", "patterned",
)
_HAIR_TOKENS: tuple[str, ...] = ("short", "long", "bald", "ponytail", "braid")
_HEADWEAR_TOKENS: tuple[str, ...] = ("hat", "cap", "beanie", "helmet", "hood")
_ITEM_TOKENS: tuple[str, ...] = (
    "backpack", "handbag", "shoulder bag", "tote", "plastic bag",
    "box", "umbrella", "phone", "briefcase", "shopping bag",
)


def _parse_image(filename: str) -> tuple[int, int] | None:
    match = _FILENAME_RE.match(filename)
    if not match:
        return None
    pid, camid = int(match.group(1)), int(match.group(2))
    if pid in {-1, 0}:
        return None
    return pid, camid


def _load_images(directory: Path) -> list[tuple[str, Path, int, int]]:
    rows: list[tuple[str, Path, int, int]] = []
    for path in sorted(directory.glob("*.jpg")):
        parsed = _parse_image(path.name)
        if parsed is None:
            continue
        pid, camid = parsed
        rows.append((path.name, path.resolve(), pid, camid))
    return rows


def _image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _coerce_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            match = _JSON_OBJECT_RE.search(value)
            value = {} if not match else _coerce_dict(match.group(0))
    return value if isinstance(value, dict) else {}


def _normalise_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _normalise_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [piece.strip() for piece in re.split(r"[,;/]+", value) if piece.strip()]
        return [piece.lower() for piece in items]
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip().lower()
            if text:
                out.append(text)
        return out
    return [str(value).strip().lower()]


def _parse_description(value: Any) -> dict[str, Any]:
    record = _coerce_dict(value)
    description = str(record.get("description", "")).strip()
    raw_facets = record.get("facets") if isinstance(record.get("facets"), dict) else {}
    facets: dict[str, Any] = {}
    for key in FACET_KEYS:
        raw = raw_facets.get(key)
        if key in {"carried_items", "distinctive_marks", "uncertainties"}:
            facets[key] = _normalise_list(raw)
        else:
            facets[key] = _normalise_text(raw)
    return {"description": description, "facets": facets}


def _match_tokens(text: str, vocab: tuple[str, ...]) -> list[str]:
    if not text:
        return []
    text = text.lower()
    found = []
    for token in vocab:
        if token and token in text:
            found.append(token)
    return found


def _tokenise_facets(facets: dict[str, Any]) -> dict[str, Any]:
    upper = facets.get("upper_body", "") or ""
    lower = facets.get("lower_body", "") or ""
    shoes = facets.get("shoes", "") or ""
    hair = facets.get("head_hair", "") or ""

    tokens: dict[str, Any] = {
        "upper_body": {
            "colors": _match_tokens(upper, _COLOR_TOKENS),
            "garments": _match_tokens(upper, _UPPER_GARMENT_TOKENS),
            "sleeves": _match_tokens(upper, _SLEEVE_TOKENS),
            "patterns": _match_tokens(upper, _PATTERN_TOKENS),
        },
        "lower_body": {
            "colors": _match_tokens(lower, _COLOR_TOKENS),
            "garments": _match_tokens(lower, _LOWER_GARMENT_TOKENS),
            "lengths": _match_tokens(lower, _LENGTH_TOKENS),
            "patterns": _match_tokens(lower, _PATTERN_TOKENS),
        },
        "shoes": {
            "colors": _match_tokens(shoes, _COLOR_TOKENS),
            "styles": _match_tokens(shoes, _SHOES_TOKENS),
        },
        "head_hair": {
            "hair": _match_tokens(hair, _HAIR_TOKENS),
            "headwear": _match_tokens(hair, _HEADWEAR_TOKENS),
        },
        "carried_items": [
            item for item in (facets.get("carried_items") or [])
            if isinstance(item, str) and item != "none"
        ],
        "distinctive_marks": [
            item for item in (facets.get("distinctive_marks") or []) if isinstance(item, str)
        ],
        "gender_presentation": facets.get("gender_presentation") or "",
        "age_presentation": facets.get("age_presentation") or "",
    }
    return tokens


def make_live_description_extractor(
    model: str = DEFAULT_DESCRIPTION_MODEL,
) -> Callable[[Path], dict[str, Any]]:
    def _extract(image_path: Path) -> dict[str, Any]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You write strict person-only descriptions for person re-identification. "
                    "Return raw JSON only and never describe the background or other people."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": DESCRIPTION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(image_path), "detail": "auto"},
                    },
                ],
            },
        ]
        response = call_provider(
            "openrouter",
            model=model,
            messages=messages,
            temperature=0.0,
            max_retries=3,
        )
        return _parse_description(response.text)

    return _extract


def make_live_text_embedding_extractor(
    model: str = DEFAULT_EMBEDDING_MODEL,
) -> Callable[[str], list[float]]:
    def _embed(text: str) -> list[float]:
        payload = text.strip() or "person"
        response = call_embedding_provider(
            "openrouter",
            model=model,
            input_payload=[payload],
            max_retries=3,
        )
        return [float(value) for value in response.embedding]

    return _embed


def build_description_db(
    *,
    image_dir: Path,
    output: Path,
    is_gallery: bool,
    max_concurrency: int = 50,
    description_model: str = DEFAULT_DESCRIPTION_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    description_extractor: Callable[[Path], dict[str, Any]] | None = None,
    embedding_extractor: Callable[[str], list[float]] | None = None,
    resume: bool = False,
) -> Path:
    rows = _load_images(image_dir)
    if not rows:
        raise ValueError(f"No valid images found in {image_dir}")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")

    description_fn = description_extractor or make_live_description_extractor(description_model)
    embed_fn = embedding_extractor or make_live_text_embedding_extractor(embedding_model)

    if description_extractor is None or embedding_extractor is None:
        load_dotenv()
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("OPENROUTER_API_KEY is not set")

    def _process(row: tuple[str, Path, int, int]) -> tuple[str, Path, int, int, dict[str, Any], list[float]]:
        image_id, image_path, pid, camid = row
        last_exc: Exception | None = None
        for attempt in range(6):
            try:
                record = description_fn(image_path)
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 5:
                    raise
                time.sleep(min(30.0, 2.0**attempt))
        else:
            raise RuntimeError(f"failed to describe {image_id}") from last_exc
        record.setdefault("description", "")
        record.setdefault("facets", {key: ([] if key in {"carried_items", "distinctive_marks", "uncertainties"} else "") for key in FACET_KEYS})
        embedding_text = record["description"] if record["description"] else "person"
        last_exc = None
        for attempt in range(6):
            try:
                embedding = embed_fn(embedding_text)
                break
            except Exception as exc:
                last_exc = exc
                if attempt == 5:
                    raise
                time.sleep(min(30.0, 2.0**attempt))
        else:
            raise RuntimeError(f"failed to embed description for {image_id}") from last_exc
        return image_id, image_path, pid, camid, record, embedding

    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as con:
        con.execute("DROP VIEW IF EXISTS gallery_descriptions")
        con.execute("DROP VIEW IF EXISTS gallery_embeddings")
        if not resume:
            con.execute("DROP TABLE IF EXISTS image_descriptions")
            con.execute("DROP TABLE IF EXISTS image_embeddings")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS image_descriptions (
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
            CREATE TABLE IF NOT EXISTS image_embeddings (
                image_id TEXT PRIMARY KEY,
                image_path TEXT NOT NULL,
                pid INTEGER NOT NULL,
                camid INTEGER NOT NULL,
                embedding_json TEXT NOT NULL
            )
            """
        )
        existing_ids = {
            str(row[0])
            for row in con.execute("SELECT image_id FROM image_descriptions")
        }
        rows_to_process = [row for row in rows if row[0] not in existing_ids]
        insert_description_sql = """
            INSERT OR REPLACE INTO image_descriptions (
                image_id, image_path, pid, camid,
                description, description_json, facets_json, tokens_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        insert_embedding_sql = """
            INSERT OR REPLACE INTO image_embeddings (image_id, image_path, pid, camid, embedding_json)
            VALUES (?, ?, ?, ?, ?)
        """
        completed = len(existing_ids)
        if rows_to_process:
            with ThreadPoolExecutor(max_workers=min(max_concurrency, len(rows_to_process))) as pool:
                futures = [pool.submit(_process, row) for row in rows_to_process]
                for future in as_completed(futures):
                    image_id, image_path, pid, camid, record, embedding = future.result()
                    con.execute(
                        insert_description_sql,
                        (
                            image_id,
                            image_path.as_posix(),
                            pid,
                            camid,
                            record["description"],
                            json.dumps(record, sort_keys=True),
                            json.dumps(record["facets"], sort_keys=True),
                            json.dumps(_tokenise_facets(record["facets"]), sort_keys=True),
                        ),
                    )
                    con.execute(
                        insert_embedding_sql,
                        (
                            image_id,
                            image_path.as_posix(),
                            pid,
                            camid,
                            json.dumps([float(value) for value in embedding]),
                        ),
                    )
                    completed += 1
                    if completed % 100 == 0 or completed == len(rows):
                        con.commit()
                        print(f"  wrote {completed}/{len(rows)} rows to {output}", flush=True)
        con.execute("CREATE INDEX IF NOT EXISTS idx_image_descriptions_pid_camid ON image_descriptions(pid, camid)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_image_embeddings_pid_camid ON image_embeddings(pid, camid)")
        if is_gallery:
            con.execute(
                """
                CREATE VIEW gallery_descriptions AS
                SELECT image_id AS gallery_id, image_path, pid, camid,
                       description, description_json, facets_json, tokens_json
                FROM image_descriptions
                """
            )
            con.execute(
                """
                CREATE VIEW gallery_embeddings AS
                SELECT image_id AS gallery_id, image_path, pid, camid, embedding_json
                FROM image_embeddings
                """
            )
    return output


def build_description_dbs(
    *,
    partition_root: Path = _DEFAULT_PARTITION,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
    max_concurrency: int = 50,
    description_model: str = DEFAULT_DESCRIPTION_MODEL,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    description_extractor: Callable[[Path], dict[str, Any]] | None = None,
    embedding_extractor: Callable[[str], list[float]] | None = None,
    resume: bool = False,
) -> dict[str, Path]:
    query_dir = partition_root / "query"
    gallery_dir = partition_root / "bounding_box_test"
    if not query_dir.is_dir():
        raise FileNotFoundError(f"query directory not found: {query_dir}")
    if not gallery_dir.is_dir():
        raise FileNotFoundError(f"gallery directory not found: {gallery_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "query": build_description_db(
            image_dir=query_dir,
            output=output_dir / "query_descriptions.sqlite",
            is_gallery=False,
            max_concurrency=max_concurrency,
            description_model=description_model,
            embedding_model=embedding_model,
            description_extractor=description_extractor,
            embedding_extractor=embedding_extractor,
            resume=resume,
        ),
        "gallery": build_description_db(
            image_dir=gallery_dir,
            output=output_dir / "gallery_descriptions.sqlite",
            is_gallery=True,
            max_concurrency=max_concurrency,
            description_model=description_model,
            embedding_model=embedding_model,
            description_extractor=description_extractor,
            embedding_extractor=embedding_extractor,
            resume=resume,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build query/gallery description DBs")
    parser.add_argument("--partition-root", type=Path, default=_DEFAULT_PARTITION)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--description-model", default=DEFAULT_DESCRIPTION_MODEL)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--max-concurrency", type=int, default=50)
    parser.add_argument("--resume", action="store_true", help="Reuse existing rows in output SQLite files.")
    args = parser.parse_args()

    outputs = build_description_dbs(
        partition_root=args.partition_root,
        output_dir=args.output_dir,
        description_model=args.description_model,
        embedding_model=args.embedding_model,
        max_concurrency=args.max_concurrency,
        resume=args.resume,
    )
    print(f"Wrote query description DB to {outputs['query']}")
    print(f"Wrote gallery description DB to {outputs['gallery']}")


if __name__ == "__main__":
    main()
