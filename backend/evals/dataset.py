"""Generalized local dataset eval adapters."""
from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from backend.evals.metrics import summarize
from backend.runtime.cancellation import CancellationController
from backend.runtime.functions import DEFAULT_MAX_CONCURRENCY, WorkflowBatchItem, run_workflow_batch


class DatasetEvalConfig(BaseModel):
    dataset_path: str = Field(min_length=1)
    dataset_format: Literal["csv", "jsonl", "yaml"] | None = None
    input_mapping: dict[str, str] = Field(default_factory=dict)
    scorers: list[dict[str, Any]] = Field(default_factory=list)
    max_concurrency: int = Field(DEFAULT_MAX_CONCURRENCY, ge=1)


def run_dataset_eval(
    *,
    workflow: str,
    config_path: Path,
    runs_root: Path = Path("runs"),
    output_path: Path | None = None,
    max_concurrency: int | None = None,
    cancellation: CancellationController | None = None,
) -> dict[str, Any]:
    config = load_dataset_eval_config(config_path)
    dataset_path = _resolve_path(config.dataset_path, base=config_path.parent)
    dataset_format = config.dataset_format or _infer_dataset_format(dataset_path)
    rows = load_dataset_rows(dataset_path, dataset_format)

    eval_root = output_path.parent if output_path else runs_root / f"dataset_eval_{workflow}_{int(time.time())}"
    output_path = output_path or eval_root / "eval.json"

    input_states = [_map_input_state(row, config.input_mapping) for row in rows]
    batch_results = run_workflow_batch(
        workflow,
        [
            WorkflowBatchItem(
                id=str(index),
                input_state=input_state,
                expected=str(input_state["_expected"]) if "_expected" in input_state else None,
            )
            for index, input_state in enumerate(input_states)
        ],
        max_concurrency=max_concurrency or config.max_concurrency,
        runs_root=eval_root / "runs",
        cancellation=cancellation,
    )

    results: list[dict[str, Any]] = []
    status = "cancelled" if cancellation is not None and cancellation.is_cancelled() else "ok"
    for index, (row, input_state, result) in enumerate(zip(rows, input_states, batch_results)):
        scorer_results = []
        for scorer in config.scorers:
            try:
                scorer_results.append(_score(scorer, row=row, final_state=result.final_state))
            except Exception as exc:
                scorer_results.append(
                    {
                        "id": str(scorer.get("id") or scorer.get("type") or "scorer"),
                        "type": str(scorer.get("type") or ""),
                        "pass": False,
                        "actual": None,
                        "expected": None,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        passed = all(item["pass"] for item in scorer_results) if scorer_results else result.status == "ok"
        results.append(
            {
                "row_index": index,
                "run_id": result.run_id,
                "run_dir": result.run_dir.as_posix() if result.run_dir else None,
                "status": result.status,
                "error": result.error,
                "pass": passed,
                "cost_usd": result.cost_usd,
                "latency_ms": result.latency_ms,
                "input_state": input_state,
                "scorers": scorer_results,
            }
        )
        if result.status == "cancelled":
            status = "cancelled"

    overall = summarize(results)
    out = {
        "workflow": workflow,
        "status": status,
        "config_path": config_path.as_posix(),
        "dataset_path": dataset_path.as_posix(),
        "dataset_format": dataset_format,
        "row_count": len(rows),
        "completed_run_count": len(results),
        "overall": asdict(overall),
        "results": results,
        "output_path": output_path.as_posix(),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def load_dataset_eval_config(path: Path) -> DatasetEvalConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected a mapping in {path}")
    return DatasetEvalConfig.model_validate(raw)


def load_dataset_rows(path: Path, dataset_format: str) -> list[dict[str, Any]]:
    if dataset_format == "csv":
        with path.open("r", encoding="utf-8", newline="") as f:
            return [dict(row) for row in csv.DictReader(f)]
    if dataset_format == "jsonl":
        rows = []
        with path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    raise ValueError(f"Expected JSON object on line {line_no} in {path}")
                rows.append(item)
        return rows
    if dataset_format == "yaml":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError(f"Expected a list in {path}")
        if not all(isinstance(item, dict) for item in raw):
            raise ValueError(f"Expected every item in {path} to be a mapping")
        return [dict(item) for item in raw]
    raise ValueError(f"Unsupported dataset format {dataset_format!r}")


def _map_input_state(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    if not mapping:
        return dict(row)
    state: dict[str, Any] = {}
    for state_key, row_path in mapping.items():
        _set_path(state, state_key, _get_path(row, row_path))
    return state


def _score(scorer: dict[str, Any], *, row: dict[str, Any], final_state: dict[str, Any]) -> dict[str, Any]:
    scorer_type = str(scorer.get("type", ""))
    scorer_id = str(scorer.get("id") or scorer_type)
    actual = _resolve_value(scorer.get("actual"), row=row, final_state=final_state)
    expected = _resolve_value(scorer.get("expected"), row=row, final_state=final_state)

    if scorer_type == "exact":
        passed = actual == expected
    elif scorer_type == "substring":
        case_sensitive = bool(scorer.get("case_sensitive", False))
        actual_text = "" if actual is None else str(actual)
        expected_text = "" if expected is None else str(expected)
        if not case_sensitive:
            actual_text = actual_text.lower()
            expected_text = expected_text.lower()
        passed = expected_text in actual_text
    elif scorer_type == "boolean":
        passed = bool(actual) is bool(expected)
    elif scorer_type == "numeric_threshold":
        operator = str(scorer.get("operator", ">="))
        threshold = float(scorer["threshold"])
        actual_number = float(actual)
        passed = _compare_number(actual_number, operator, threshold)
        expected = threshold
    elif scorer_type == "map_cmc":
        gallery_ids_list = _resolve_value(scorer.get("gallery_ids"), row=row, final_state=final_state)
        gallery_pids_list = _resolve_value(scorer.get("gallery_pids"), row=row, final_state=final_state)
        gallery_camids_list = _resolve_value(scorer.get("gallery_camids"), row=row, final_state=final_state)
        query_pid = int(_resolve_value(scorer.get("query_pid"), row=row, final_state=final_state))
        query_camid = int(_resolve_value(scorer.get("query_camid"), row=row, final_state=final_state))
        cmc_ks = list(scorer.get("cmc_ks", [1, 5, 10]))
        ranked_ids = _parse_ranked_ids(actual)
        metrics = _compute_map_cmc(
            ranked_ids,
            gallery_ids=gallery_ids_list,
            gallery_pids=gallery_pids_list,
            gallery_camids=gallery_camids_list,
            query_pid=query_pid,
            query_camid=query_camid,
            cmc_ks=cmc_ks,
        )
        passed = metrics.get("cmc_1", 0) == 1
        actual = metrics
        expected = query_pid
    else:
        raise ValueError(f"Unsupported scorer type {scorer_type!r}")

    return {
        "id": scorer_id,
        "type": scorer_type,
        "pass": passed,
        "actual": actual,
        "expected": expected,
    }


def _resolve_value(source: Any, *, row: dict[str, Any], final_state: dict[str, Any]) -> Any:
    if isinstance(source, str) and source.startswith("row."):
        return _get_path(row, source.removeprefix("row."))
    if isinstance(source, str) and source.startswith("final_state."):
        return _get_path(final_state, source.removeprefix("final_state."))
    return source


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(f"path {path!r} not found")
        current = current[part]
    return current


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = data
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"cannot set nested path {path!r}")
        current = child
    current[parts[-1]] = value


def _compare_number(actual: float, operator: str, threshold: float) -> bool:
    if operator == ">=":
        return actual >= threshold
    if operator == ">":
        return actual > threshold
    if operator == "<=":
        return actual <= threshold
    if operator == "<":
        return actual < threshold
    if operator == "==":
        return actual == threshold
    raise ValueError(f"Unsupported numeric threshold operator {operator!r}")


def _parse_ranked_ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        text = value.strip()
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
            if isinstance(parsed, dict):
                inner = parsed.get("ranked") or parsed.get("ranked_ids") or []
                return [str(x) for x in inner]
        except (json.JSONDecodeError, TypeError):
            pass
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except (json.JSONDecodeError, TypeError):
                pass
    return []


def _compute_map_cmc(
    ranked_ids: list[str],
    *,
    gallery_ids: list[str],
    gallery_pids: list[int],
    gallery_camids: list[int],
    query_pid: int,
    query_camid: int,
    cmc_ks: list[int],
) -> dict[str, Any]:
    lookup: dict[str, tuple[int, int]] = {
        str(gid): (int(pid), int(camid))
        for gid, pid, camid in zip(gallery_ids, gallery_pids, gallery_camids)
    }
    # Relevant = same pid, different cam, not background junk
    total_relevant = sum(
        1 for pid, camid in zip(gallery_pids, gallery_camids)
        if int(pid) == query_pid and int(camid) != query_camid
    )
    # Filter ranked list: remove same-cam-same-pid junk and background (pid -1/0)
    filtered: list[tuple[str, int]] = []
    for gid in ranked_ids:
        entry = lookup.get(str(gid))
        if entry is None:
            continue
        pid, camid = entry
        if pid == query_pid and camid == query_camid:
            continue
        if pid in {-1, 0}:
            continue
        filtered.append((gid, pid))

    if total_relevant == 0:
        return {"ap": 0.0, **{f"cmc_{k}": 0 for k in cmc_ks}}

    # AP: interpolated precision at each relevant rank
    ap_sum = 0.0
    relevant_found = 0
    for rank_idx, (_, pid) in enumerate(filtered, start=1):
        if pid == query_pid:
            relevant_found += 1
            ap_sum += relevant_found / rank_idx
    ap = ap_sum / total_relevant

    # CMC@k: found first relevant at or before rank k
    found_at: int | None = None
    for rank_idx, (_, pid) in enumerate(filtered, start=1):
        if pid == query_pid:
            found_at = rank_idx
            break

    return {"ap": ap, **{f"cmc_{k}": (1 if found_at is not None and found_at <= k else 0) for k in cmc_ks}}


def _resolve_path(path: str, *, base: Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else base / candidate


def _infer_dataset_format(path: Path) -> Literal["csv", "jsonl", "yaml"]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    raise ValueError(f"Cannot infer dataset format from {path}")
