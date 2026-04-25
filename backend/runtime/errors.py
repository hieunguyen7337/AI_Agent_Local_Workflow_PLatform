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


class ReplayError(WorkflowError):
    pass


class CancelledError(WorkflowError):
    pass


class PendingApprovalError(WorkflowError):
    def __init__(self, approval: dict, state_update: dict | None = None):
        super().__init__(f"pending approval at node {approval.get('node_id')!r}")
        self.approval = approval
        self.state_update = state_update or {}


class RoutingError(WorkflowError):
    pass


class SubgraphError(WorkflowError):
    def __init__(self, node_id: str, child_run_id: str, status: str, error: str | None = None):
        message = f"subgraph node {node_id!r} child run {child_run_id!r} finished with status {status!r}"
        if error:
            message = f"{message}: {error}"
        super().__init__(message)
        self.node_id = node_id
        self.child_run_id = child_run_id
        self.status = status
        self.error = error
