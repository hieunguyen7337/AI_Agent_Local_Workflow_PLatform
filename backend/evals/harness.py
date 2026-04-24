"""Eval harness: run a workflow N times against a fixture set, emit metrics JSON."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from backend.evals.fixtures import Fixture, load_fixtures
from backend.evals.metrics import EvalSummary, confidence_intervals, summarize
from backend.evals.regression import build_baseline_snapshot, compare_against_baseline
from backend.graphspec import load_workflow_metadata
from backend.builder.api import GraphMetadata
from backend.runtime.cancellation import CancellationController
from backend.runtime.executor import run_graph

EVALS_ROOT = Path("evals")


def run_eval(
    *,
    workflow: str,
    n_per_fixture: int = 4,
    fixtures_path: Path | None = None,
    runs_root: Path = Path("runs"),
    output_path: Path | None = None,
    baseline_path: Path | None = None,
    update_baseline: bool = False,
    cancellation: CancellationController | None = None,
) -> dict:
    metadata = load_workflow_metadata(workflow)
    return run_eval_for_metadata(
        workflow=workflow,
        metadata=metadata,
        n_per_fixture=n_per_fixture,
        fixtures_path=fixtures_path,
        runs_root=runs_root,
        output_path=output_path,
        baseline_path=baseline_path,
        update_baseline=update_baseline,
        cancellation=cancellation,
    )


def run_eval_for_metadata(
    *,
    workflow: str,
    metadata: GraphMetadata,
    n_per_fixture: int = 4,
    fixtures_path: Path | None = None,
    runs_root: Path = Path("runs"),
    output_path: Path | None = None,
    baseline_path: Path | None = None,
    update_baseline: bool = False,
    cancellation: CancellationController | None = None,
    max_cost_usd: float | None = None,
) -> dict:
    has_tester_node = any(getattr(cfg, "kind", "") == "tester" for cfg in metadata.nodes.values())

    fixtures_path = fixtures_path or EVALS_ROOT / workflow / "fixtures.yaml"
    fixtures = load_fixtures(fixtures_path)

    all_results: list[dict] = []
    per_fixture_summaries: dict[str, EvalSummary] = {}
    per_fixture_ci: dict[str, dict] = {}
    stopped_cost_cap = False

    for fx in fixtures:
        per_fx: list[dict] = []
        for i in range(n_per_fixture):
            if cancellation is not None and cancellation.is_cancelled():
                break
            initial_overrides = {"_test_code": fx.test_code} if fx.test_code else None
            result = run_graph(
                metadata,
                user_input=fx.input,
                expected=fx.expected,
                runs_root=runs_root,
                initial_state_overrides=initial_overrides,
                cancellation=cancellation,
            )
            passed = _infer_pass(result.final_state, fx.expected, has_tester_node=has_tester_node)
            per_fx.append(
                {
                    "fixture_id": fx.id,
                    "iteration": i,
                    "run_id": result.run_id,
                    "pass": passed,
                    "cost_usd": result.cost_usd,
                    "latency_ms": result.latency_ms,
                    "status": result.status,
                    "error": result.error,
                }
            )
            if max_cost_usd is not None:
                total_cost = sum(float(r.get("cost_usd", 0.0)) for r in all_results) + sum(
                    float(r.get("cost_usd", 0.0)) for r in per_fx
                )
                if total_cost >= max_cost_usd:
                    stopped_cost_cap = True
                    break
            if result.status == "cancelled":
                break
        if not per_fx:
            break
        per_fixture_summaries[fx.id] = summarize(per_fx)
        per_fixture_ci[fx.id] = _ci_dict(per_fx)
        all_results.extend(per_fx)
        if stopped_cost_cap:
            break
        if cancellation is not None and cancellation.is_cancelled():
            break
        if per_fx and per_fx[-1]["status"] == "cancelled":
            break

    overall = summarize(all_results)
    overall_ci = _ci_dict(all_results)
    out = {
        "workflow": workflow,
        "n_per_fixture": n_per_fixture,
        "fixture_count": len(fixtures),
        "status": (
            "cancelled"
            if cancellation is not None and cancellation.is_cancelled()
            else "stopped_cost_cap"
            if stopped_cost_cap
            else "ok"
        ),
        "completed_fixture_count": len(per_fixture_summaries),
        "completed_run_count": len(all_results),
        "overall": asdict(overall),
        "overall_ci": overall_ci,
        "per_fixture": {fid: asdict(s) for fid, s in per_fixture_summaries.items()},
        "per_fixture_ci": per_fixture_ci,
        "results": all_results,
    }

    baseline_path = baseline_path or EVALS_ROOT / workflow / "baseline.json"
    baseline_payload: dict | None = None
    if update_baseline and out["status"] != "cancelled":
        baseline_payload = build_baseline_snapshot(out)
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        with baseline_path.open("w", encoding="utf-8") as bf:
            json.dump(baseline_payload, bf, indent=2)
    elif baseline_path.exists():
        try:
            with baseline_path.open("r", encoding="utf-8") as bf:
                baseline_payload = json.load(bf)
        except Exception:
            baseline_payload = None

    if out["status"] == "cancelled":
        baseline_comparison = {
            "status": "cancelled",
            "baseline_path": str(baseline_path),
            "regression_detected": False,
            "metrics": [],
            "regressions": [],
        }
    elif baseline_payload is None:
        baseline_comparison = {
            "status": "no_baseline",
            "baseline_path": str(baseline_path),
            "regression_detected": False,
            "metrics": [],
            "regressions": [],
        }
    else:
        baseline_comparison = compare_against_baseline(current=out, baseline=baseline_payload)
        baseline_comparison["baseline_path"] = str(baseline_path)
        if update_baseline:
            baseline_comparison["status"] = "baseline_updated"
            baseline_comparison["regression_detected"] = False
            baseline_comparison["regressions"] = []

    out["baseline_comparison"] = baseline_comparison
    out["regression_detected"] = bool(baseline_comparison.get("regression_detected", False))

    output_path = output_path or runs_root / f"eval_{workflow}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    return out


def _ci_dict(results: list[dict]) -> dict[str, dict[str, float]]:
    ci = confidence_intervals(results)
    return {
        "pass_rate_95": asdict(ci.pass_rate),
        "mean_cost_usd_95": asdict(ci.mean_cost_usd),
        "mean_latency_ms_95": asdict(ci.mean_latency_ms),
    }


def _infer_pass(final_state: dict, expected: str, *, has_tester_node: bool) -> bool:
    """Prefer tester verdict when present; otherwise fallback to substring match."""
    if has_tester_node:
        return bool(final_state.get("tester_verdict", False))

    expected_text = str(expected or "").strip().lower()
    if not expected_text:
        return False

    candidate_keys = ("final_answer", "synthesiser_output", "answer", "output", "coder_output")
    for key in candidate_keys:
        value = final_state.get(key)
        if isinstance(value, str) and value.strip():
            return expected_text in value.lower()
    return False
