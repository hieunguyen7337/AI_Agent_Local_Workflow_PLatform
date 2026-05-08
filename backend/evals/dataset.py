"""Generalized local dataset eval adapters."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from backend.evals.metrics import summarize
from backend.repo_root import (
    find_repo_root,
    looks_like_dataset_path_string,
    resolve_dataset_eval_config_path,
    resolve_dataset_path_str,
)
from backend.runtime.cancellation import CancellationController
from backend.runtime.artifacts import AEST_UTC_OFFSET, make_eval_id
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
    repo_root = find_repo_root()
    config = load_dataset_eval_config(config_path)
    dataset_path = resolve_dataset_eval_config_path(
        config.dataset_path, base=config_path.parent, repo_root=repo_root
    )
    dataset_format = config.dataset_format or _infer_dataset_format(dataset_path)
    rows = load_dataset_rows(dataset_path, dataset_format)

    suffix = _eval_suffix(rows)
    eval_root = output_path.parent if output_path else runs_root / "evals" / workflow / make_eval_id(workflow, suffix=suffix)
    output_path = output_path or eval_root / "eval_summary.json"

    input_states = [_map_input_state(row, config.input_mapping, repo_root=repo_root) for row in rows]
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
        runs_root=eval_root,
        shared_run_dir=eval_root,
        use_checkpointer=False,
        write_manifest_files=False,
        write_audit_summary=False,
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
                "final_state": result.final_state,
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
    _write_eval_artifacts(eval_root=eval_root, summary_path=output_path, summary=out, workflow=workflow, rows=results)
    return out


def _eval_suffix(rows: list[dict[str, Any]]) -> str:
    if rows and isinstance(rows[0].get("gallery_ids"), list):
        return f"{len(rows)}q_{len(rows[0]['gallery_ids'])}g"
    return f"{len(rows)}rows"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_eval_artifacts(
    *,
    eval_root: Path,
    summary_path: Path,
    summary: dict[str, Any],
    workflow: str,
    rows: list[dict[str, Any]],
) -> None:
    eval_root.mkdir(parents=True, exist_ok=True)
    failed_rows = [row for row in rows if row.get("status") != "ok" or not row.get("pass")]
    compact_rows = [
        {
            "row_index": row["row_index"],
            "run_id": row["run_id"],
            "status": row["status"],
            "error": row["error"],
            "pass": row["pass"],
            "cost_usd": row["cost_usd"],
            "latency_ms": row["latency_ms"],
            "query_id": row["input_state"].get("user_input"),
            "ranked_gallery_ids": row["final_state"].get("ranked_gallery_ids"),
            "scorers": row["scorers"],
        }
        for row in rows
    ]
    rows_path = eval_root / "rows.jsonl"
    failed_path = eval_root / "failed_rows.jsonl"
    manifest_path = eval_root / "manifest.json"
    summary["artifacts"] = {
        "eval_root": eval_root.as_posix(),
        "eval_summary": summary_path.as_posix(),
        "rows_jsonl": rows_path.as_posix(),
        "failed_rows_jsonl": failed_path.as_posix(),
        "node_events_jsonl": (eval_root / "node_events.jsonl").as_posix(),
        "manifest": manifest_path.as_posix(),
        "telemetry_db": (eval_root / "telemetry.db").as_posix(),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_jsonl(rows_path, compact_rows)
    _write_jsonl(failed_path, [row for row in compact_rows if row["status"] != "ok" or not row["pass"]])
    manifest_path.write_text(
        json.dumps(
            {
                "workflow": workflow,
                "kind": "dataset_eval",
                "timezone": "AEST",
                "utc_offset": AEST_UTC_OFFSET,
                "row_count": summary["row_count"],
                "completed_run_count": summary["completed_run_count"],
                "artifacts": summary["artifacts"],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


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


def _map_input_state(
    row: dict[str, Any], mapping: dict[str, str], *, repo_root: Path | None = None
) -> dict[str, Any]:
    root = repo_root or find_repo_root()
    if not mapping:
        state = dict(row)
    else:
        state = {}
        for state_key, row_path in mapping.items():
            _set_path(state, state_key, _get_path(row, row_path))
    return _resolve_paths_in_structure(state, root)


def _resolve_paths_in_structure(obj: Any, repo_root: Path) -> Any:
    if isinstance(obj, str):
        if looks_like_dataset_path_string(obj):
            return resolve_dataset_path_str(obj, repo_root=repo_root)
        return obj
    if isinstance(obj, dict):
        return {key: _resolve_paths_in_structure(val, repo_root) for key, val in obj.items()}
    if isinstance(obj, list):
        return [_resolve_paths_in_structure(item, repo_root) for item in obj]
    return obj


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


def map_cmc_filtered_ranked_pairs(
    ranked_ids: list[str],
    *,
    gallery_ids: list[str],
    gallery_pids: list[int],
    gallery_camids: list[int],
    query_pid: int,
    query_camid: int,
) -> tuple[int, list[tuple[str, int]]]:
    """Build the junk-filtered ranking used by ``map_cmc`` / ``_compute_map_cmc``.

    Returns ``(total_relevant, filtered_pairs)`` where ``total_relevant`` counts
    gallery items with ``pid == query_pid`` and ``camid != query_camid``, and
    ``filtered_pairs`` is ``(gallery_id, pid)`` in retrieval order after the
    same junk rules as the scorer (unknown id dropped, same pid+query cam
    dropped, background pids dropped).

    Pair this with :func:`map_cmc_hit_relevant_within_k` for per-``k`` CMC-style
    hits without duplicating filter logic.
    """
    qpid = int(query_pid)
    qcam = int(query_camid)
    lookup: dict[str, tuple[int, int]] = {
        str(gid): (int(pid), int(camid))
        for gid, pid, camid in zip(gallery_ids, gallery_pids, gallery_camids)
    }
    total_relevant = sum(
        1 for pid, camid in zip(gallery_pids, gallery_camids)
        if int(pid) == qpid and int(camid) != qcam
    )
    filtered: list[tuple[str, int]] = []
    for gid in ranked_ids:
        entry = lookup.get(str(gid))
        if entry is None:
            continue
        pid, camid = entry
        if pid == qpid and camid == qcam:
            continue
        if pid in {-1, 0}:
            continue
        filtered.append((gid, pid))
    return total_relevant, filtered


def map_cmc_hit_relevant_within_k(
    filtered_pairs: list[tuple[str, int]],
    *,
    query_pid: int,
    k: int,
) -> bool:
    """Whether a ``pid == query_pid`` item appears in the first ``k`` filtered slots (CMC@k style)."""
    qpid = int(query_pid)
    for rank_idx, (_gid, pid) in enumerate(filtered_pairs, start=1):
        if rank_idx > k:
            break
        if pid == qpid:
            return True
    return False


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
    total_relevant, filtered = map_cmc_filtered_ranked_pairs(
        ranked_ids,
        gallery_ids=gallery_ids,
        gallery_pids=gallery_pids,
        gallery_camids=gallery_camids,
        query_pid=query_pid,
        query_camid=query_camid,
    )

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



def _infer_dataset_format(path: Path) -> Literal["csv", "jsonl", "yaml"]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".jsonl":
        return "jsonl"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    raise ValueError(f"Cannot infer dataset format from {path}")
