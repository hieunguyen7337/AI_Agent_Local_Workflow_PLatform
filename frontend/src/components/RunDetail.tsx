import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { decideApproval, fetchRun } from "../api/client";
import type { ApprovalDecisionResponse, SubgraphLineage } from "../types";

export default function RunDetail({
  runId,
  onSelectRun,
}: {
  runId: string;
  onSelectRun?: (runId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [reviewer, setReviewer] = useState("local-user");
  const [comment, setComment] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [decisionResult, setDecisionResult] = useState<ApprovalDecisionResponse | undefined>(undefined);
  const [decisionError, setDecisionError] = useState<string | undefined>(undefined);
  const [isSubmittingDecision, setIsSubmittingDecision] = useState(false);

  const q = useQuery({ queryKey: ["run", runId], queryFn: () => fetchRun(runId) });
  if (q.isLoading) return <div className="p-4 text-sm text-gray-500">Loading run...</div>;
  if (q.error) return <div className="p-4 text-sm text-red-700">Error loading run</div>;
  const run = q.data;
  if (!run) return null;

  const canDecide = run.status === "pending_approval" && run.approval && !run.approval_decision;
  const approvalDecision = run.approval_decision;
  const approvalResume = run.approval_resume;

  async function submitDecision(decision: "approved" | "rejected") {
    if (!confirmed || isSubmittingDecision) return;
    setIsSubmittingDecision(true);
    setDecisionError(undefined);
    setDecisionResult(undefined);
    try {
      const result = await decideApproval(runId, {
        decision,
        reviewer,
        comment,
      });
      setDecisionResult(result);
      setConfirmed(false);
      await Promise.all([
        q.refetch(),
        queryClient.invalidateQueries({ queryKey: ["runs"] }),
        queryClient.invalidateQueries({ queryKey: ["approvals"] }),
      ]);
    } catch (error) {
      setDecisionError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsSubmittingDecision(false);
    }
  }

  return (
    <div className="p-4 text-sm space-y-3 overflow-y-auto">
      <div>
        <div className="text-xs uppercase text-gray-500">Run</div>
        <div className="font-mono text-xs">{run.run_id}</div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div><div className="text-xs text-gray-500">Status</div><div>{run.status}</div></div>
        <div><div className="text-xs text-gray-500">Cost</div><div>${run.cost_usd?.toFixed(4) ?? "-"}</div></div>
        <div><div className="text-xs text-gray-500">Latency</div><div>{run.latency_ms?.toFixed(0) ?? "-"} ms</div></div>
      </div>
      {run.error && (
        <div className="p-2 rounded bg-red-50 text-red-800 text-xs whitespace-pre-wrap">{run.error}</div>
      )}
      <div className="rounded border border-slate-200 bg-slate-50 p-3 text-xs text-slate-800 space-y-1">
        <div className="font-medium">Artifacts</div>
        <ArtifactPath label="Run dir" value={run.run_dir} />
        <ArtifactPath label="Manifest" value={run.manifest} />
        <ArtifactPath label="Telemetry DB" value={run.telemetry_db} />
        <ArtifactPath label="Checkpoints DB" value={run.checkpoints_db} />
        <ArtifactPath label="Spans JSONL" value={run.spans_jsonl} />
      </div>
      {run.approval && (
        <div className="rounded border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 space-y-2">
          <div className="font-medium">Pending approval</div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <div className="uppercase text-amber-700">Node</div>
              <div className="font-mono">{run.approval.node_id}</div>
            </div>
            <div>
              <div className="uppercase text-amber-700">Created</div>
              <div>{run.approval.created_ns ? new Date(run.approval.created_ns / 1_000_000).toLocaleString() : "-"}</div>
            </div>
          </div>
          <div>
            <div className="uppercase text-amber-700">Prompt</div>
            <div className="whitespace-pre-wrap">{run.approval.prompt}</div>
          </div>
          <div className="font-mono text-[11px] text-amber-800">{run.approval.artifact_path}</div>
          {approvalDecision && (
            <div className="rounded border border-emerald-200 bg-emerald-50 p-2 text-emerald-900 space-y-1">
              <div className="font-medium">Decision: {approvalDecision.decision}</div>
              <div>
                Continuation:{" "}
                <button
                  type="button"
                  className="font-mono hover:underline"
                  onClick={() => onSelectRun?.(approvalDecision.continuation_run_id)}
                >
                  {approvalDecision.continuation_run_id}
                </button>
              </div>
              <div>Status: {approvalDecision.continuation_status}</div>
              <div className="font-mono text-[11px]">{approvalDecision.artifact_path}</div>
            </div>
          )}
          {canDecide && (
            <div className="space-y-2 rounded border border-amber-200 bg-white p-2">
              <div className="font-medium">Record decision and fork continuation</div>
              <label className="block space-y-1">
                <div className="uppercase text-amber-700">Reviewer</div>
                <input
                  className="w-full rounded border border-amber-200 p-1.5 text-xs"
                  value={reviewer}
                  onChange={(event) => setReviewer(event.target.value)}
                />
              </label>
              <label className="block space-y-1">
                <div className="uppercase text-amber-700">Comment</div>
                <textarea
                  className="h-16 w-full resize-none rounded border border-amber-200 p-1.5 text-xs"
                  value={comment}
                  onChange={(event) => setComment(event.target.value)}
                />
              </label>
              <label className="flex items-start gap-2">
                <input
                  className="mt-0.5"
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                <span>I reviewed this approval request and want to resume the workflow.</span>
              </label>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="rounded border border-emerald-700 bg-emerald-700 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-400 disabled:border-slate-400"
                  disabled={!confirmed || isSubmittingDecision}
                  onClick={() => submitDecision("approved")}
                >
                  {isSubmittingDecision ? "Submitting..." : "Approve"}
                </button>
                <button
                  type="button"
                  className="rounded border border-red-700 bg-red-700 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-400 disabled:border-slate-400"
                  disabled={!confirmed || isSubmittingDecision}
                  onClick={() => submitDecision("rejected")}
                >
                  {isSubmittingDecision ? "Submitting..." : "Reject"}
                </button>
              </div>
            </div>
          )}
          {decisionError && (
            <div className="whitespace-pre-wrap rounded border border-red-200 bg-red-50 p-2 text-xs text-red-800">
              {decisionError}
            </div>
          )}
          {decisionResult && (
            <div className="rounded border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-900">
              Forked continuation <span className="font-mono">{decisionResult.continuation_run_id}</span> with status{" "}
              {decisionResult.continuation_status}.
            </div>
          )}
        </div>
      )}
      {approvalResume && (
        <div className="rounded border border-blue-200 bg-blue-50 p-3 text-xs text-blue-900 space-y-2">
          <div className="font-medium">Approval continuation</div>
          <div>
            Source run:{" "}
            <button
              type="button"
              className="font-mono hover:underline"
              onClick={() => onSelectRun?.(approvalResume.source_run_id)}
            >
              {approvalResume.source_run_id}
            </button>
          </div>
          <div>Decision: {approvalResume.decision}</div>
          <div>Approval node: <span className="font-mono">{approvalResume.approval_node_id}</span></div>
          <div className="font-mono text-[11px]">{approvalResume.artifact_path}</div>
        </div>
      )}
      {run.subgraphs && run.subgraphs.length > 0 && (
        <div className="rounded border border-violet-200 bg-violet-50 p-3 text-xs text-violet-950 space-y-2">
          <div className="font-medium">Subgraph child runs</div>
          {run.subgraphs.map((item: SubgraphLineage) => (
            <div key={`${item.node_id}-${item.child_run_id}`} className="rounded border border-violet-200 bg-white p-2 space-y-1">
              <div>
                Node <span className="font-mono">{item.node_id}</span> ran{" "}
                <span className="font-mono">{item.child_workflow}</span>
              </div>
              <div>
                Child run:{" "}
                <button
                  type="button"
                  className="font-mono hover:underline"
                  onClick={() => onSelectRun?.(item.child_run_id)}
                >
                  {item.child_run_id}
                </button>
              </div>
              <div>
                Status: {item.status}
                {item.cost_usd != null ? ` | $${item.cost_usd.toFixed(4)}` : ""}
                {item.latency_ms != null ? ` | ${item.latency_ms.toFixed(0)}ms` : ""}
              </div>
              <div className="font-mono text-[11px]">{item.artifact_path}</div>
            </div>
          ))}
        </div>
      )}
      {run.parent_run && (
        <div className="rounded border border-indigo-200 bg-indigo-50 p-3 text-xs text-indigo-950 space-y-2">
          <div className="font-medium">Subgraph parent run</div>
          <div>
            Parent run:{" "}
            <button
              type="button"
              className="font-mono hover:underline"
              onClick={() => onSelectRun?.(run.parent_run!.parent_run_id)}
            >
              {run.parent_run.parent_run_id}
            </button>
          </div>
          <div>
            Node <span className="font-mono">{run.parent_run.node_id}</span> in{" "}
            <span className="font-mono">{run.parent_run.parent_workflow ?? "parent workflow"}</span>
          </div>
          <div className="font-mono text-[11px]">{run.parent_run.artifact_path}</div>
        </div>
      )}
      <div>
        <div className="text-xs uppercase text-gray-500 mb-1">Spans</div>
        <div className="space-y-1">
          {run.spans?.map((s: any) => (
            <div key={s.span_id} className="flex text-xs justify-between border-b py-1">
              <div>
                <span className="font-mono">{s.name}</span>
                {s.iteration ? <span className="ml-2 text-gray-400">#{s.iteration}</span> : null}
              </div>
              <div className="text-gray-500">
                {s.duration_ms?.toFixed(0)}ms
                {s.cost_usd != null ? ` - $${s.cost_usd.toFixed(4)}` : ""}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ArtifactPath({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  return (
    <div>
      <div className="uppercase text-slate-500">{label}</div>
      <div className="break-all font-mono text-[11px]">{value}</div>
    </div>
  );
}
