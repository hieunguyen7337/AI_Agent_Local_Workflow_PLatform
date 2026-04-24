import { useQuery } from "@tanstack/react-query";
import { fetchRun } from "../api/client";

export default function RunDetail({ runId }: { runId: string }) {
  const q = useQuery({ queryKey: ["run", runId], queryFn: () => fetchRun(runId) });
  if (q.isLoading) return <div className="p-4 text-sm text-gray-500">Loading run…</div>;
  if (q.error) return <div className="p-4 text-sm text-red-700">Error loading run</div>;
  const run = q.data;
  if (!run) return null;
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
                {s.cost_usd != null ? ` · $${s.cost_usd.toFixed(4)}` : ""}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
