import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { decideApproval, fetchRun } from "../api/client";
import type { ApprovalDecisionResponse, SubgraphLineage, PendingSubgraphApproval, SubgraphDecision, SubgraphResume } from "../types";

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
  if (q.isLoading) return <div className="p-4 text-sm text-slate-400">Loading run...</div>;
  if (q.error) return <div className="p-4 text-sm text-red-400">Error loading run</div>;
  const run = q.data;
  if (!run) return null;

  const canDecide = run.status === "pending_approval" && run.approval && !run.approval_decision;
  const approvalDecision = run.approval_decision;
  const approvalResume = run.approval_resume;
  const displayStatus = run.display_status ?? run.status;
  const approvalPanelTitle = approvalDecision ? "Approval checkpoint" : "Pending approval";

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
    <div className="h-full overflow-y-auto p-4 text-sm space-y-3">
      <div>
        <div className="text-xs uppercase text-slate-400">Run</div>
        <div className="font-mono text-xs">{run.run_id}</div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <div>
          <div className="text-xs text-slate-400">Status</div>
          <div>{displayStatus}</div>
          {displayStatus !== run.status && <div className="text-[11px] text-slate-500">raw: {run.status}</div>}
        </div>
        <div><div className="text-xs text-slate-400">Cost</div><div>${run.cost_usd?.toFixed(4) ?? "-"}</div></div>
        <div><div className="text-xs text-slate-400">Latency</div><div>{run.latency_ms?.toFixed(0) ?? "-"} ms</div></div>
      </div>
      {approvalDecision && run.continuation_run_id && (
        <div className="rounded border border-emerald-700 bg-emerald-900/30 p-3 text-xs text-emerald-300">
          Approval {approvalDecision.decision}; continued as{" "}
          <button
            type="button"
            className="font-mono hover:underline"
            onClick={() => onSelectRun?.(run.continuation_run_id!)}
          >
            {run.continuation_run_id}
          </button>
          .
        </div>
      )}
      {run.error && (
        <div className="p-2 rounded bg-red-900/30 text-red-400 text-xs whitespace-pre-wrap">{run.error}</div>
      )}
      <div className="rounded border border-slate-700 bg-slate-800 p-3 text-xs text-slate-200 space-y-1">
        <div className="flex items-center justify-between gap-3">
          <div className="font-medium">Artifacts</div>
          <div className="text-[11px] text-slate-500">Guide: docs/run_artifacts.md</div>
        </div>
        <ArtifactPath label="Run dir" value={run.run_dir} />
        <ArtifactPath label="Manifest" value={run.manifest} />
        <ArtifactPath label="Telemetry DB" value={run.telemetry_db} />
        <ArtifactPath label="Internal replay checkpoint" value={run.checkpoints_db} />
        <ArtifactPath label="Spans JSONL" value={run.spans_jsonl} />
        <ArtifactPath label="Audit JSON" value={run.audit} />
        <ArtifactPath label="Node events JSONL" value={run.node_events} />
      </div>
      {run.audit_events && run.audit_events.length > 0 && (
        <div>
          <div className="text-xs uppercase text-slate-400 mb-1">Node Audit</div>
          <div className="space-y-2">
            {run.audit_events.map((event: any, index: number) => (
              <details key={`${event.node_id ?? "node"}-${index}`} className="rounded border border-slate-700 bg-slate-800 p-2">
                <summary className="cursor-pointer text-xs">
                  <span className="font-mono">{event.node_id ?? event.name ?? "node"}</span>
                  <span className="ml-2 text-slate-500">{event.node_kind ?? ""}</span>
                  <span className="ml-2 text-slate-500">{event.status ?? ""}</span>
                  {event.latency_ms != null && <span className="ml-2 text-slate-500">{Number(event.latency_ms).toFixed(0)}ms</span>}
                </summary>
                <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-words rounded bg-slate-900 p-2 text-[11px] text-slate-300">
                  {JSON.stringify(event, null, 2)}
                </pre>
              </details>
            ))}
          </div>
        </div>
      )}
      {run.approval && (
        <div className="rounded border border-amber-700 bg-amber-900/30 p-3 text-xs text-amber-300 space-y-2">
          <div className="font-medium">{approvalPanelTitle}</div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <div className="uppercase text-amber-400">Node</div>
              <div className="font-mono">{run.approval.node_id}</div>
            </div>
            <div>
              <div className="uppercase text-amber-400">Created</div>
              <div>{run.approval.created_ns ? new Date(run.approval.created_ns / 1_000_000).toLocaleString() : "-"}</div>
            </div>
          </div>
          <div>
            <div className="uppercase text-amber-400">Prompt</div>
            <div className="whitespace-pre-wrap">{run.approval.prompt}</div>
          </div>
          <div className="font-mono text-[11px] text-amber-400">{run.approval.artifact_path}</div>
          {approvalDecision && (
            <div className="rounded border border-emerald-700 bg-emerald-900/30 p-2 text-emerald-300 space-y-1">
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
            <div className="space-y-2 rounded border border-amber-700 bg-slate-900 p-2">
              <div className="font-medium">Record decision and fork continuation</div>
              <label className="block space-y-1">
                <div className="uppercase text-amber-400">Reviewer</div>
                <input
                  className="w-full rounded border border-amber-700 bg-slate-800 text-slate-100 p-1.5 text-xs"
                  value={reviewer}
                  onChange={(event) => setReviewer(event.target.value)}
                />
              </label>
              <label className="block space-y-1">
                <div className="uppercase text-amber-400">Comment</div>
                <textarea
                  className="h-16 w-full resize-none rounded border border-amber-700 bg-slate-800 text-slate-100 p-1.5 text-xs"
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
                  className="rounded border border-emerald-700 bg-emerald-700 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-700 disabled:border-slate-700"
                  disabled={!confirmed || isSubmittingDecision}
                  onClick={() => submitDecision("approved")}
                >
                  {isSubmittingDecision ? "Submitting..." : "Approve"}
                </button>
                <button
                  type="button"
                  className="rounded border border-red-700 bg-red-700 px-3 py-1.5 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-700 disabled:border-slate-700"
                  disabled={!confirmed || isSubmittingDecision}
                  onClick={() => submitDecision("rejected")}
                >
                  {isSubmittingDecision ? "Submitting..." : "Reject"}
                </button>
              </div>
            </div>
          )}
          {decisionError && (
            <div className="whitespace-pre-wrap rounded border border-red-700 bg-red-900/30 p-2 text-xs text-red-400">
              {decisionError}
            </div>
          )}
          {decisionResult && (
            <div className="rounded border border-emerald-700 bg-emerald-900/30 p-2 text-xs text-emerald-300">
              Forked continuation <span className="font-mono">{decisionResult.continuation_run_id}</span> with status{" "}
              {decisionResult.continuation_status}.
            </div>
          )}
        </div>
      )}
      {approvalResume && (
        <div className="rounded border border-blue-700 bg-blue-900/30 p-3 text-xs text-blue-300 space-y-2">
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
        <div className="rounded border border-violet-700 bg-violet-900/20 p-3 text-xs text-violet-300 space-y-2">
          <div className="font-medium">Subgraph child runs</div>
          {run.subgraphs.map((item: SubgraphLineage) => (
            <div key={`${item.node_id}-${item.child_run_id}`} className="rounded border border-violet-700 bg-slate-800 p-2 space-y-1">
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
        <div className="rounded border border-indigo-700 bg-indigo-900/30 p-3 text-xs text-indigo-300 space-y-2">
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
      {run.pending_subgraph_approval && !run.subgraph_decision && (
        <PendingChildApprovalPanel
          psa={run.pending_subgraph_approval as PendingSubgraphApproval}
          onSelectRun={onSelectRun}
        />
      )}
      {run.subgraph_decision && (
        <SubgraphDecisionPanel
          sd={run.subgraph_decision as SubgraphDecision}
          onSelectRun={onSelectRun}
        />
      )}
      {run.subgraph_resume && (
        <SubgraphResumePanel
          sr={run.subgraph_resume as SubgraphResume}
          onSelectRun={onSelectRun}
        />
      )}
      <div>
        <div className="text-xs uppercase text-slate-400 mb-1">Spans</div>
        <div className="space-y-1">
          {run.spans?.map((s: any) => (
            <div key={s.span_id} className="flex text-xs justify-between border-b border-slate-700 py-1">
              <div>
                <span className="font-mono">{s.name}</span>
                {s.iteration ? <span className="ml-2 text-slate-500">#{s.iteration}</span> : null}
              </div>
              <div className="text-slate-400">
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

function PendingChildApprovalPanel({
  psa,
  onSelectRun,
}: {
  psa: PendingSubgraphApproval;
  onSelectRun?: (runId: string) => void;
}) {
  return (
    <div className="rounded border border-amber-700 bg-amber-900/30 p-3 text-xs text-amber-300 space-y-2">
      <div className="font-medium">Pending child approval</div>
      <div>
        Subgraph node <span className="font-mono">{psa.node_id}</span> launched{" "}
        <span className="font-mono">{psa.child_workflow}</span> which is awaiting approval.
      </div>
      <div>
        Child run:{" "}
        <button
          type="button"
          className="font-mono hover:underline"
          onClick={() => onSelectRun?.(psa.child_run_id)}
        >
          {psa.child_run_id}
        </button>
      </div>
      <div className="font-mono text-[11px]">{psa.artifact_path}</div>
    </div>
  );
}

function SubgraphDecisionPanel({
  sd,
  onSelectRun,
}: {
  sd: SubgraphDecision;
  onSelectRun?: (runId: string) => void;
}) {
  return (
    <div className="rounded border border-emerald-700 bg-emerald-900/30 p-3 text-xs text-emerald-300 space-y-2">
      <div className="font-medium">Subgraph approval decided</div>
      <div>
        Node <span className="font-mono">{sd.subgraph_node_id}</span> — decision:{" "}
        <span className="font-semibold">{sd.decision}</span>
      </div>
      <div>
        Child continuation:{" "}
        <button
          type="button"
          className="font-mono hover:underline"
          onClick={() => onSelectRun?.(sd.child_continuation_run_id)}
        >
          {sd.child_continuation_run_id}
        </button>
      </div>
      {sd.parent_continuation_run_id && (
        <div>
          Parent continuation:{" "}
          <button
            type="button"
            className="font-mono hover:underline"
            onClick={() => onSelectRun?.(sd.parent_continuation_run_id!)}
          >
            {sd.parent_continuation_run_id}
          </button>
          {sd.parent_continuation_status && (
            <span className="ml-1 text-emerald-400">({sd.parent_continuation_status})</span>
          )}
        </div>
      )}
      <div className="font-mono text-[11px]">{sd.artifact_path}</div>
    </div>
  );
}

function SubgraphResumePanel({
  sr,
  onSelectRun,
}: {
  sr: SubgraphResume;
  onSelectRun?: (runId: string) => void;
}) {
  return (
    <div className="rounded border border-blue-700 bg-blue-900/30 p-3 text-xs text-blue-300 space-y-2">
      <div className="font-medium">Subgraph continuation</div>
      <div>
        Source parent run:{" "}
        <button
          type="button"
          className="font-mono hover:underline"
          onClick={() => onSelectRun?.(sr.source_parent_run_id)}
        >
          {sr.source_parent_run_id}
        </button>
      </div>
      <div>
        Node <span className="font-mono">{sr.subgraph_node_id}</span> — child decision:{" "}
        <span className="font-semibold">{sr.decision}</span>
      </div>
      <div>
        Child continuation:{" "}
        <button
          type="button"
          className="font-mono hover:underline"
          onClick={() => onSelectRun?.(sr.child_continuation_run_id)}
        >
          {sr.child_continuation_run_id}
        </button>
      </div>
      <div className="font-mono text-[11px]">{sr.artifact_path}</div>
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
