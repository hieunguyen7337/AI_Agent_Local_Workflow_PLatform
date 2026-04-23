import type { NodeMetricsResponse, RunSummary, Topology } from "../types";

const BASE = "";

export async function fetchTopology(workflow: string): Promise<Topology> {
  const r = await fetch(`${BASE}/api/graph/${workflow}`);
  if (!r.ok) throw new Error(`graph fetch ${r.status}`);
  return r.json();
}

export async function fetchRuns(): Promise<RunSummary[]> {
  const r = await fetch(`${BASE}/api/runs`);
  if (!r.ok) throw new Error(`runs fetch ${r.status}`);
  return r.json();
}

export async function fetchRun(runId: string): Promise<any> {
  const r = await fetch(`${BASE}/api/runs/${runId}`);
  if (!r.ok) throw new Error(`run fetch ${r.status}`);
  return r.json();
}

export async function fetchNodeMetrics(workflow: string, limit = 50): Promise<NodeMetricsResponse> {
  const r = await fetch(`${BASE}/api/graph/${workflow}/node-metrics?limit=${limit}`);
  if (!r.ok) throw new Error(`node metrics fetch ${r.status}`);
  return r.json();
}
