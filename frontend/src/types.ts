export type NodeKind = "llm" | "tester" | "gate" | "retriever" | "router" | "approval";

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
  artifact_path?: string;
  state_snapshot?: Record<string, unknown>;
}
