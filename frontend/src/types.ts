export type NodeKind = "llm" | "tester" | "gate" | "retriever" | "router" | "approval" | "subgraph";

export interface GraphNode {
  id: string;
  kind: NodeKind;
  name: string;
  description: string;
  metadata: Record<string, unknown>;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: "normal" | "conditional";
  label?: string;
}

export interface GraphLoop {
  loop_id: string;
  from: string;
  to: string;
  max_iterations: number;
}

export interface Topology {
  name: string;
  entry: string;
  cost_budget_usd: number;
  latency_budget_ms: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  loops: GraphLoop[];
}

export interface WorkflowSpecResponse {
  workflow: string;
  spec: {
    schema_version: string;
    name: string;
    description: string;
    budget: {
      cost_usd: number;
      latency_ms: number;
    };
    entry: string;
    nodes: Array<Record<string, unknown> & { id: string; kind: NodeKind }>;
    edges: Array<{ from?: string; source?: string; to?: string; target?: string }>;
    loops: Array<{ from?: string; source?: string; to?: string; target?: string; max_iterations: number }>;
  };
  yaml: string;
  source_path: string;
}

export interface MutationProposalRequest {
  goal: string;
  constraints?: string;
  max_proposals?: number;
}

export interface MutationProposalResponse {
  workflow: string;
  status: "valid" | "invalid";
  summary: string;
  original_yaml: string;
  proposed_yaml: string;
  diff: string;
  validation_errors: string[];
}

export interface ProposalEvaluationRequest {
  proposed_yaml: string;
  n_per_fixture?: number;
  max_cost_usd?: number;
}

export interface ProposalEvaluationResponse {
  workflow: string;
  status: "ok" | "invalid" | "cancelled" | "stopped_cost_cap";
  validation_errors: string[];
  eval: null | {
    completed_run_count: number;
    completed_fixture_count: number;
    overall: {
      total_runs: number;
      passes: number;
      pass_rate: number;
      mean_cost_usd: number;
      mean_latency_ms: number;
      p95_latency_ms: number;
      cost_stdev_usd: number;
      latency_stdev_ms: number;
    };
    overall_ci: Record<string, unknown>;
    baseline_comparison: Record<string, unknown>;
  };
  run_artifact: string | null;
}

export interface OptimizeProposalsRequest {
  goal: string;
  constraints?: string;
  candidate_count?: number;
  n_per_fixture?: number;
  max_cost_usd?: number;
}

export interface OptimizationCandidate {
  candidate_id: string;
  status: "valid" | "invalid";
  summary: string;
  proposed_yaml: string;
  diff: string;
  validation_errors: string[];
  eval: null | {
    status: string;
    completed_run_count: number;
    completed_fixture_count: number;
    overall: {
      total_runs?: number;
      passes?: number;
      pass_rate?: number;
      mean_cost_usd?: number;
      mean_latency_ms?: number;
      p95_latency_ms?: number;
    };
    overall_ci: Record<string, unknown>;
    baseline_comparison: Record<string, unknown>;
  };
  run_artifact: string | null;
  rank: number | null;
  recommended: boolean;
}

export interface OptimizeProposalsResponse {
  workflow: string;
  status: "ok" | "stopped_cost_cap" | "no_recommendation";
  recommended_candidate_id: string | null;
  report_artifact: string;
  original_yaml: string;
  candidates: OptimizationCandidate[];
}

export interface ApplyProposalRequest {
  proposed_yaml: string;
  proposal_summary?: string;
  evaluation_artifact?: string | null;
  accepted_by?: string;
}

export interface ApplyProposalResponse {
  workflow: string;
  status: "applied";
  source_path: string;
  audit_path: string;
  rollback_path: string;
  diff: string;
  spec: WorkflowSpecResponse["spec"];
  yaml: string;
}

export interface RollbackSnapshot {
  snapshot_id: string;
  audit_path: string;
  rollback_path: string;
  created_at: string;
  accepted_by: string;
  proposal_summary: string;
  original_sha256: string;
  proposed_sha256: string;
}

export interface RollbackSnapshotsResponse {
  workflow: string;
  snapshots: RollbackSnapshot[];
}

export interface RollbackPreviewResponse {
  workflow: string;
  snapshot_id: string;
  current_yaml: string;
  rollback_yaml: string;
  diff: string;
}

export interface RestoreRollbackRequest {
  accepted_by?: string;
}

export interface RestoreRollbackResponse {
  workflow: string;
  status: "restored";
  source_path: string;
  snapshot_id: string;
  audit_path: string;
  rollback_path: string;
  diff: string;
  spec: WorkflowSpecResponse["spec"];
  yaml: string;
}

export interface NodeMetric {
  node_id: string;
  runs_considered: number;
  invocations: number;
  failed_invocations: number;
  fail_pct: number;
  p95_latency_ms: number;
  cost_per_run_usd: number;
  avg_retries_per_run: number;
  max_retries_in_run: number;
}

export interface NodeMetricsResponse {
  workflow: string;
  limit: number;
  runs_considered: number;
  metrics: Record<string, NodeMetric>;
}

export interface RunSummary {
  run_id: string;
  graph_name: string;
  started_ns: number;
  ended_ns: number | null;
  status: string;
  cost_usd: number;
  latency_ms: number;
  error: string | null;
  run_dir?: string;
  telemetry_db?: string;
  checkpoints_db?: string;
  spans_jsonl?: string;
  manifest?: string;
}

export interface StartRunRequest {
  input: string;
  expected?: string;
}

export interface StartRunResponse {
  run_id: string;
  graph_name: string;
  status: string;
  cost_usd: number;
  latency_ms: number;
  error: string | null;
  run_dir: string;
  telemetry_db?: string;
  checkpoints_db?: string;
  spans_jsonl?: string;
  manifest?: string;
  final_state: Record<string, unknown>;
}

export interface ApprovalSummary {
  workflow: string;
  run_id: string;
  node_id: string;
  prompt: string;
  approval_state_key: string;
  approved_target: string;
  rejected_target: string;
  created_ns: number;
  status: "pending" | "decided";
  artifact_path?: string;
  state_snapshot?: Record<string, unknown>;
  approval_decision?: {
    decision: "approved" | "rejected";
    reviewer: string;
    comment: string;
    created_ns: number;
    continuation_run_id: string;
    continuation_status: string;
    continuation_error?: string | null;
    artifact_path?: string;
  };
}

export interface ApprovalDecisionRequest {
  decision: "approved" | "rejected";
  reviewer?: string;
  comment?: string;
}

export interface ApprovalDecisionResponse {
  source_run_id: string;
  status: "resumed";
  decision: "approved" | "rejected";
  decision_artifact: string;
  continuation_run_id: string;
  continuation_status: string;
  continuation_run_dir: string;
  continuation_error?: string | null;
}

export interface SubgraphLineage {
  parent_run_id: string;
  parent_workflow?: string;
  node_id: string;
  child_workflow: string;
  child_run_id: string;
  status: string;
  error?: string | null;
  cost_usd?: number;
  latency_ms?: number;
  inputs?: Record<string, string>;
  outputs?: Record<string, string>;
  created_ns?: number;
  artifact_path?: string;
}

export interface RunDetailResponse extends RunSummary {
  spans: Array<Record<string, any>>;
  approval?: ApprovalSummary | null;
  approval_decision?: ApprovalSummary["approval_decision"] | null;
  approval_resume?: {
    source_run_id: string;
    approval_node_id: string;
    decision: "approved" | "rejected";
    artifact_path?: string;
  } | null;
  subgraphs?: SubgraphLineage[];
  parent_run?: SubgraphLineage | null;
}
