"""Aggregate eval metrics. M1: pass rate, mean cost, mean latency, p95 latency."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass
class EvalSummary:
    total_runs: int
    passes: int
    pass_rate: float  # passes / total
    mean_cost_usd: float
    mean_latency_ms: float
    p95_latency_ms: float
    cost_stdev_usd: float
    latency_stdev_ms: float


@dataclass
class ConfidenceInterval95:
    low: float
    high: float


@dataclass
class EvalConfidenceIntervals:
    pass_rate: ConfidenceInterval95
    mean_cost_usd: ConfidenceInterval95
    mean_latency_ms: ConfidenceInterval95


def summarize(results: list[dict]) -> EvalSummary:
    """results: list of {pass: bool, cost_usd: float, latency_ms: float}."""
    n = len(results)
    if n == 0:
        return EvalSummary(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    passes = sum(1 for r in results if r.get("pass"))
    costs = [r["cost_usd"] for r in results]
    latencies = sorted(r["latency_ms"] for r in results)
    p95_idx = max(0, min(n - 1, int(round(0.95 * (n - 1)))))
    return EvalSummary(
        total_runs=n,
        passes=passes,
        pass_rate=passes / n,
        mean_cost_usd=sum(costs) / n,
        mean_latency_ms=sum(latencies) / n,
        p95_latency_ms=latencies[p95_idx],
        cost_stdev_usd=statistics.pstdev(costs) if n > 1 else 0.0,
        latency_stdev_ms=statistics.pstdev(latencies) if n > 1 else 0.0,
    )


def wilson_ci_95(*, passes: int, total: int) -> ConfidenceInterval95:
    """95% Wilson interval for Bernoulli success rate."""
    if total <= 0:
        return ConfidenceInterval95(0.0, 0.0)
    z = 1.959963984540054
    p = passes / total
    den = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / den
    spread = (z / den) * math.sqrt((p * (1.0 - p) / total) + ((z * z) / (4.0 * total * total)))
    return ConfidenceInterval95(max(0.0, center - spread), min(1.0, center + spread))


def mean_ci_95(values: list[float]) -> ConfidenceInterval95:
    """95% CI for the mean using normal approximation."""
    n = len(values)
    if n <= 0:
        return ConfidenceInterval95(0.0, 0.0)
    mean = sum(values) / n
    if n == 1:
        return ConfidenceInterval95(mean, mean)
    stdev = statistics.pstdev(values)
    margin = 1.959963984540054 * (stdev / math.sqrt(n))
    return ConfidenceInterval95(max(0.0, mean - margin), mean + margin)


def confidence_intervals(results: list[dict]) -> EvalConfidenceIntervals:
    n = len(results)
    passes = sum(1 for r in results if r.get("pass"))
    costs = [float(r.get("cost_usd", 0.0)) for r in results]
    latencies = [float(r.get("latency_ms", 0.0)) for r in results]
    return EvalConfidenceIntervals(
        pass_rate=wilson_ci_95(passes=passes, total=n),
        mean_cost_usd=mean_ci_95(costs),
        mean_latency_ms=mean_ci_95(latencies),
    )
