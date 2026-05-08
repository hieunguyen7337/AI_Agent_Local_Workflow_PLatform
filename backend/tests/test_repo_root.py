"""Tests for repository-root-relative dataset paths."""
from __future__ import annotations

from pathlib import Path

from backend.repo_root import (
    find_repo_root,
    looks_like_dataset_path_string,
    resolve_dataset_eval_config_path,
    resolve_dataset_path_str,
    to_repo_posix,
)


def test_find_repo_root_contains_pyproject():
    root = find_repo_root()
    assert (root / "pyproject.toml").is_file()


def test_resolve_dataset_path_str_known_eval_file():
    root = find_repo_root()
    resolved = resolve_dataset_path_str("evals/person_reid_market1501/dataset_eval.yaml")
    assert Path(resolved) == (root / "evals/person_reid_market1501/dataset_eval.yaml").resolve()
    assert Path(resolved).is_file()


def test_looks_like_dataset_path_string_excludes_bare_ids():
    assert looks_like_dataset_path_string("visual_image_embedding") is False
    assert looks_like_dataset_path_string("0001_c2s1_000301_00.jpg") is False
    assert looks_like_dataset_path_string("evals/foo/bar.sqlite") is True
    assert looks_like_dataset_path_string("partition/query/x.jpg") is True


def test_resolve_dataset_eval_config_path_prefers_repo_root_for_evals_prefix():
    root = find_repo_root()
    p = resolve_dataset_eval_config_path(
        "evals/person_reid_market1501/dataset_eval.yaml",
        base=Path("/nonexistent_config_dir"),
        repo_root=root,
    )
    assert p == (root / "evals/person_reid_market1501/dataset_eval.yaml").resolve()


def test_resolve_dataset_eval_config_path_config_dir_relative_without_evals_prefix(tmp_path: Path):
    root = find_repo_root()
    cfg_dir = tmp_path / "evals" / "demo"
    cfg_dir.mkdir(parents=True)
    p = resolve_dataset_eval_config_path("data.yaml", base=cfg_dir, repo_root=root)
    assert p == (cfg_dir / "data.yaml").resolve()


def test_to_repo_posix_under_repo_is_relative_string():
    root = find_repo_root()
    posix = to_repo_posix(root / "pyproject.toml", repo_root=root)
    assert posix == "pyproject.toml"
