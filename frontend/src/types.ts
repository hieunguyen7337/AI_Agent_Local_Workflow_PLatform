export type NodeKind = "llm" | "tester" | "gate" | "retriever" | "router";

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
