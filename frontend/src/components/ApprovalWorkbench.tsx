import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { decideApproval, fetchApprovals } from "../api/client";
import type { ApprovalSummary } from "../types";

type ApprovalTab = "pending" | "decided";

function fmtTime(ns: number | undefined) {
  if (!ns) return "-";
  return new Date(ns / 1_000_000).toLocaleString();
}

function preview(text: string, max = 90) {
  return text.length > max ? `${text.slice(0, max - 3)}...` : text;
}

export default function ApprovalWorkbench({
  onSelectRun,
  selectedRun,
}: {
  onSelectRun: (runId: string) => void;
  selectedRun?: string;
}) {
  const [tab, setTab] = useState<ApprovalTab>("pending");
  const q = useQuery({
    queryKey: ["approvals", tab],
    queryFn: () => fetchApprovals(tab),
  });

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <div className="border-b border-slate-600 flex text-xs">
        {(["pending", "decided"] as ApprovalTab[]).map((item) => (
          <button
            key={item}
            type="button"
            className={[
              "px-3 py-2 border-r border-slate-600 capitalize",
              tab === item ? "bg-amber-900/30 text-amber-300 font-medium" : "bg-slate-900 text-slate-400",
            ].join(" ")}
            onClick={() => setTab(item)}
          >
            {item}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto">
        {q.isLoading && <div className="p-3 text-sm text-slate-400">Loading approvals...</div>}
        {q.error && <div className="p-3 text-sm text-red-400">Error loading approvals</div>}
        {!q.isLoading && !q.error && (q.data ?? []).length === 0 && (
          <div className="p-3 text-sm text-slate-400">No {tab} approvals.</div>
        )}
        <div className="divide-y">
          {(q.data ?? []).map((approval) => (
            <ApprovalItem
              key={approval.run_id}
              approval={approval}
              selectedRun={selectedRun}
              onSelectRun={onSelectRun}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function ApprovalItem({
  approval,
  selectedRun,
  onSelectRun,
}: {
  approval: ApprovalSummary;
  selectedRun?: string;
  onSelectRun: (runId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const selected = selectedRun === approval.run_id;
  const decision = approval.approval_decision;
  const canDecide = approval.status === "pending" && !decision;
  const mutation = useMutation({
    mutationFn: (decisionValue: "approved" | "rejected") =>
      decideApproval(approval.run_id, {
        decision: decisionValue,
        reviewer: "local-user",
        comment: "Submitted from approval workbench.",
      }),
    onSuccess: async (result) => {
      setConfirmed(false);
      setError(undefined);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["approvals"] }),
        queryClient.invalidateQueries({ queryKey: ["runs"] }),
        queryClient.invalidateQueries({ queryKey: ["run", approval.run_id] }),
      ]);
      onSelectRun(result.continuation_run_id);
    },
    onError: (err) => {
      setError(err instanceof Error ? err.message : String(err));
    },
  });

  function submitDecision(decisionValue: "approved" | "rejected") {
    if (!confirmed || mutation.isPending) return;
    mutation.mutate(decisionValue);
  }

  return (
    <div className={["p-3 text-xs space-y-2", selected ? "bg-blue-900/20" : "bg-slate-900"].join(" ")}>
      <div className="flex items-start justify-between gap-2">
        <button
          type="button"
          className="text-left font-mono text-[11px] text-slate-200 hover:underline"
          onClick={() => onSelectRun(approval.run_id)}
        >
          {approval.run_id}
        </button>
        <span
          className={[
            "rounded px-2 py-0.5",
            approval.status === "pending" ? "bg-amber-900/30 text-amber-300" : "bg-emerald-900/30 text-emerald-400",
          ].join(" ")}
        >
          {approval.status}
        </span>
      </div>
      <div className="text-slate-300">
        <span className="font-medium">{approval.workflow}</span> /{" "}
        <span className="font-mono">{approval.node_id}</span>
      </div>
      <div className="text-slate-400">{preview(approval.prompt)}</div>
      <div className="text-slate-500">{fmtTime(approval.created_ns)}</div>
      {canDecide && (
        <div className="rounded border border-amber-700 bg-amber-900/30 p-2 space-y-2">
          <label className="flex items-start gap-2 text-amber-300">
            <input
              className="mt-0.5"
              type="checkbox"
              checked={confirmed}
              onChange={(event) => setConfirmed(event.target.checked)}
            />
            <span>I reviewed this approval request.</span>
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              className="rounded bg-emerald-700 px-3 py-1 text-xs font-medium text-white disabled:bg-slate-700"
              disabled={!confirmed || mutation.isPending}
              onClick={() => submitDecision("approved")}
            >
              {mutation.isPending ? "Submitting..." : "Approve"}
            </button>
            <button
              type="button"
              className="rounded bg-red-700 px-3 py-1 text-xs font-medium text-white disabled:bg-slate-700"
              disabled={!confirmed || mutation.isPending}
              onClick={() => submitDecision("rejected")}
            >
              {mutation.isPending ? "Submitting..." : "Reject"}
            </button>
          </div>
          {error && <div className="whitespace-pre-wrap text-red-400">{error}</div>}
        </div>
      )}
      {decision && (
        <div className="rounded border border-emerald-700 bg-emerald-900/30 p-2 text-emerald-300 space-y-1">
          <div>
            Decision: <span className="font-medium">{decision.decision}</span>
            {decision.reviewer ? ` by ${decision.reviewer}` : ""}
          </div>
          <div>Status: {decision.continuation_status}</div>
          <button
            type="button"
            className="font-mono text-[11px] text-emerald-400 hover:underline"
            onClick={() => onSelectRun(decision.continuation_run_id)}
          >
            {decision.continuation_run_id}
          </button>
        </div>
      )}
    </div>
  );
}
