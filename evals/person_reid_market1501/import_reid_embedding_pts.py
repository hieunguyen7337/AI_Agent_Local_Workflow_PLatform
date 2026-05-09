"""Import Torchreid/FastReID image-mode `.pt` embeddings into SQLite DBs.

GPU inference happens outside this script through PBS/qsub in the ReID repos.
This CPU-side adapter only translates completed `.pt` artifacts into the same
`image_embeddings` schema already used by the person-ReID eval builders.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_FILENAME_RE = re.compile(r"^(\d+)_c(\d+)s\d+_\d+_\d+\.jpg$")
Source = Literal["torchreid", "fastreid"]
Split = Literal["query", "gallery"]


def _parse_market1501_id(image_id: str) -> tuple[int, int]:
    match = _FILENAME_RE.match(image_id)
    if not match:
        raise ValueError(f"unsupported Market1501 image id: {image_id!r}")
    pid, camid = int(match.group(1)), int(match.group(2))
    if pid in {-1, 0}:
        raise ValueError(f"background/junk Market1501 image id is not valid for eval import: {image_id!r}")
    return pid, camid


def _to_rows_tensor(value: Any) -> list[list[float]]:
    """Convert a torch/numpy/list tensor-like value into JSON-serializable rows."""
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, list):
        raise TypeError("embedding tensor could not be converted to a Python list")
    if value and not isinstance(value[0], list):
        value = [value]
    return [[float(item) for item in row] for row in value]


def _load_pt(path: Path) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover - depends on optional local env
        raise RuntimeError(
            "PyTorch is required to import .pt embedding artifacts. "
            "Run this script in an environment with torch installed."
        ) from exc

    try:
        payload = torch.load(path, map_location="cpu", weights_only=False)
    except TypeError:
        payload = torch.load(path, map_location="cpu")
    if not isinstance(payload, dict):
        raise ValueError(f"expected a dict payload in {path}")
    return payload


def extract_reid_rows(payload: dict[str, Any], *, source: Source) -> list[tuple[str, str, list[float]]]:
    """Extract `(image_id, image_path, embedding)` rows from a ReID `.pt` payload."""
    if source == "torchreid":
        paths = payload.get("items")
        embeddings = payload.get("embeddings")
        if not isinstance(paths, list) or embeddings is None:
            raise ValueError("Torchreid image-mode payload must contain `items` and `embeddings`")
    elif source == "fastreid":
        paths = payload.get("frame_paths")
        embeddings = payload.get("frame_embeddings")
        if not isinstance(paths, list) or embeddings is None:
            raise ValueError("FastReID image-mode payload must contain `frame_paths` and `frame_embeddings`")
    else:
        raise ValueError(f"unsupported source: {source}")

    embedding_rows = _to_rows_tensor(embeddings)
    if len(paths) != len(embedding_rows):
        raise ValueError(f"path/embedding count mismatch: {len(paths)} paths vs {len(embedding_rows)} embeddings")

    rows: list[tuple[str, str, list[float]]] = []
    for raw_path, embedding in zip(paths, embedding_rows):
        image_path = Path(str(raw_path))
        image_id = image_path.name
        _parse_market1501_id(image_id)
        rows.append((image_id, image_path.as_posix(), embedding))
    return rows


def write_embedding_db(*, rows: list[tuple[str, str, list[float]]], output: Path, split: Split) -> Path:
    if not rows:
        raise ValueError("cannot write an empty embedding DB")

    output.parent.mkdir(parents=True, exist_ok=True)
    view_name = "gallery_embeddings" if split == "gallery" else "query_embeddings"
    id_alias = "gallery_id" if split == "gallery" else "query_id"

    with sqlite3.connect(output) as con:
        con.execute(f"DROP VIEW IF EXISTS {view_name}")
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
        insert_rows = []
        for image_id, image_path, embedding in rows:
            pid, camid = _parse_market1501_id(image_id)
            insert_rows.append((image_id, image_path, pid, camid, json.dumps([float(v) for v in embedding])))
        con.executemany(
            """
            INSERT INTO image_embeddings (image_id, image_path, pid, camid, embedding_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            insert_rows,
        )
        con.execute(
            f"""
            CREATE VIEW {view_name} AS
            SELECT image_id AS {id_alias}, image_path, pid, camid, embedding_json
            FROM image_embeddings
            """
        )
        con.execute("CREATE INDEX idx_image_embeddings_pid_camid ON image_embeddings(pid, camid)")
    return output


def import_reid_embedding_pt(*, input_pt: Path, output: Path, source: Source, split: Split) -> Path:
    payload = _load_pt(input_pt)
    rows = extract_reid_rows(payload, source=source)
    return write_embedding_db(rows=rows, output=output, split=split)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import ReID .pt embeddings into an eval SQLite DB")
    parser.add_argument("--input-pt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", required=True, choices=["torchreid", "fastreid"])
    parser.add_argument("--split", required=True, choices=["query", "gallery"])
    args = parser.parse_args()

    output = import_reid_embedding_pt(
        input_pt=args.input_pt,
        output=args.output,
        source=args.source,
        split=args.split,
    )
    print(f"Wrote {args.source} {args.split} embedding DB to {output}")


if __name__ == "__main__":
    main()
