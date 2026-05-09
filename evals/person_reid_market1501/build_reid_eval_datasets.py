"""Create dataset row variants that point at ReID precomputed ranking DBs."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.repo_root import find_repo_root, to_repo_posix
from evals.person_reid_market1501.build_reid_retrieval_score_db import (
    DESCRIPTION_CHANNEL_MAP,
    VISUAL_FASTREID_CHANNEL,
    VISUAL_TORCHREID_CHANNEL,
)

_DEFAULT_BASE_DATASET = Path(__file__).parent / "partition_1000q_5000g" / "dataset.yaml"
_DEFAULT_OUTPUT_DIR = Path(__file__).parent / "partition_1000q_5000g"
_DEFAULT_RETRIEVAL_DB = (
    Path(__file__).parent
    / "precomputed_retrieval"
    / "market1501_1000q_5000g_retrieval_rankings_all_paths_v1.sqlite"
)


def _load_rows(path: Path) -> list[dict]:
    rows = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"expected a YAML list of row mappings in {path}")
    return [dict(row) for row in rows]


def _write_variant(
    *,
    rows: list[dict],
    output: Path,
    retrieval_db_path: str,
    visual_channel: str,
    retrieval_top_k: int,
) -> Path:
    out_rows = []
    for row in rows:
        item = dict(row)
        item["retrieval_score_db_path"] = retrieval_db_path
        item["retrieval_score_top_k"] = int(retrieval_top_k)
        item["channel_visual_image_embedding"] = visual_channel
        item["channel_description_semantic_text"] = DESCRIPTION_CHANNEL_MAP["description_semantic_text"]
        item["channel_description_structured_facets"] = DESCRIPTION_CHANNEL_MAP["description_structured_facets"]
        out_rows.append(item)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(out_rows, sort_keys=False), encoding="utf-8")
    return output


def build_reid_eval_datasets(
    *,
    base_dataset: Path = _DEFAULT_BASE_DATASET,
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
    retrieval_db: Path = _DEFAULT_RETRIEVAL_DB,
    retrieval_top_k: int = 200,
) -> dict[str, Path]:
    rows = _load_rows(base_dataset)
    repo_root = find_repo_root()
    retrieval_db_path = to_repo_posix(retrieval_db.resolve(), repo_root=repo_root)
    variants = {
        "torchreid": (VISUAL_TORCHREID_CHANNEL, output_dir / "dataset_torchreid_reid.yaml"),
        "fastreid": (VISUAL_FASTREID_CHANNEL, output_dir / "dataset_fastreid_reid.yaml"),
    }
    return {
        name: _write_variant(
            rows=rows,
            output=output,
            retrieval_db_path=retrieval_db_path,
            visual_channel=channel,
            retrieval_top_k=retrieval_top_k,
        )
        for name, (channel, output) in variants.items()
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build ReID eval dataset row variants")
    parser.add_argument("--base-dataset", type=Path, default=_DEFAULT_BASE_DATASET)
    parser.add_argument("--output-dir", type=Path, default=_DEFAULT_OUTPUT_DIR)
    parser.add_argument("--retrieval-db", type=Path, default=_DEFAULT_RETRIEVAL_DB)
    parser.add_argument("--retrieval-top-k", type=int, default=200)
    args = parser.parse_args()
    outputs = build_reid_eval_datasets(
        base_dataset=args.base_dataset,
        output_dir=args.output_dir,
        retrieval_db=args.retrieval_db,
        retrieval_top_k=args.retrieval_top_k,
    )
    for name, path in outputs.items():
        print(f"Wrote {name} dataset rows to {path}")


if __name__ == "__main__":
    main()
