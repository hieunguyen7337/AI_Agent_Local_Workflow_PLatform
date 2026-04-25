"""Multi-candidate GraphSpec optimization proposal service."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from backend.providers import call_provider
from backend.providers.base import ProviderError
from backend.providers.pricing import DEFAULT_OPENAI_MODEL

from .loader import graph_spec_to_metadata, load_graph_spec_source
from .models import GraphSpec
from .mutation import _diff_yaml, _extract_summary, _extract_yaml


@dataclass
class OptimizationCandidate:
    candidate_id: str
    status: str
    summary: str
    proposed_yaml: str
    diff: str
    validation_errors: list[str]
    eval: dict[str, Any] | None = None
    run_artifact: str | None = None
    rank: int | None = None
    recommended: bool = False


@dataclass
class OptimizationReport:
    workflow: str
    status: str
    recommended_candidate_id: str | None
    report_artifact: str
    original_yaml: str
    candidates: list[OptimizationCandidate]


def optimize_proposals(
    *,
    workflow: str,
    goal: str,
    constraints: str | None = None,
    candidate_count: int = 3,
    n_per_fixture: int = 1,
    max_cost_usd: float = 5.0,
    provider: str = "openai",
    model: str = DEFAULT_OPENAI_MODEL,
    runs_root: Path = Path("runs"),
    evals_root: Path = Path("evals"),
) -> OptimizationReport:
    if not goal.strip():
        raise ValueError("goal is required")
    if candidate_count < 2 or candidate_count > 5:
        raise ValueError("candidate_count must be between 2 and 5")
    if n_per_fixture < 1:
        raise ValueError("n_per_fixture must be >= 1")
    if max_cost_usd <= 0:
        raise ValueError("max_cost_usd must be > 0")

    _spec, original_yaml, _source_path = load_graph_spec_source(workflow)
    fixtures_path = evals_root / workflow / "fixtures.yaml"
    if not fixtures_path.exists():
        raise FileNotFoundError(f"eval fixtures for workflow {workflow!r} not found")

    root = runs_root / f"optimization_{workflow}_{int(time.time())}"
    root.mkdir(parents=True, exist_ok=True)
    candidates: list[OptimizationCandidate] = []
    total_eval_cost = 0.0
    stopped_cost_cap = False

    for idx in range(1, candidate_count + 1):
        candidate_id = f"candidate_{idx}"
        candidate = _generate_candidate(
            workflow=workflow,
            original_yaml=original_yaml,
            goal=goal,
            constraints=constraints,
            candidate_id=candidate_id,
            candidate_count=candidate_count,
            provider=provider,
            model=model,
        )
        candidates.append(candidate)
        if candidate.status != "valid":
            continue

        remaining = max_cost_usd - total_eval_cost
        if remaining <= 0:
            stopped_cost_cap = True
            candidate.eval = {
                "status": "skipped_cost_cap",
                "completed_run_count": 0,
                "completed_fixture_count": 0,
                "overall": {},
                "overall_ci": {},
                "baseline_comparison": {},
            }
            continue

        payload = yaml.safe_load(candidate.proposed_yaml)
        spec = GraphSpec.model_validate(payload)
        metadata = graph_spec_to_metadata(spec)
        candidate_root = root / candidate_id
        output_path = candidate_root / "eval.json"
        from backend.evals.harness import run_eval_for_metadata

        out = run_eval_for_metadata(
            workflow=workflow,
            metadata=metadata,
            n_per_fixture=n_per_fixture,
            fixtures_path=fixtures_path,
            runs_root=candidate_root,
            output_path=output_path,
            baseline_path=evals_root / workflow / "baseline.json",
            max_cost_usd=remaining,
        )
        total_eval_cost += sum(float(item.get("cost_usd", 0.0)) for item in out.get("results", []))
        candidate.run_artifact = output_path.as_posix()
        candidate.eval = {
            "status": out.get("status", "ok"),
            "completed_run_count": out.get("completed_run_count", 0),
            "completed_fixture_count": out.get("completed_fixture_count", 0),
            "overall": out.get("overall", {}),
            "overall_ci": out.get("overall_ci", {}),
            "baseline_comparison": out.get("baseline_comparison", {}),
        }
        if out.get("status") == "stopped_cost_cap" or total_eval_cost >= max_cost_usd:
            stopped_cost_cap = True

    recommended = _rank_candidates(candidates)
    status = "stopped_cost_cap" if stopped_cost_cap else "ok"
    if recommended is None and not stopped_cost_cap:
        status = "no_recommendation"

    report_path = root / "report.json"
    report = OptimizationReport(
        workflow=workflow,
        status=status,
        recommended_candidate_id=recommended.candidate_id if recommended else None,
        report_artifact=report_path.as_posix(),
        original_yaml=original_yaml,
        candidates=candidates,
    )
    report_path.write_text(json.dumps(_report_to_dict(report), indent=2), encoding="utf-8")
    return report


def _generate_candidate(
    *,
    workflow: str,
    original_yaml: str,
    goal: str,
    constraints: str | None,
    candidate_id: str,
    candidate_count: int,
    provider: str,
    model: str,
) -> OptimizationCandidate:
    try:
        response = call_provider(
            provider=provider,
            model=model,
            messages=_candidate_messages(
                workflow=workflow,
                original_yaml=original_yaml,
                goal=goal,
                constraints=constraints,
                candidate_id=candidate_id,
                candidate_count=candidate_count,
            ),
            temperature=0.35,
            max_tokens=6000,
            max_retries=2,
        )
    except ProviderError:
        raise

    proposed_yaml = _extract_yaml(response.text)
    summary = _extract_summary(response.text)
    validation_errors: list[str] = []
    status = "valid"
    try:
        payload = yaml.safe_load(proposed_yaml)
        if not isinstance(payload, dict):
            raise ValueError("proposed YAML must contain a mapping")
        spec = GraphSpec.model_validate(payload)
        if spec.name != workflow:
            raise ValueError(f"proposed spec name {spec.name!r} does not match workflow {workflow!r}")
    except (ValueError, ValidationError, yaml.YAMLError) as exc:
        status = "invalid"
        validation_errors.append(str(exc))

    return OptimizationCandidate(
        candidate_id=candidate_id,
        status=status,
        summary=summary,
        proposed_yaml=proposed_yaml,
        diff=_diff_yaml(original_yaml, proposed_yaml),
        validation_errors=validation_errors,
    )


def _candidate_messages(
    *,
    workflow: str,
    original_yaml: str,
    goal: str,
    constraints: str | None,
    candidate_id: str,
    candidate_count: int,
) -> list[dict]:
    constraint_text = constraints.strip() if constraints and constraints.strip() else "No extra constraints."
    return [
        {
            "role": "system",
            "content": (
                "You generate one candidate mutation for a YAML workflow GraphSpec. "
                "Return a complete revised YAML document, not a patch. "
                "Preserve schema_version, workflow name, valid node ids, valid edge targets, and GraphSpec compatibility. "
                "Do not include markdown fences unless unavoidable."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Workflow: {workflow}\n"
                f"Candidate: {candidate_id} of {candidate_count}\n\n"
                f"Goal:\n{goal.strip()}\n\n"
                f"Constraints:\n{constraint_text}\n\n"
                "Current YAML:\n"
                f"{original_yaml}\n\n"
                "Respond with one distinct complete revised YAML candidate. Optionally add a one-line summary "
                "before the YAML using the prefix 'Summary:'."
            ),
        },
    ]


def _rank_candidates(candidates: list[OptimizationCandidate]) -> OptimizationCandidate | None:
    rankable = [candidate for candidate in candidates if _is_rankable(candidate)]
    if not rankable:
        return None
    rankable.sort(key=_rank_key)
    for rank, candidate in enumerate(rankable, start=1):
        candidate.rank = rank
        candidate.recommended = rank == 1
    return rankable[0]


def _is_rankable(candidate: OptimizationCandidate) -> bool:
    if candidate.status != "valid" or not candidate.eval:
        return False
    overall = candidate.eval.get("overall") or {}
    return bool(candidate.eval.get("completed_run_count", 0)) and "pass_rate" in overall


def _rank_key(candidate: OptimizationCandidate) -> tuple:
    eval_payload = candidate.eval or {}
    overall = eval_payload.get("overall") or {}
    baseline = eval_payload.get("baseline_comparison") or {}
    regression = bool(baseline.get("regression_detected", False))
    pass_rate = float(overall.get("pass_rate", 0.0))
    mean_cost = float(overall.get("mean_cost_usd", 0.0))
    mean_latency = float(overall.get("mean_latency_ms", 0.0))
    return (regression, -pass_rate, mean_cost, mean_latency, candidate.candidate_id)


def _report_to_dict(report: OptimizationReport) -> dict:
    return {
        "workflow": report.workflow,
        "status": report.status,
        "recommended_candidate_id": report.recommended_candidate_id,
        "report_artifact": report.report_artifact,
        "original_yaml": report.original_yaml,
        "candidates": [asdict(candidate) for candidate in report.candidates],
    }
