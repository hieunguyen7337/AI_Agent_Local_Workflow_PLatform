"""Repository root discovery and repo-relative paths for dataset/YAML artifacts."""
from __future__ import annotations

from pathlib import Path

_PYPROJECT = "pyproject.toml"


def find_repo_root(start: Path | None = None) -> Path:
    """Return the directory containing ``pyproject.toml``, or raise if not found."""
    here = (start or Path(__file__).resolve()).parent
    for _ in range(16):
        if (here / _PYPROJECT).is_file():
            return here
        if here.parent == here:
            break
        here = here.parent
    raise RuntimeError(
        f"Could not locate repository root ({_PYPROJECT}) starting from {start or Path(__file__)}."
    )


def to_repo_posix(path: Path, *, repo_root: Path | None = None) -> str:
    """Return a posix path string: relative to the repo root when under it, else absolute."""
    resolved = path.resolve()
    root = repo_root or find_repo_root()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def looks_like_dataset_path_string(value: str) -> bool:
    """Whether ``value`` should be resolved as a filesystem path (vs an ID or label)."""
    text = value.strip()
    if not text:
        return False
    p = Path(text)
    if p.is_absolute():
        return True
    if "/" in text or "\\" in text:
        return True
    if text.startswith(("evals/", "dataset/", "./")) or text.startswith(".."):
        return True
    return False


def resolve_dataset_path_str(value: str, *, repo_root: Path | None = None) -> str:
    """Resolve a dataset workflow/YAML path string to a normalized absolute posix path."""
    text = value.strip()
    root = repo_root or find_repo_root()
    p = Path(text)
    if p.is_absolute():
        return p.resolve().as_posix()
    return (root / p).resolve().as_posix()


def resolve_dataset_eval_config_path(path: str, *, base: Path, repo_root: Path | None = None) -> Path:
    """Resolve ``dataset_path`` from a dataset_eval YAML file.

    Relative paths whose first segment is ``evals`` or ``dataset`` are resolved from the
    repository root. All other relative paths are resolved from ``base`` (the config
    file's directory).
    """
    root = repo_root or find_repo_root()
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    parts = candidate.parts
    if parts and parts[0] in {"evals", "dataset"}:
        return (root / candidate).resolve()
    return (base / candidate).resolve()
