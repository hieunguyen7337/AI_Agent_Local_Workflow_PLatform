import type {
  ApplyProposalRequest,
  ApplyProposalResponse,
  ApprovalSummary,
  MutationProposalRequest,
  MutationProposalResponse,
  NodeMetricsResponse,
  ProposalEvaluationRequest,
  ProposalEvaluationResponse,
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

export async function evaluateSpecProposal(
  workflow: string,
  payload: ProposalEvaluationRequest
): Promise<ProposalEvaluationResponse> {
  const r = await fetch(`${BASE}/api/spec/${workflow}/evaluate-proposal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...payload,
      n_per_fixture: payload.n_per_fixture ?? 1,
      max_cost_usd: payload.max_cost_usd ?? 2.0,
    }),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || `proposal evaluation ${r.status}`);
  }
  return r.json();
}

export async function applySpecProposal(
  workflow: string,
  payload: ApplyProposalRequest
): Promise<ApplyProposalResponse> {
  const r = await fetch(`${BASE}/api/spec/${workflow}/apply-proposal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || `proposal apply ${r.status}`);
  }
  return r.json();
}

export async function fetchRuns(): Promise<RunSummary[]> {
  const r = await fetch(`${BASE}/api/runs`);
  if (!r.ok) throw new Error(`runs fetch ${r.status}`);
  return r.json();
}

export async function fetchApprovals(): Promise<ApprovalSummary[]> {
  const r = await fetch(`${BASE}/api/approvals`);
  if (!r.ok) throw new Error(`approvals fetch ${r.status}`);
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
