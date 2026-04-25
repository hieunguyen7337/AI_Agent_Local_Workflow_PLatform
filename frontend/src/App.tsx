import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import GraphView from "./components/GraphView";
import RunList from "./components/RunList";
import RunDetail from "./components/RunDetail";
import ApprovalWorkbench from "./components/ApprovalWorkbench";
import SpecInspector from "./components/SpecInspector";
import { fetchNodeMetrics, fetchTopology, fetchWorkflowSpec } from "./api/client";
import { useLiveUpdates } from "./live/useLiveUpdates";

const WORKFLOWS = [
  "coder_tester",
  "linear_rag",
  "supervisor_loop",
  "dispatch_aggregate",
  "approval_review",
  "rag_subgraph_wrapper",
] as const;
type InspectorTab = "node" | "source" | "validation" | "propose" | "rollback";

export default function App() {
  const [workflow, setWorkflow] = useState<(typeof WORKFLOWS)[number]>("coder_tester");
  const [selectedRun, setSelectedRun] = useState<string | undefined>(undefined);
  const [selectedNode, setSelectedNode] = useState<string | undefined>(undefined);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("node");
  useLiveUpdates(workflow, selectedRun);

  useEffect(() => {
    setSelectedRun(undefined);
    setSelectedNode(undefined);
    setInspectorTab("node");
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

  const spec = useQuery({
    queryKey: ["spec", workflow],
    queryFn: () => fetchWorkflowSpec(workflow),
    staleTime: Infinity,
    refetchInterval: false,
  });

  return (
    <div className="h-full grid" style={{ gridTemplateRows: "48px 1fr", gridTemplateColumns: "1fr 460px" }}>
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
        {topo.data && (
          <GraphView
            topology={topo.data}
            nodeMetrics={nodeMetrics.data?.metrics ?? {}}
            selectedNodeId={selectedNode}
            onSelectNode={(nodeId) => {
              setSelectedNode(nodeId);
              setInspectorTab("node");
            }}
          />
        )}
      </div>
      <aside className="flex flex-col h-full overflow-hidden">
        <div className="border-b h-[55%] min-h-[320px] overflow-hidden">
          {spec.error && <div className="p-4 text-sm text-red-700">Error loading source spec.</div>}
          <SpecInspector
            topology={topo.data}
            spec={spec.data}
            selectedNodeId={selectedNode}
            tab={inspectorTab}
            onTabChange={setInspectorTab}
            onApplied={async () => {
              await Promise.all([spec.refetch(), topo.refetch()]);
            }}
          />
        </div>
        <div className="border-b flex-none">
          <div className="px-3 py-2 text-xs uppercase text-gray-500">Recent runs</div>
          <RunList workflow={workflow} onSelect={(r) => setSelectedRun(r.run_id)} selected={selectedRun} />
        </div>
        <div className="border-b h-[220px] flex-none overflow-hidden">
          <div className="px-3 py-2 text-xs uppercase text-gray-500">Approvals</div>
          <ApprovalWorkbench onSelectRun={setSelectedRun} selectedRun={selectedRun} />
        </div>
        <div className="flex-1 overflow-hidden">
          {selectedRun ? (
            <RunDetail runId={selectedRun} onSelectRun={setSelectedRun} />
          ) : (
            <div className="p-4 text-sm text-gray-500">Select a run to see details.</div>
          )}
        </div>
      </aside>
    </div>
  );
}
