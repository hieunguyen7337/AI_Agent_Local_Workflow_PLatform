import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import GraphView from "./components/GraphView";
import RunList from "./components/RunList";
import RunDetail from "./components/RunDetail";
import { fetchNodeMetrics, fetchTopology } from "./api/client";
import { useLiveUpdates } from "./live/useLiveUpdates";

const WORKFLOWS = ["coder_tester", "linear_rag"] as const;

export default function App() {
  const [workflow, setWorkflow] = useState<(typeof WORKFLOWS)[number]>("coder_tester");
  const [selectedRun, setSelectedRun] = useState<string | undefined>(undefined);
  useLiveUpdates(workflow, selectedRun);

  useEffect(() => {
    setSelectedRun(undefined);
  }, [workflow]);

  const topo = useQuery({
    queryKey: ["topology", workflow],
    queryFn: () => fetchTopology(workflow),
    staleTime: Infinity,
    refetchInterval: false,
  });

  const nodeMetrics = useQuery({
    queryKey: ["node-metrics", workflow],
    queryFn: () => fetchNodeMetrics(workflow, 50),
    staleTime: Infinity,
    refetchInterval: false,
  });

  return (
    <div className="h-full grid" style={{ gridTemplateRows: "48px 1fr", gridTemplateColumns: "1fr 420px" }}>
      <header className="col-span-2 border-b flex items-center px-4 bg-white gap-3">
        <div className="font-semibold">Workflow Platform</div>
        <label className="text-sm text-gray-500">Workflow</label>
        <select
          className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          value={workflow}
          onChange={(e) => setWorkflow(e.target.value as (typeof WORKFLOWS)[number])}
        >
          {WORKFLOWS.map((w) => (
            <option key={w} value={w}>
              {w}
            </option>
          ))}
        </select>
      </header>
      <div className="border-r">
        {topo.isLoading && <div className="p-4 text-sm text-gray-500">Loading topology...</div>}
        {topo.error && <div className="p-4 text-sm text-red-700">Error loading topology. Is the backend running on :8000?</div>}
        {topo.data && <GraphView topology={topo.data} nodeMetrics={nodeMetrics.data?.metrics ?? {}} />}
      </div>
      <aside className="flex flex-col h-full overflow-hidden">
        <div className="border-b">
          <div className="px-3 py-2 text-xs uppercase text-gray-500">Recent runs</div>
          <RunList workflow={workflow} onSelect={(r) => setSelectedRun(r.run_id)} selected={selectedRun} />
        </div>
        <div className="flex-1 overflow-hidden">
          {selectedRun ? (
            <RunDetail runId={selectedRun} />
          ) : (
            <div className="p-4 text-sm text-gray-500">Select a run to see details.</div>
          )}
        </div>
      </aside>
    </div>
  );
}
