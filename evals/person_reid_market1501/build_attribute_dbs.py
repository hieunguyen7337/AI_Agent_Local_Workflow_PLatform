"""Build offline query and gallery attribute databases for a Market-1501 partition."""
from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import mimetypes
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any, Callable

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.providers import call_provider

_FILENAME_RE = re.compile(r"^(\d+)_c(\d+)s\d+_\d+_\d+\.jpg$")
_DEFAULT_PARTITION = Path(__file__).parent / "partition_100q_500g"
_DEFAULT_OUTPUT_DIR = Path(__file__).parent / "attribute_db"
DEFAULT_MODEL = "qwen/qwen3.5-9b"
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
ATTRIBUTE_KEYS = (
    "gender",
    "hair",
    "age",
    "clothing_type",
    "upper_body_clothes",
    "lower_body_clothes",
    "hat",
    "backpack",
    "bag",
    "handbag",
    "upper_body_clothes_color",
    "lower_body_clothes_color",
)
ATTRIBUTE_PROMPT = """Label every attribute of the main pedestrian in this image.
If multiple people are visible, focus on the most prominent (largest/most central) person.
If a body region is occluded or cropped, infer from whatever is visible.

Return only a raw JSON object with these exact keys and text values:

{
  "gender":                    "male" | "female",
  "hair":                      "short" | "long",
  "age":                       "child" | "teenager" | "adult" | "old",
  "clothing_type":             "dress" | "pants" | "shorts" | "skirt",
  "upper_body_clothes":        "long sleeve" | "short sleeve",
  "lower_body_clothes":        "long" | "short",
  "hat":                       "no" | "yes",
  "backpack":                  "no" | "yes",
  "bag":                       "no" | "yes",
  "handbag":                   "no" | "yes",
  "upper_body_clothes_color":  "black" | "white" | "red" | "purple" | "gray" | "blue" | "green" | "yellow" | "pink" | "orange" | "brown",
  "lower_body_clothes_color":  "black" | "white" | "pink" | "gray" | "blue" | "green" | "brown" | "yellow" | "purple" | "red"
}

For upper_body_clothes_color and lower_body_clothes_color, choose the dominant color.
No markdown, no explanation - raw JSON only."""


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
        rows.append((path.name, path.resolve(), pid, camid))
    return rows


def _image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _parse_attributes(value: Any) -> dict[str, str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            match = _JSON_OBJECT_RE.search(value)
            if match:
                try:
                    value = json.loads(match.group(0))
                except json.JSONDecodeError:
                    value = {}
            else:
                value = {}
    if not isinstance(value, dict):
        value = {}
    attrs = value.get("attributes") if isinstance(value.get("attributes"), dict) else value
    return {key: str(attrs.get(key, "")).strip().lower() for key in ATTRIBUTE_KEYS}


def make_live_attribute_extractor(model: str = DEFAULT_MODEL) -> Callable[[Path], dict[str, str]]:
    def _extract(image_path: Path) -> dict[str, str]:
        messages = [
            {
                "role": "system",
                "content": "You label pedestrian attributes for person re-identification. Return raw JSON only.",
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": ATTRIBUTE_PROMPT},
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
        return _parse_attributes(response.text)

    return _extract


def build_attribute_db(
    *,
    image_dir: Path,
    output: Path,
    max_concurrency: int = 50,
    model: str = DEFAULT_MODEL,
    attribute_extractor: Callable[[Path], dict[str, str]] | None = None,
) -> Path:
    rows = _load_images(image_dir)
    if not rows:
        raise ValueError(f"No valid images found in {image_dir}")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")

    extractor = attribute_extractor or make_live_attribute_extractor(model)
    if attribute_extractor is None:
        load_dotenv()
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("OPENROUTER_API_KEY is not set")

    def _extract(row: tuple[str, Path, int, int]) -> tuple[str, Path, int, int, dict[str, str]]:
        image_id, image_path, pid, camid = row
        return image_id, image_path, pid, camid, extractor(image_path)

    with ThreadPoolExecutor(max_workers=min(max_concurrency, len(rows))) as pool:
        extracted = list(pool.map(_extract, rows))

    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as con:
        con.execute("DROP VIEW IF EXISTS gallery_attributes")
        con.execute("DROP TABLE IF EXISTS image_attributes")
        attr_columns_sql = ",\n                ".join(f"{key} TEXT NOT NULL" for key in ATTRIBUTE_KEYS)
        con.execute(
            f"""
            CREATE TABLE image_attributes (
                image_id TEXT PRIMARY KEY,
                image_path TEXT NOT NULL,
                pid INTEGER NOT NULL,
                camid INTEGER NOT NULL,
                attributes_json TEXT NOT NULL,
                {attr_columns_sql}
            )
            """
        )
        attr_columns = ", ".join(ATTRIBUTE_KEYS)
        placeholders = ", ".join("?" for _ in ATTRIBUTE_KEYS)
        con.executemany(
            f"""
            INSERT INTO image_attributes (
                image_id, image_path, pid, camid, attributes_json,
                {attr_columns}
            )
            VALUES (?, ?, ?, ?, ?, {placeholders})
            """,
            [
                (
                    image_id,
                    image_path.as_posix(),
                    pid,
                    camid,
                    json.dumps(attrs, sort_keys=True),
                    *(attrs.get(key, "") for key in ATTRIBUTE_KEYS),
                )
                for image_id, image_path, pid, camid, attrs in extracted
            ],
        )
        con.execute(
            f"""
            CREATE VIEW gallery_attributes AS
            SELECT
                image_id AS gallery_id,
                image_path,
                pid,
                camid,
                attributes_json,
                {attr_columns}
            FROM image_attributes
            """
        )
        con.execute("CREATE INDEX idx_image_attributes_pid_camid ON image_attributes(pid, camid)")
        con.execute(
            "CREATE INDEX idx_image_attributes_terms ON image_attributes("
            + ", ".join(ATTRIBUTE_KEYS)
            + ")"
        )
    return output


def build_attribute_dbs(
    *,
    partition_root: Path = _DEFAULT_PARTITION,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
    max_concurrency: int = 50,
    model: str = DEFAULT_MODEL,
    attribute_extractor: Callable[[Path], dict[str, str]] | None = None,
) -> dict[str, Path]:
    query_dir = partition_root / "query"
    gallery_dir = partition_root / "bounding_box_test"
    if not query_dir.is_dir():
        raise FileNotFoundError(f"query directory not found: {query_dir}")
    if not gallery_dir.is_dir():
        raise FileNotFoundError(f"gallery directory not found: {gallery_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "query": build_attribute_db(
            image_dir=query_dir,
            output=output_dir / "query_attributes.sqlite",
            max_concurrency=max_concurrency,
            model=model,
            attribute_extractor=attribute_extractor,
        ),
        "gallery": build_attribute_db(
            image_dir=gallery_dir,
            output=output_dir / "gallery_attributes.sqlite",
            max_concurrency=max_concurrency,
            model=model,
            attribute_extractor=attribute_extractor,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build query/gallery attribute DBs")
    parser.add_argument("--partition-root", type=Path, default=_DEFAULT_PARTITION)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-concurrency", type=int, default=50)
    args = parser.parse_args()

    outputs = build_attribute_dbs(
        partition_root=args.partition_root,
        output_dir=args.output_dir,
        model=args.model,
        max_concurrency=args.max_concurrency,
    )
    print(f"Wrote query attribute DB to {outputs['query']}")
    print(f"Wrote gallery attribute DB to {outputs['gallery']}")


if __name__ == "__main__":
    main()
