import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import GraphView from "./components/GraphView";
import RunList from "./components/RunList";
import RunDetail from "./components/RunDetail";
import ApprovalWorkbench from "./components/ApprovalWorkbench";
import SpecInspector from "./components/SpecInspector";
import RunStarter from "./components/RunStarter";
import { fetchNodeMetrics, fetchTopology, fetchWorkflowSpec, fetchWorkflows } from "./api/client";
import { useLiveUpdates } from "./live/useLiveUpdates";
import type { CopyTemplateResponse, WorkflowSummary } from "./types";

type InspectorTab = "node" | "source" | "validation" | "propose" | "rollback";
type WorkbenchTab = "inspect" | "run" | "improve" | "recover";

const WORKBENCH_TABS: Array<{ id: WorkbenchTab; label: string }> = [
  { id: "inspect", label: "Inspect" },
  { id: "run", label: "Run" },
  { id: "improve", label: "Improve" },
  { id: "recover", label: "Recover" },
];

function categoryLabel(category: string) {
  return category
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.slice(0, 1).toUpperCase() + part.slice(1))
    .join(" ");
}

function workflowMatchesSearch(workflow: WorkflowSummary, search: string) {
  const query = search.trim().toLowerCase();
  if (query === "") return true;
  return [
    workflow.id,
    workflow.name,
    workflow.description,
    workflow.category,
    workflow.template ? "template" : "",
    workflow.template_parameter_count > 0 ? `${workflow.template_parameter_count} parameters expected inputs` : "",
    workflow.validation_status,
    workflow.source_path,
    workflow.eval_quality.fixture_status,
    `fixtures ${workflow.eval_quality.fixture_status}`,
    workflow.eval_quality.fixture_path,
    `${workflow.eval_quality.fixture_count} fixtures`,
    workflow.eval_quality.baseline_status,
    `baseline ${workflow.eval_quality.baseline_status}`,
    workflow.eval_quality.baseline_path,
    ...workflow.tags,
    ...workflow.validation_errors,
    ...workflow.eval_quality.fixture_errors,
    ...workflow.eval_quality.stale_reasons,
  ]
    .join(" ")
    .toLowerCase()
    .includes(query);
}

function workflowOptionLabel(workflow: WorkflowSummary) {
  if (workflow.validation_status === "invalid") {
    return `${workflow.name} (invalid)`;
  }
  if (workflow.eval_quality.fixture_status === "invalid") {
    return `${workflow.name} (fixtures invalid)`;
  }
  if (workflow.eval_quality.baseline_status === "stale") {
    return `${workflow.name} (baseline stale)`;
  }
  if (workflow.eval_quality.baseline_status === "missing") {
    return `${workflow.name} (baseline missing)`;
  }
  if (workflow.template) {
    return `${workflow.name} (template)`;
  }
  return workflow.name;
}

function workflowOptionTitle(workflow: WorkflowSummary) {
  return [
    workflow.description,
    workflow.source_path,
    workflow.template ? "template" : "",
    workflow.template_parameter_count > 0 ? `${workflow.template_parameter_count} parameters` : "",
    `fixtures: ${workflow.eval_quality.fixture_status}`,
    `fixture count: ${workflow.eval_quality.fixture_count}`,
    workflow.eval_quality.fixture_path,
    `baseline: ${workflow.eval_quality.baseline_status}`,
    workflow.eval_quality.baseline_path,
    workflow.tags.join(", "),
    ...workflow.validation_errors,
    ...workflow.eval_quality.fixture_errors,
    ...workflow.eval_quality.stale_reasons,
  ].filter(Boolean).join(" | ");
}

function workflowFactSummary(workflow: WorkflowSummary | undefined) {
  if (!workflow) return "";
  const parts = [
    workflow.template ? "template" : workflow.validation_status,
    `${workflow.facts.node_count} nodes`,
    `${workflow.facts.edge_count} edges`,
  ];
  if (workflow.facts.loop_count > 0) parts.push(`${workflow.facts.loop_count} loops`);
  if (workflow.facts.approval_node_count > 0) {
    parts.push(`${workflow.facts.approval_node_count} approval`);
  }
  if (workflow.facts.subgraph_node_count > 0) {
    parts.push(`${workflow.facts.subgraph_node_count} subgraph`);
  }
  if (workflow.template_parameter_count > 0) {
    parts.push(`${workflow.template_parameter_count} parameters`);
  }
  const fixtureLabel =
    workflow.eval_quality.fixture_status === "present"
      ? `fixtures: ${workflow.eval_quality.fixture_count}`
      : `fixtures: ${workflow.eval_quality.fixture_status}`;
  parts.push(fixtureLabel);
  parts.push(`baseline: ${workflow.eval_quality.baseline_status.replace("_", " ")}`);
  return parts.join(" · ");
}

