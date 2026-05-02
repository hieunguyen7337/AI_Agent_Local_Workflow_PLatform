"""Build an offline Market-1501 gallery attribute database.

The online person_reid_market1501 workflow should not process the full gallery
for every query. This script preprocesses bounding_box_test images once and
stores gallery-side attribute representations in SQLite for retriever nodes.
"""
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
_DEFAULT_OUTPUT = Path(__file__).parent / "gallery_db" / "attributes.sqlite"
_MODEL = "qwen/qwen3.5-9b"
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
ATTRIBUTE_KEYS = (
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
ATTRIBUTE_PROMPT = """Label the main pedestrian in this image.
If multiple people are visible, focus on the most prominent (largest/most central) person.
If a body region is occluded or cropped, infer from whatever is visible.

Return only a raw JSON object with these exact keys and text values:

{
  "gender":               "male" | "female",
  "hair":                 "short" | "long",
  "clothing_type":        "dress" | "pants" | "shorts" | "skirt",
  "upper_body_clothes":   "long sleeve" | "short sleeve",
  "lower_body_clothes":   "long" | "short",
  "hat":                  "no" | "yes",
  "backpack":             "no" | "yes",
  "bag":                  "no" | "yes",
  "handbag":              "no" | "yes"
}

No markdown, no explanation - raw JSON only."""


def _parse_image(filename: str) -> tuple[int, int] | None:
    match = _FILENAME_RE.match(filename)
    if not match:
        return None
    pid, camid = int(match.group(1)), int(match.group(2))
    if pid in {-1, 0}:
        return None
    return pid, camid


def _load_gallery(gallery_dir: Path) -> list[tuple[str, Path, int, int]]:
    rows = []
    for path in sorted(gallery_dir.glob("*.jpg")):
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


def _live_attribute_extractor(image_path: Path) -> dict[str, str]:
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
        model=_MODEL,
        messages=messages,
        temperature=0.0,
        max_retries=3,
    )
    return _parse_attributes(response.text)


def build_gallery_db(
    *,
    market1501_root: Path,
    output: Path = _DEFAULT_OUTPUT,
    max_concurrency: int = 50,
    attribute_extractor: Callable[[Path], dict[str, str]] | None = None,
) -> Path:
    gallery_dir = market1501_root / "bounding_box_test"
    if not gallery_dir.is_dir():
        raise FileNotFoundError(f"gallery directory not found: {gallery_dir}")
    gallery_rows = _load_gallery(gallery_dir)
    if not gallery_rows:
        raise ValueError(f"No valid gallery images found in {gallery_dir}")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")

    extractor = attribute_extractor or _live_attribute_extractor
    if attribute_extractor is None:
        load_dotenv()
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("OPENROUTER_API_KEY is not set")

    def _extract(row: tuple[str, Path, int, int]) -> tuple[str, Path, int, int, dict[str, str]]:
        gallery_id, image_path, pid, camid = row
        return gallery_id, image_path, pid, camid, extractor(image_path)

    with ThreadPoolExecutor(max_workers=min(max_concurrency, len(gallery_rows))) as pool:
        extracted = list(pool.map(_extract, gallery_rows))

    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as con:
        con.execute("DROP TABLE IF EXISTS gallery_attributes")
        attr_columns_sql = ",\n                ".join(f"{key} TEXT NOT NULL" for key in ATTRIBUTE_KEYS)
        con.execute(
            f"""
            CREATE TABLE gallery_attributes (
                gallery_id TEXT PRIMARY KEY,
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
            INSERT INTO gallery_attributes (
                gallery_id, image_path, pid, camid, attributes_json,
                {attr_columns}
            )
            VALUES (?, ?, ?, ?, ?, {placeholders})
            """,
            [
                (
                    gallery_id,
                    image_path.as_posix(),
                    pid,
                    camid,
                    json.dumps(attrs, sort_keys=True),
                    *(attrs.get(key, "") for key in ATTRIBUTE_KEYS),
                )
                for gallery_id, image_path, pid, camid, attrs in extracted
            ],
        )
        con.execute("CREATE INDEX idx_gallery_attributes_pid_camid ON gallery_attributes(pid, camid)")
        con.execute(
            "CREATE INDEX idx_gallery_attributes_terms ON gallery_attributes("
            + ", ".join(ATTRIBUTE_KEYS)
            + ")"
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Market-1501 gallery attribute database")
    parser.add_argument("--market1501-root", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--max-concurrency", type=int, default=50)
    args = parser.parse_args()

    output = build_gallery_db(
        market1501_root=args.market1501_root,
        output=args.output,
        max_concurrency=args.max_concurrency,
    )
    print(f"Wrote gallery attribute database to {output}")


if __name__ == "__main__":
    main()
