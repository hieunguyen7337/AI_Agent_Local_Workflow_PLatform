"""Baseline comparison and regression detection for eval outputs."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class RegressionThresholds:
    pass_rate_drop_abs: float = 0.02
    mean_cost_increase_pct: float = 0.15
    mean_latency_increase_pct: float = 0.15


def build_baseline_snapshot(eval_output: dict[str, Any]) -> dict[str, Any]:
    """Build a compact, versioned baseline payload from run_eval output."""
    return {
        "schema_version": 1,
        "workflow": eval_output.get("workflow"),
        "n_per_fixture": eval_output.get("n_per_fixture"),
        "overall": eval_output.get("overall", {}),
        "per_fixture": eval_output.get("per_fixture", {}),
    }


def compare_against_baseline(
    *,
    current: dict[str, Any],
    baseline: dict[str, Any],
    thresholds: RegressionThresholds | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or RegressionThresholds()
    current_overall = current.get("overall", {})
    baseline_overall = baseline.get("overall", {})

    required = ("pass_rate", "mean_cost_usd", "mean_latency_ms")
    missing = [k for k in required if k not in current_overall or k not in baseline_overall]
    if missing:
        return {
            "status": "invalid_baseline",
            "missing_metrics": missing,
            "regression_detected": False,
            "thresholds": asdict(thresholds),
            "metrics": [],
            "regressions": [],
        }

    pass_rate_delta = float(current_overall["pass_rate"]) - float(baseline_overall["pass_rate"])
    cost_delta = float(current_overall["mean_cost_usd"]) - float(baseline_overall["mean_cost_usd"])
    latency_delta = float(current_overall["mean_latency_ms"]) - float(
        baseline_overall["mean_latency_ms"]
    )

    cost_pct = _pct_change(cost_delta, float(baseline_overall["mean_cost_usd"]))
    latency_pct = _pct_change(latency_delta, float(baseline_overall["mean_latency_ms"]))

    metric_rows = [
        {
            "metric": "pass_rate",
            "baseline": float(baseline_overall["pass_rate"]),
            "current": float(current_overall["pass_rate"]),
            "delta": pass_rate_delta,
            "delta_pct": None,
            "is_regression": pass_rate_delta < -thresholds.pass_rate_drop_abs,
        },
        {
            "metric": "mean_cost_usd",
            "baseline": float(baseline_overall["mean_cost_usd"]),
            "current": float(current_overall["mean_cost_usd"]),
            "delta": cost_delta,
            "delta_pct": cost_pct,
            "is_regression": cost_pct > thresholds.mean_cost_increase_pct,
        },
        {
            "metric": "mean_latency_ms",
            "baseline": float(baseline_overall["mean_latency_ms"]),
            "current": float(current_overall["mean_latency_ms"]),
            "delta": latency_delta,
            "delta_pct": latency_pct,
            "is_regression": latency_pct > thresholds.mean_latency_increase_pct,
        },
    ]
    regressions = [r for r in metric_rows if r["is_regression"]]

    return {
        "status": "compared",
        "thresholds": asdict(thresholds),
        "metrics": metric_rows,
        "regressions": regressions,
        "regression_detected": bool(regressions),
    }


def _pct_change(delta: float, baseline: float) -> float:
    if baseline <= 0.0:
        return float("inf") if delta > 0.0 else 0.0
    return delta / baseline

