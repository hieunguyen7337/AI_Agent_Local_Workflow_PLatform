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
  if (s === "ok") return "text-green-700 bg-green-100";
  if (s === "running") return "text-blue-700 bg-blue-100";
  if (s === "pending_approval") return "text-amber-700 bg-amber-100";
  if (s === "budget_exceeded") return "text-orange-700 bg-orange-100";
  return "text-red-700 bg-red-100";
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
  if (q.isLoading) return <div className="p-4 text-sm text-gray-500">Loading runs...</div>;
  if (q.error) return <div className="p-4 text-sm text-red-700">Error loading runs</div>;

  const runs = (q.data ?? []).filter((r) => r.graph_name === workflow);
  if (runs.length === 0) {
    return (
      <div className="p-4 text-sm text-gray-500">
        No runs yet for <code>{workflow}</code>. Use the run form above to start one.
      </div>
    );
  }

  return (
    <div className="overflow-y-auto">
      <table className="w-full text-sm">
        <thead className="bg-gray-50 text-gray-600">
          <tr>
            <th className="text-left px-3 py-2">Run</th>
            <th className="text-left px-3 py-2">Graph</th>
            <th className="text-left px-3 py-2">Status</th>
            <th className="text-right px-3 py-2">Cost</th>
            <th className="text-right px-3 py-2">Latency</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr
              key={r.run_id}
              className={`cursor-pointer hover:bg-gray-50 ${selected === r.run_id ? "bg-blue-50" : ""}`}
              onClick={() => onSelect(r)}
            >
              <td className="px-3 py-2 font-mono text-xs">{r.run_id.slice(0, 18)}...</td>
              <td className="px-3 py-2">{r.graph_name}</td>
              <td className="px-3 py-2">
                <span className={`px-2 py-0.5 rounded text-xs ${statusColor(r.status)}`}>{r.status}</span>
              </td>
              <td className="px-3 py-2 text-right">{fmtCost(r.cost_usd)}</td>
              <td className="px-3 py-2 text-right">{fmtMs(r.latency_ms)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