export default function App() {
  const [workflow, setWorkflow] = useState<string>("");
  const [workflowSearch, setWorkflowSearch] = useState("");
  const [selectedRun, setSelectedRun] = useState<string | undefined>(undefined);
  const [selectedNode, setSelectedNode] = useState<string | undefined>(undefined);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("node");
  const [workbenchTab, setWorkbenchTab] = useState<WorkbenchTab>("inspect");
  const [templateCopyNotice, setTemplateCopyNotice] = useState<CopyTemplateResponse | undefined>(undefined);
  useLiveUpdates(workflow, selectedRun);

  const workflows = useQuery({
    queryKey: ["workflows"],
    queryFn: fetchWorkflows,
    staleTime: Infinity,
    refetchInterval: false,
  });

  useEffect(() => {
    if (workflow === "" && workflows.data && workflows.data.length > 0) {
      setWorkflow(workflows.data[0].id);
    }
  }, [workflow, workflows.data]);

  useEffect(() => {
    setSelectedRun(undefined);
    setSelectedNode(undefined);
    setInspectorTab("node");
    setWorkbenchTab("inspect");
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

  const groupedWorkflows = useMemo(() => {
    const groups = new Map<string, WorkflowSummary[]>();
    for (const item of workflows.data ?? []) {
      if (!workflowMatchesSearch(item, workflowSearch)) continue;
      const category = item.category || "general";
      groups.set(category, [...(groups.get(category) ?? []), item]);
    }
    return [...groups.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([category, items]) => ({
        category,
        items: items.sort((a, b) => a.name.localeCompare(b.name)),
      }));
  }, [workflowSearch, workflows.data]);

  const selectedWorkflowSummary = workflows.data?.find((item) => item.id === workflow);
  const visibleGroupedWorkflows = useMemo(() => {
    if (!selectedWorkflowSummary || workflowMatchesSearch(selectedWorkflowSummary, workflowSearch)) {
      return groupedWorkflows;
    }
    return [{ category: "current-selection", items: [selectedWorkflowSummary] }, ...groupedWorkflows];
  }, [groupedWorkflows, selectedWorkflowSummary, workflowSearch]);

  const filteredWorkflowCount = groupedWorkflows.reduce((count, group) => count + group.items.length, 0);
  const workflowHealth = useMemo(() => {
    const items = workflows.data ?? [];
    const valid = items.filter((item) => item.validation_status === "valid").length;
    const fixtureReady = items.filter((item) => item.eval_quality.fixture_status === "present").length;
    const freshBaseline = items.filter((item) => item.eval_quality.baseline_status === "fresh").length;
    return {
      total: items.length,
      valid,
      invalid: items.length - valid,
      fixtureReady,
      freshBaseline,
    };
  }, [workflows.data]);

  return (
    <div
      className="h-full grid"
      style={{ gridTemplateRows: "48px 1fr", gridTemplateColumns: "minmax(0, 1fr) minmax(480px, 520px)" }}
    >
      <header className="col-span-2 border-b flex items-center px-4 bg-white gap-3">
        <div className="font-semibold">Workflow Platform</div>
        <label className="text-sm text-gray-500">Workflow</label>
        <input
          type="search"
          className="w-48 text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          placeholder="Search workflows"
          value={workflowSearch}
          onChange={(e) => setWorkflowSearch(e.target.value)}
        />
        <select
          className="min-w-64 text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          value={workflow}
          disabled={workflows.isLoading || !workflows.data}
          onChange={(e) => setWorkflow(e.target.value)}
        >
          {workflows.isLoading && <option value="">Loading...</option>}
          {!workflows.isLoading && filteredWorkflowCount === 0 && !selectedWorkflowSummary && <option value="">No workflows match</option>}
          {visibleGroupedWorkflows.map((group) => (
            <optgroup key={group.category} label={categoryLabel(group.category)}>
              {group.items.map((w) => (
                <option key={w.id} value={w.id} title={workflowOptionTitle(w)}>
                  {workflowOptionLabel(w)}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {workflows.data && (
          <div className="min-w-0 text-xs text-slate-500">
            <span className="font-medium text-slate-700">{workflowHealth.total}</span> workflows ·{" "}
            <span className="text-emerald-700">{workflowHealth.valid}</span> valid ·{" "}
            <span className={workflowHealth.invalid > 0 ? "text-red-700" : "text-slate-500"}>
              {workflowHealth.invalid}
            </span>{" "}
            invalid · <span className="text-sky-700">{workflowHealth.fixtureReady}</span> fixture-ready ·{" "}
            <span className="text-indigo-700">{workflowHealth.freshBaseline}</span> fresh baseline
            {selectedWorkflowSummary && (
              <span>
                {" · "}
                <span
                  className={
                    selectedWorkflowSummary.validation_status === "invalid" ||
                    selectedWorkflowSummary.eval_quality.fixture_status === "invalid" ||
                    selectedWorkflowSummary.eval_quality.baseline_status === "stale" ||
                    selectedWorkflowSummary.eval_quality.baseline_status === "invalid"
                      ? "text-red-700"
                      : ""
                  }
                >
                  {workflowFactSummary(selectedWorkflowSummary)}
                </span>
              </span>
            )}
          </div>
        )}
      </header>
      <div className="border-r min-w-0">
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
              setWorkbenchTab("inspect");
            }}
          />
        )}
      </div>
      <aside className="flex flex-col h-full min-w-0 overflow-hidden">
        <div className="border-b bg-white">
          <div className="px-3 py-2">
            <div className="text-xs uppercase text-slate-500">Workflow workbench</div>
            <div className="text-sm font-semibold text-slate-800">{workflow}</div>
            {templateCopyNotice?.workflow === workflow && (
              <div className="mt-2 space-y-1 rounded border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-900">
                <div className="font-medium">Copied template to {templateCopyNotice.workflow}</div>
                <div>Normal template: false workflow. Customize it through source review or the proposal flow.</div>
                <div className="font-mono text-[11px] break-all">source: {templateCopyNotice.source_path}</div>
                <div className="font-mono text-[11px] break-all">audit: {templateCopyNotice.audit_path}</div>
              </div>
            )}
          </div>
          <div className="flex border-t text-xs">
            {WORKBENCH_TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                className={[
                  "flex-1 border-r px-3 py-2 text-center",
                  workbenchTab === item.id ? "bg-slate-100 font-medium text-slate-900" : "bg-white text-slate-500",
                ].join(" ")}
                onClick={() => {
                  setWorkbenchTab(item.id);
                  if (item.id === "inspect" && !["node", "source", "validation"].includes(inspectorTab)) {
                    setInspectorTab("node");
                  }
                  if (item.id === "improve") setInspectorTab("propose");
                  if (item.id === "recover") setInspectorTab("rollback");
                }}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1 min-h-0 overflow-hidden">
          {workbenchTab === "inspect" && (
            <div className="h-full overflow-hidden">
              {spec.error && <div className="p-4 text-sm text-red-700">Error loading source spec.</div>}
              <SpecInspector
                topology={topo.data}
                spec={spec.data}
                workflowIds={(workflows.data ?? []).map((item) => item.id)}
                selectedNodeId={selectedNode}
                tab={["node", "source", "validation"].includes(inspectorTab) ? inspectorTab : "node"}
                onTabChange={setInspectorTab}
                availableTabs={["node", "source", "validation"]}
                title="Inspect source"
                onTemplateCopied={async (result) => {
                  await workflows.refetch();
                  setWorkflowSearch("");
                  setTemplateCopyNotice(result);
                  setWorkflow(result.workflow);
                }}
              />
            </div>
          )}
          {workbenchTab === "run" && (
            <div className="h-full min-h-0 flex flex-col overflow-hidden bg-white">
              <RunStarter
                workflow={workflow}
                onStarted={(runId) => {
                  setSelectedRun(runId);
                }}
              />
              <div className="border-b min-h-[170px] max-h-[250px] overflow-hidden flex flex-col">
                <div className="px-3 py-2 text-xs uppercase text-gray-500">Recent runs</div>
                <RunList workflow={workflow} onSelect={(r) => setSelectedRun(r.run_id)} selected={selectedRun} />
              </div>
              <div className="border-b h-[240px] flex-none overflow-hidden">
                <div className="px-3 py-2 text-xs uppercase text-gray-500">Approvals</div>
                <ApprovalWorkbench onSelectRun={setSelectedRun} selectedRun={selectedRun} />
              </div>
              <div className="flex-1 min-h-0 overflow-hidden">
                {selectedRun ? (
                  <RunDetail runId={selectedRun} onSelectRun={setSelectedRun} />
                ) : (
                  <div className="p-4 text-sm text-gray-500">Select or start a run to see details, artifacts, and lineage.</div>
                )}
              </div>
            </div>
          )}
          {workbenchTab === "improve" && (
            <div className="h-full overflow-hidden">
              <SpecInspector
                topology={topo.data}
                spec={spec.data}
                selectedNodeId={selectedNode}
                tab="propose"
                onTabChange={setInspectorTab}
                availableTabs={["propose"]}
                title="Improve workflow"
                onApplied={async () => {
                  await Promise.all([spec.refetch(), topo.refetch()]);
                }}
              />
            </div>
          )}
          {workbenchTab === "recover" && (
            <div className="h-full overflow-hidden">
              <SpecInspector
                topology={topo.data}
                spec={spec.data}
                selectedNodeId={selectedNode}
                tab="rollback"
                onTabChange={setInspectorTab}
                availableTabs={["rollback"]}
                title="Recover source"
                onApplied={async () => {
                  await Promise.all([spec.refetch(), topo.refetch()]);
                }}
              />
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
