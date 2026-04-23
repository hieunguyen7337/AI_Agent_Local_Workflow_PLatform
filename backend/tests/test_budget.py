"""Budget enforcer unit tests."""
from __future__ import annotations

import time

import pytest

from backend.budget.enforcer import BudgetEnforcer
from backend.runtime.errors import BudgetExceededError


def test_cost_budget_enforced():
    b = BudgetEnforcer(cost_budget_usd=0.01, latency_budget_ms=60_000)
    b.start()
    b.add_cost(0.005)
    b.check()  # fine
    b.add_cost(0.010)
    with pytest.raises(BudgetExceededError) as exc:
        b.check()
    assert exc.value.kind == "cost"


def test_latency_budget_enforced():
    b = BudgetEnforcer(cost_budget_usd=1.0, latency_budget_ms=5)
    b.start()
    time.sleep(0.02)
    with pytest.raises(BudgetExceededError) as exc:
        b.check()
    assert exc.value.kind == "latency"


def test_no_error_when_within_limits():
    b = BudgetEnforcer(cost_budget_usd=1.0, latency_budget_ms=60_000)
    b.start()
    b.add_cost(0.5)
    b.check()  # no error
