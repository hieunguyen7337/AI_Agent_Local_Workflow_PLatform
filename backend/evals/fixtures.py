"""YAML fixture loader for eval runs."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


class Fixture(BaseModel):
    id: str
    input: str
    expected: str
    test_code: str | None = None
    approval_decision: Literal["approved", "rejected"] | None = None
    approval_comment: str | None = None
    meta: dict = {}


def load_fixtures(path: Path) -> list[Fixture]:
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, list):
        raise ValueError(f"Expected a list of fixtures in {path}, got {type(raw).__name__}")
    return [Fixture.model_validate(item) for item in raw]
