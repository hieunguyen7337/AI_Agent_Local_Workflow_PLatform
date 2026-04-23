"""Budget enforcement. Checked after a node completes, before the next node dispatches.

A single runaway node can overshoot by one node's worth of cost/latency.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from backend.runtime.errors import BudgetExceededError


@dataclass
class BudgetEnforcer:
    cost_budget_usd: float
    latency_budget_ms: float
    started_ns: int | None = None
    cost_accum_usd: float = 0.0

    def start(self) -> None:
        self.started_ns = time.monotonic_ns()

    def add_cost(self, usd: float) -> None:
        self.cost_accum_usd += usd

    def latency_ms(self) -> float:
        if self.started_ns is None:
            return 0.0
        return (time.monotonic_ns() - self.started_ns) / 1_000_000

    def check(self) -> None:
        """Raise BudgetExceededError if any budget is exceeded."""
        if self.cost_accum_usd > self.cost_budget_usd:
            raise BudgetExceededError("cost", self.cost_budget_usd, self.cost_accum_usd)
        elapsed = self.latency_ms()
        if elapsed > self.latency_budget_ms:
            raise BudgetExceededError("latency", self.latency_budget_ms, elapsed)
