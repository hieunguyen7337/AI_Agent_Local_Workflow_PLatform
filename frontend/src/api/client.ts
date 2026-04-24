import type {
  MutationProposalRequest,
  MutationProposalResponse,
  NodeMetricsResponse,
  RunSummary,
  Topology,
  WorkflowSpecResponse,
} from "../types";

const BASE = "";

export async function fetchTopology(workflow: string): Promise<Topology> {
  const r = await fetch(`${BASE}/api/graph/${workflow}`);
  if (!r.ok) throw new Error(`graph fetch ${r.status}`);
  return r.json();
}

export async function fetchWorkflowSpec(workflow: string): Promise<WorkflowSpecResponse> {
  const r = await fetch(`${BASE}/api/spec/${workflow}`);
  if (!r.ok) throw new Error(`spec fetch ${r.status}`);
  return r.json();
}

export async function proposeSpecMutation(
  workflow: string,
  payload: MutationProposalRequest
): Promise<MutationProposalResponse> {
  const r = await fetch(`${BASE}/api/spec/${workflow}/propose-mutation`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload, max_proposals: payload.max_proposals ?? 1 }),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || `mutation proposal ${r.status}`);
  }
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
