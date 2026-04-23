"""Regression threshold behavior for eval baseline comparisons."""
from __future__ import annotations

from backend.evals.regression import RegressionThresholds, compare_against_baseline


def test_compare_against_baseline_detects_regression():
    baseline = {"overall": {"pass_rate": 1.0, "mean_cost_usd": 0.01, "mean_latency_ms": 100.0}}
    current = {"overall": {"pass_rate": 0.8, "mean_cost_usd": 0.02, "mean_latency_ms": 120.0}}
    out = compare_against_baseline(current=current, baseline=baseline)
    assert out["status"] == "compared"
    assert out["regression_detected"] is True
    assert {r["metric"] for r in out["regressions"]} == {
        "pass_rate",
        "mean_cost_usd",
        "mean_latency_ms",
    }


def test_compare_against_baseline_no_regression_when_within_thresholds():
    baseline = {"overall": {"pass_rate": 0.9, "mean_cost_usd": 0.01, "mean_latency_ms": 100.0}}
    current = {"overall": {"pass_rate": 0.89, "mean_cost_usd": 0.011, "mean_latency_ms": 108.0}}
    out = compare_against_baseline(
        current=current,
        baseline=baseline,
        thresholds=RegressionThresholds(
            pass_rate_drop_abs=0.02,
            mean_cost_increase_pct=0.15,
            mean_latency_increase_pct=0.15,
        ),
    )
    assert out["status"] == "compared"
    assert out["regression_detected"] is False
    assert out["regressions"] == []

