"""Build offline query and gallery multimodal embedding databases for a partition."""
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
from typing import Callable

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.providers import call_embedding_provider

_FILENAME_RE = re.compile(r"^(\d+)_c(\d+)s\d+_\d+_\d+\.jpg$")
_DEFAULT_PARTITION = Path(__file__).parent / "partition_100q_500g"
_DEFAULT_OUTPUT_DIR = Path(__file__).parent / "embedding_db"
DEFAULT_MODEL = "google/gemini-embedding-2-preview"


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


def make_live_embedding_extractor(model: str = DEFAULT_MODEL) -> Callable[[Path], list[float]]:
    def _extract(image_path: Path) -> list[float]:
        response = call_embedding_provider(
            "openrouter",
            model=model,
            input_payload=[
                {
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": _image_data_url(image_path)},
                        }
                    ]
                }
            ],
            max_retries=3,
        )
        return [float(value) for value in response.embedding]

    return _extract


def build_embedding_db(
    *,
    image_dir: Path,
    output: Path,
    max_concurrency: int = 50,
    model: str = DEFAULT_MODEL,
    embedding_extractor: Callable[[Path], list[float]] | None = None,
) -> Path:
    rows = _load_images(image_dir)
    if not rows:
        raise ValueError(f"No valid images found in {image_dir}")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")

    extractor = embedding_extractor or make_live_embedding_extractor(model)
    if embedding_extractor is None:
        load_dotenv()
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise RuntimeError("OPENROUTER_API_KEY is not set")

    def _extract(row: tuple[str, Path, int, int]) -> tuple[str, Path, int, int, list[float]]:
        image_id, image_path, pid, camid = row
        return image_id, image_path, pid, camid, extractor(image_path)

    with ThreadPoolExecutor(max_workers=min(max_concurrency, len(rows))) as pool:
        extracted = list(pool.map(_extract, rows))

    output.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(output) as con:
        con.execute("DROP VIEW IF EXISTS gallery_embeddings")
        con.execute("DROP TABLE IF EXISTS image_embeddings")
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
        con.executemany(
            """
            INSERT INTO image_embeddings (image_id, image_path, pid, camid, embedding_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    image_id,
                    image_path.as_posix(),
                    pid,
                    camid,
                    json.dumps([float(value) for value in embedding]),
                )
                for image_id, image_path, pid, camid, embedding in extracted
            ],
        )
        con.execute(
            """
            CREATE VIEW gallery_embeddings AS
            SELECT image_id AS gallery_id, image_path, pid, camid, embedding_json
            FROM image_embeddings
            """
        )
        con.execute("CREATE INDEX idx_image_embeddings_pid_camid ON image_embeddings(pid, camid)")
    return output


def build_embedding_dbs(
    *,
    partition_root: Path = _DEFAULT_PARTITION,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
    max_concurrency: int = 50,
    model: str = DEFAULT_MODEL,
    embedding_extractor: Callable[[Path], list[float]] | None = None,
) -> dict[str, Path]:
    query_dir = partition_root / "query"
    gallery_dir = partition_root / "bounding_box_test"
    if not query_dir.is_dir():
        raise FileNotFoundError(f"query directory not found: {query_dir}")
    if not gallery_dir.is_dir():
        raise FileNotFoundError(f"gallery directory not found: {gallery_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "query": build_embedding_db(
            image_dir=query_dir,
            output=output_dir / "query_embeddings.sqlite",
            max_concurrency=max_concurrency,
            model=model,
            embedding_extractor=embedding_extractor,
        ),
        "gallery": build_embedding_db(
            image_dir=gallery_dir,
            output=output_dir / "gallery_embeddings.sqlite",
            max_concurrency=max_concurrency,
            model=model,
            embedding_extractor=embedding_extractor,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build query/gallery embedding DBs")
    parser.add_argument("--partition-root", type=Path, default=_DEFAULT_PARTITION)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-concurrency", type=int, default=50)
    args = parser.parse_args()

    outputs = build_embedding_dbs(
        partition_root=args.partition_root,
        output_dir=args.output_dir,
        model=args.model,
        max_concurrency=args.max_concurrency,
    )
    print(f"Wrote query embedding DB to {outputs['query']}")
    print(f"Wrote gallery embedding DB to {outputs['gallery']}")


if __name__ == "__main__":
    main()
