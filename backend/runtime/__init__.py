from typing import Any

__all__ = [
    "WorkflowBatchItem",
    "WorkflowBatchResult",
    "WorkflowFunctionResult",
    "run_workflow_batch",
    "run_workflow_function",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from backend.runtime.functions import (
            WorkflowBatchItem,
            WorkflowBatchResult,
            WorkflowFunctionResult,
            run_workflow_batch,
            run_workflow_function,
        )

        return {
            "WorkflowBatchItem": WorkflowBatchItem,
            "WorkflowBatchResult": WorkflowBatchResult,
            "WorkflowFunctionResult": WorkflowFunctionResult,
            "run_workflow_batch": run_workflow_batch,
            "run_workflow_function": run_workflow_function,
        }[name]
    raise AttributeError(name)
