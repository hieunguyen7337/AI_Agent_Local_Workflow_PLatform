import { useQuery } from "@tanstack/react-query";
import { fetchRuns } from "../api/client";
import type { RunSummary } from "../types";

function fmtCost(usd: number | null | undefined) {
  if (usd == null) return "-";
  return `$${usd.toFixed(4)}`;
}

function fmtMs(ms: number | null | undefined) {
  if (ms == null) return "-";
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function statusColor(s: string) {
  if (s === "ok") return "text-green-400 bg-green-900/30";
  if (s === "running") return "text-blue-400 bg-blue-900/30";
  if (s === "pending_approval") return "text-amber-400 bg-amber-900/30";
  if (s === "approved_continued") return "text-emerald-400 bg-emerald-900/30";
  if (s === "rejected_continued") return "text-red-400 bg-red-900/30";
  if (s === "budget_exceeded") return "text-orange-400 bg-orange-900/30";
  return "text-red-400 bg-red-900/30";
}

function displayStatus(run: RunSummary) {
  return run.display_status ?? run.status;
}

export default function RunList({
  workflow,
  onSelect,
  selected,
}: {
  workflow: string;
  onSelect: (r: RunSummary) => void;
  selected?: string;
}) {
  const q = useQuery({ queryKey: ["runs"], queryFn: fetchRuns });
  if (q.isLoading) return <div className="p-4 text-sm text-slate-400">Loading runs...</div>;
  if (q.error) return <div className="p-4 text-sm text-red-400">Error loading runs</div>;

  const runs = (q.data ?? []).filter((r) => r.graph_name === workflow);
  if (runs.length === 0) {
    return (
      <div className="p-4 text-sm text-slate-400">
        No runs yet for <code>{workflow}</code>. Use the run form above to start one.
      </div>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <table className="w-full text-sm">
        <thead className="bg-slate-800 text-slate-400">
          <tr>
            <th className="text-left px-3 py-2">Run</th>
            <th className="text-left px-3 py-2">Status</th>
            <th className="text-right px-3 py-2">Cost</th>
            <th className="text-right px-3 py-2">Latency</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const status = displayStatus(r);
            return (
              <tr
                key={r.run_id}
                className={`cursor-pointer hover:bg-slate-800 ${selected === r.run_id ? "bg-blue-900/30" : ""}`}
                onClick={() => onSelect(r)}
              >
                <td className="px-3 py-2 font-mono text-xs">{r.run_id.slice(0, 26)}...</td>
                <td className="px-3 py-2">
                  <span className={`px-2 py-0.5 rounded text-xs ${statusColor(status)}`}>{status}</span>
                  {r.continuation_run_id && (
                    <div className="mt-1 font-mono text-[10px] text-slate-500">
                      continued {r.continuation_run_id.slice(0, 12)}...
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 text-right">{fmtCost(r.cost_usd)}</td>
                <td className="px-3 py-2 text-right">{fmtMs(r.latency_ms)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
