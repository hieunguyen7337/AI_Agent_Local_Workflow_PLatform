"""Typed errors for clean runtime halts."""
from __future__ import annotations


class WorkflowError(RuntimeError):
    pass


class BudgetExceededError(WorkflowError):
    def __init__(self, kind: str, limit: float, actual: float):
        super().__init__(f"{kind} budget exceeded: {actual:.6f} > {limit:.6f}")
        self.kind = kind
        self.limit = limit
        self.actual = actual


class MaxIterationsError(WorkflowError):
    def __init__(self, loop_id: str, max_iterations: int):
        super().__init__(
            f"Loop {loop_id!r} hit max_iterations={max_iterations}"
        )
        self.loop_id = loop_id
        self.max_iterations = max_iterations


class BuilderValidationError(WorkflowError):
    pass
