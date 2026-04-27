import type {
  ApplyProposalRequest,
  ApplyProposalResponse,
  ApprovalDecisionRequest,
  ApprovalDecisionResponse,
  ApprovalSummary,
  CopyTemplateRequest,
  CopyTemplateResponse,
  MutationProposalRequest,
  MutationProposalResponse,
  NodeMetricsResponse,
  OptimizeProposalsRequest,
  OptimizeProposalsResponse,
  ProposalEvaluationRequest,
  ProposalEvaluationResponse,
  RestoreRollbackRequest,
  RestoreRollbackResponse,
  RollbackPreviewResponse,
  RollbackSnapshotsResponse,
  StartRunRequest,
  StartRunResponse,
  RunDetailResponse,
  RunSummary,
  Topology,
  WorkflowSpecResponse,
  WorkflowSummary,
} from "../types";

const BASE = "";

export async function fetchWorkflows(): Promise<WorkflowSummary[]> {
  const r = await fetch(`${BASE}/api/workflows`);
  if (!r.ok) throw new Error(`workflows fetch ${r.status}`);
  return r.json();
}

export async function copyWorkflowTemplate(
  workflow: string,
  payload: CopyTemplateRequest
): Promise<CopyTemplateResponse> {
  const r = await fetch(`${BASE}/api/workflows/${workflow}/copy-template`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || `template copy ${r.status}`);
  }
  return r.json();
}

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

export async function optimizeSpecProposals(
  workflow: string,
  payload: OptimizeProposalsRequest
): Promise<OptimizeProposalsResponse> {
  const r = await fetch(`${BASE}/api/spec/${workflow}/optimize-proposals`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...payload,
      candidate_count: payload.candidate_count ?? 3,
      n_per_fixture: payload.n_per_fixture ?? 1,
      max_cost_usd: payload.max_cost_usd ?? 5.0,
    }),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || `proposal optimization ${r.status}`);
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

export async function fetchRollbackSnapshots(workflow: string): Promise<RollbackSnapshotsResponse> {
  const r = await fetch(`${BASE}/api/spec/${workflow}/rollback-snapshots`);
  if (!r.ok) throw new Error(`rollback snapshots fetch ${r.status}`);
  return r.json();
}

export async function fetchRollbackPreview(
  workflow: string,
  snapshotId: string
): Promise<RollbackPreviewResponse> {
  const r = await fetch(`${BASE}/api/spec/${workflow}/rollback-snapshots/${snapshotId}/preview`);
  if (!r.ok) throw new Error(`rollback preview fetch ${r.status}`);
  return r.json();
}

export async function restoreRollbackSnapshot(
  workflow: string,
  snapshotId: string,
  payload: RestoreRollbackRequest = {}
): Promise<RestoreRollbackResponse> {
  const r = await fetch(`${BASE}/api/spec/${workflow}/rollback-snapshots/${snapshotId}/restore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || `rollback restore ${r.status}`);
  }
  return r.json();
}

export async function fetchRuns(): Promise<RunSummary[]> {
  const r = await fetch(`${BASE}/api/runs`);
  if (!r.ok) throw new Error(`runs fetch ${r.status}`);
  return r.json();
}

export async function startWorkflowRun(
  workflow: string,
  payload: StartRunRequest
): Promise<StartRunResponse> {
  const r = await fetch(`${BASE}/api/runs?workflow=${encodeURIComponent(workflow)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || `run start ${r.status}`);
  }
  return r.json();
}

export async function fetchApprovals(status: "pending" | "decided" | "all" = "pending"): Promise<ApprovalSummary[]> {
  const r = await fetch(`${BASE}/api/approvals?status=${status}`);
  if (!r.ok) throw new Error(`approvals fetch ${r.status}`);
  return r.json();
}

export async function decideApproval(
  runId: string,
  payload: ApprovalDecisionRequest
): Promise<ApprovalDecisionResponse> {
  const r = await fetch(`${BASE}/api/approvals/${runId}/decision`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const text = await r.text();
    throw new Error(text || `approval decision ${r.status}`);
  }
  return r.json();
}

export async function fetchRun(runId: string): Promise<RunDetailResponse> {
  const r = await fetch(`${BASE}/api/runs/${runId}`);
  if (!r.ok) throw new Error(`run fetch ${r.status}`);
  return r.json();
}

export async function fetchNodeMetrics(workflow: string, limit = 50): Promise<NodeMetricsResponse> {
  const r = await fetch(`${BASE}/api/graph/${workflow}/node-metrics?limit=${limit}`);
  if (!r.ok) throw new Error(`node metrics fetch ${r.status}`);
  return r.json();
}
