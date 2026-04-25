"""Declarative workflow graph specs.

YAML workflow files are the editable source of truth. GraphSpec validates that
source and adapts it to the existing runtime GraphMetadata representation.
"""

from .loader import (
    builder_to_graph_spec,
    graph_spec_path,
    graph_spec_to_metadata,
    load_graph_spec,
    load_graph_spec_source,
    load_workflow_metadata,
)
from backend.builder.nodes import ApprovalNodeConfig, SubgraphNodeConfig

from .models import BudgetSpec, EdgeSpec, GraphSpec, LoopSpec
from .apply import AppliedProposal, apply_graph_spec_proposal
from .mutation import MutationProposal, propose_mutation
from .optimization import OptimizationCandidate, OptimizationReport, optimize_proposals
from .rollback import (
    RestoredRollback,
    RollbackPreview,
    RollbackSnapshot,
    list_rollback_snapshots,
    preview_rollback_snapshot,
    restore_rollback_snapshot,
)

__all__ = [
    "AppliedProposal",
    "ApprovalNodeConfig",
    "BudgetSpec",
    "EdgeSpec",
    "GraphSpec",
    "LoopSpec",
    "MutationProposal",
    "OptimizationCandidate",
    "OptimizationReport",
    "RestoredRollback",
    "RollbackPreview",
    "RollbackSnapshot",
    "SubgraphNodeConfig",
    "builder_to_graph_spec",
    "graph_spec_path",
    "graph_spec_to_metadata",
    "load_graph_spec",
    "load_graph_spec_source",
    "load_workflow_metadata",
    "apply_graph_spec_proposal",
    "list_rollback_snapshots",
    "optimize_proposals",
    "preview_rollback_snapshot",
    "propose_mutation",
    "restore_rollback_snapshot",
]
