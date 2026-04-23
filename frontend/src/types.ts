export type NodeKind = "llm" | "tester" | "gate" | "retriever";

export interface GraphNode {
  id: string;
  kind: NodeKind;
  name: string;
  description: string;
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
