import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchDatasetEval, fetchDatasetEvals } from "../api/client";

export default function EvalAudit({ workflow }: { workflow: string }) {
  const [selectedEvalId, setSelectedEvalId] = useState<string | undefined>(undefined);
  const [query, setQuery] = useState("");
  const evals = useQuery({ queryKey: ["dataset-evals"], queryFn: fetchDatasetEvals });
  const workflowEvals = (evals.data ?? []).filter((item) => item.workflow === workflow);
  const activeEvalId = selectedEvalId ?? workflowEvals[0]?.eval_id;
  const detail = useQuery({
    queryKey: ["dataset-eval", workflow, activeEvalId],
    queryFn: () => fetchDatasetEval(workflow, activeEvalId!),
    enabled: Boolean(activeEvalId),
  });

  const filteredRows = useMemo(() => {
    const rows = detail.data?.rows ?? [];
    const needle = query.trim().toLowerCase();
    if (!needle) return rows.slice(0, 100);
    return rows
      .filter((row: any) => JSON.stringify(row).toLowerCase().includes(needle))
      .slice(0, 100);
  }, [detail.data?.rows, query]);

  return (
    <div className="bg-slate-900">
      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <div className="text-xs uppercase text-slate-400">Dataset eval audit</div>
        <select
          className="max-w-[260px] rounded border border-slate-600 bg-slate-800 text-slate-100 px-2 py-1 text-xs"
          value={activeEvalId ?? ""}
          onChange={(event) => setSelectedEvalId(event.target.value || undefined)}
        >
          {workflowEvals.length === 0 && <option value="">No evals</option>}
          {workflowEvals.map((item) => (
            <option key={item.eval_id} value={item.eval_id}>
              {item.eval_id}
            </option>
          ))}
        </select>
      </div>
      {detail.data && (
        <div className="space-y-2 px-3 pb-3 text-xs">
          <div className="grid grid-cols-4 gap-2 text-slate-300">
            <div>Rows: {detail.data.completed_run_count}/{detail.data.row_count}</div>
            <div>Pass: {Number(detail.data.overall?.pass_rate ?? 0).toFixed(2)}</div>
            <div>Mean latency: {Number(detail.data.overall?.mean_latency_ms ?? 0).toFixed(0)}ms</div>
            <div>Mean cost: ${Number(detail.data.overall?.mean_cost_usd ?? 0).toFixed(4)}</div>
          </div>
          <input
            className="w-full rounded border border-slate-600 bg-slate-800 text-slate-100 px-2 py-1 text-xs placeholder:text-slate-500"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter rows by query id, pass/fail, node id, or error"
          />
          <div className="min-h-[140px] max-h-[320px] overflow-auto rounded border border-slate-600">
            {filteredRows.map((row: any) => (
              <details key={`${row.row_index}-${row.run_id}`} className="border-b border-slate-700 p-2">
                <summary className="cursor-pointer">
                  <span className="font-mono">#{row.row_index}</span>
                  <span className="ml-2">{row.query_id}</span>
                  <span className={row.pass ? "ml-2 text-emerald-400" : "ml-2 text-red-400"}>
                    {row.pass ? "pass" : "fail"}
                  </span>
                </summary>
                <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-slate-800 p-2 text-[11px] text-slate-300">
                  {JSON.stringify(row, null, 2)}
                </pre>
              </details>
            ))}
          </div>
        </div>
      )}
      {detail.error && <div className="px-3 pb-3 text-xs text-red-400">Error loading eval audit.</div>}
    </div>
  );
}
