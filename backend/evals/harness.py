"""Eval harness: run a workflow N times against a fixture set, emit metrics JSON."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from backend.approvals import decide_approval
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
    approval_path_coverage: dict[str, int] = {}
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
            eval_result = _build_eval_result(
                workflow=workflow,
                metadata=metadata,
                fixture=fx,
                iteration=i,
                result=result,
                runs_root=runs_root,
                has_tester_node=has_tester_node,
            )
            if eval_result.get("approval_decision"):
                decision = str(eval_result["approval_decision"])
                approval_path_coverage[decision] = approval_path_coverage.get(decision, 0) + 1
            per_fx.append(eval_result)
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
        "approval_path_coverage": approval_path_coverage,
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

    candidate_keys = (
        "final_answer",
        "nested_final_answer",
        "rag_answer",
        "synthesiser_output",
        "answer",
        "output",
        "coder_output",
    )
    for key in candidate_keys:
        value = final_state.get(key)
        if isinstance(value, str) and value.strip():
            return expected_text in value.lower()
    return False


def _build_eval_result(
    *,
    workflow: str,
    metadata: GraphMetadata,
    fixture: Fixture,
    iteration: int,
    result,
    runs_root: Path,
    has_tester_node: bool,
) -> dict:
    if result.status != "pending_approval":
        eval_result = {
            "fixture_id": fixture.id,
            "iteration": iteration,
            "run_id": result.run_id,
            "pass": _infer_pass(
                result.final_state,
                fixture.expected,
                has_tester_node=has_tester_node,
            ),
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
            "status": result.status,
            "error": result.error,
        }
        subgraphs = _read_subgraph_lineage(result.run_dir)
        if subgraphs:
            eval_result["subgraphs"] = subgraphs
        return eval_result

    # Detect nested approval subgraph: parent paused but has no own approval.json.
    pending_subgraph_path = result.run_dir / "pending_subgraph_approval.json"
    if pending_subgraph_path.exists():
        return _build_nested_subgraph_eval_result(
            workflow=workflow,
            fixture=fixture,
            iteration=iteration,
            result=result,
            pending_subgraph_path=pending_subgraph_path,
            runs_root=runs_root,
            has_tester_node=has_tester_node,
        )

    if fixture.approval_decision is None:
        return {
            "fixture_id": fixture.id,
            "iteration": iteration,
            "run_id": result.run_id,
            "source_run_id": result.run_id,
            "pass": False,
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
            "status": "pending_approval",
            "error": "fixture approval_decision is required for pending approval runs",
        }

    decision = decide_approval(
        run_id=result.run_id,
        decision=fixture.approval_decision,
        reviewer="eval",
        comment=fixture.approval_comment,
        runs_root=runs_root,
        metadata=metadata,
    )
    decision_artifact = decision.decision_artifact.as_posix()
    approval_resume_artifact = (decision.continuation_run_dir / "approval_resume.json").as_posix()
    passed = _infer_approval_pass(
        final_state=decision.continuation_final_state,
        expected=fixture.expected,
        decision=fixture.approval_decision,
        continuation_status=decision.continuation_status,
        has_tester_node=has_tester_node,
    )
    return {
        "fixture_id": fixture.id,
        "iteration": iteration,
        "run_id": decision.continuation_run_id,
        "source_run_id": result.run_id,
        "continuation_run_id": decision.continuation_run_id,
        "approval_decision": fixture.approval_decision,
        "decision_artifact": decision_artifact,
        "approval_resume_artifact": approval_resume_artifact,
        "pass": passed,
        "cost_usd": decision.continuation_cost_usd,
        "latency_ms": decision.continuation_latency_ms,
        "status": decision.continuation_status,
        "error": decision.continuation_error,
    }


def _build_nested_subgraph_eval_result(
    *,
    workflow: str,
    fixture: Fixture,
    iteration: int,
    result,
    pending_subgraph_path: Path,
    runs_root: Path,
    has_tester_node: bool,
) -> dict:
    if fixture.approval_decision is None:
        return {
            "fixture_id": fixture.id,
            "iteration": iteration,
            "run_id": result.run_id,
            "source_run_id": result.run_id,
            "pass": False,
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
            "status": "pending_approval",
            "error": "fixture approval_decision is required for nested subgraph approval runs",
        }

    try:
        psa = json.loads(pending_subgraph_path.read_text(encoding="utf-8"))
        child_run_id = str(psa["child_run_id"])
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        return {
            "fixture_id": fixture.id,
            "iteration": iteration,
            "run_id": result.run_id,
            "source_run_id": result.run_id,
            "pass": False,
            "cost_usd": result.cost_usd,
            "latency_ms": result.latency_ms,
            "status": "error",
            "error": f"could not read pending_subgraph_approval.json: {exc}",
        }

    # Decide the child approval; decide_approval auto-forks the parent continuation.
    decision = decide_approval(
        run_id=child_run_id,
        decision=fixture.approval_decision,
        reviewer="eval",
        comment=fixture.approval_comment,
        runs_root=runs_root,
        metadata=None,  # auto-loads child workflow metadata from approval artifact
    )

    # Score against the parent continuation's final state when available.
    if decision.parent_continuation_final_state is not None:
        passed = _infer_approval_pass(
            final_state=decision.parent_continuation_final_state,
            expected=fixture.expected,
            decision=fixture.approval_decision,
            continuation_status=decision.parent_continuation_status or "",
            has_tester_node=has_tester_node,
        )
        final_status = decision.parent_continuation_status or decision.continuation_status
        final_error = decision.parent_continuation_error or decision.continuation_error
    else:
        # No parent continuation forked (e.g. child continuation itself failed).
        passed = _infer_approval_pass(
            final_state=decision.continuation_final_state,
            expected=fixture.expected,
            decision=fixture.approval_decision,
            continuation_status=decision.continuation_status,
            has_tester_node=has_tester_node,
        )
        final_status = decision.continuation_status
        final_error = decision.continuation_error

    eval_result: dict = {
        "fixture_id": fixture.id,
        "iteration": iteration,
        "run_id": decision.parent_continuation_run_id or decision.continuation_run_id,
        "source_run_id": result.run_id,
        "child_run_id": child_run_id,
        "child_continuation_run_id": decision.continuation_run_id,
        "parent_continuation_run_id": decision.parent_continuation_run_id,
        "approval_decision": fixture.approval_decision,
        "decision_artifact": decision.decision_artifact.as_posix(),
        "pass": passed,
        "cost_usd": decision.continuation_cost_usd,
        "latency_ms": decision.continuation_latency_ms,
        "status": final_status,
        "error": final_error,
    }

    if decision.parent_continuation_run_dir is not None:
        sr_path = decision.parent_continuation_run_dir / "subgraph_resume.json"
        if sr_path.exists():
            eval_result["subgraph_resume_artifact"] = sr_path.as_posix()

    return eval_result


def _read_subgraph_lineage(run_dir: Path) -> list[dict]:
    subgraph_dir = run_dir / "subgraphs"
    if not subgraph_dir.exists():
        return []
    out: list[dict] = []
    for path in sorted(subgraph_dir.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(item, dict):
                item["artifact_path"] = path.as_posix()
                out.append(item)
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _infer_approval_pass(
    *,
    final_state: dict,
    expected: str,
    decision: str,
    continuation_status: str,
    has_tester_node: bool,
) -> bool:
    expected_text = str(expected or "").strip()
    if decision == "rejected" and not expected_text:
        return continuation_status == "ok"
    return _infer_pass(final_state, expected_text, has_tester_node=has_tester_node)
