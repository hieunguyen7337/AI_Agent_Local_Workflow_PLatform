"""Declarative workflow graph specs.

YAML workflow files are the editable source of truth. GraphSpec validates that
source and adapts it to the existing runtime GraphMetadata representation.
"""

from .loader import (
    builder_to_graph_spec,
    graph_spec_path,
    graph_spec_to_metadata,
    list_workflow_ids,
    load_graph_spec,
    load_graph_spec_source,
    load_workflow_metadata,
)
from backend.builder.nodes import (
    AgentContextNodeConfig,
    AgentModelNodeConfig,
    AgentResponseParserNodeConfig,
    AgentStartupNodeConfig,
    ApprovalNodeConfig,
    ContextCompactorNodeConfig,
    HookRunnerNodeConfig,
    MemoryWriterNodeConfig,
    PermissionGateNodeConfig,
    SubagentContextNodeConfig,
    SubagentJoinNodeConfig,
    SubagentOrchestratorNodeConfig,
    SubagentPlanNodeConfig,
    SubagentSpawnNodeConfig,
    SubagentSummarizeNodeConfig,
    SubgraphNodeConfig,
    ToolExecutorNodeConfig,
)

from .models import BudgetSpec, EdgeSpec, GraphSpec, LoopSpec, TemplateParameterSpec
from .apply import AppliedProposal, apply_graph_spec_proposal
from .mutation import MutationProposal, propose_mutation
from .optimization import OptimizationCandidate, OptimizationReport, optimize_proposals
from .templates import CopiedTemplate, copy_workflow_template
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
    "AgentContextNodeConfig",
    "AgentModelNodeConfig",
    "AgentResponseParserNodeConfig",
    "AgentStartupNodeConfig",
    "ApprovalNodeConfig",
    "BudgetSpec",
    "ContextCompactorNodeConfig",
    "CopiedTemplate",
    "EdgeSpec",
    "GraphSpec",
    "HookRunnerNodeConfig",
    "MemoryWriterNodeConfig",
    "LoopSpec",
    "PermissionGateNodeConfig",
    "SubagentContextNodeConfig",
    "SubagentJoinNodeConfig",
    "SubagentPlanNodeConfig",
    "SubagentSpawnNodeConfig",
    "SubagentSummarizeNodeConfig",
    "TemplateParameterSpec",
    "MutationProposal",
    "OptimizationCandidate",
    "OptimizationReport",
    "RestoredRollback",
    "RollbackPreview",
    "RollbackSnapshot",
    "SubagentOrchestratorNodeConfig",
    "SubgraphNodeConfig",
    "ToolExecutorNodeConfig",
    "builder_to_graph_spec",
    "graph_spec_path",
    "graph_spec_to_metadata",
    "list_workflow_ids",
    "load_graph_spec",
    "load_graph_spec_source",
    "load_workflow_metadata",
    "apply_graph_spec_proposal",
    "copy_workflow_template",
    "list_rollback_snapshots",
    "optimize_proposals",
    "preview_rollback_snapshot",
    "propose_mutation",
    "restore_rollback_snapshot",
]
