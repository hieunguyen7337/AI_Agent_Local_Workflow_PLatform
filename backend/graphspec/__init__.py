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
from backend.builder.nodes import ApprovalNodeConfig

from .models import BudgetSpec, EdgeSpec, GraphSpec, LoopSpec
from .apply import AppliedProposal, apply_graph_spec_proposal
from .mutation import MutationProposal, propose_mutation

__all__ = [
    "AppliedProposal",
    "ApprovalNodeConfig",
    "BudgetSpec",
    "EdgeSpec",
    "GraphSpec",
    "LoopSpec",
    "MutationProposal",
    "builder_to_graph_spec",
    "graph_spec_path",
    "graph_spec_to_metadata",
    "load_graph_spec",
    "load_graph_spec_source",
    "load_workflow_metadata",
    "apply_graph_spec_proposal",
    "propose_mutation",
]
