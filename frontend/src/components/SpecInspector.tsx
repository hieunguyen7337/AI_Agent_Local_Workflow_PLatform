import { useState } from "react";
import { proposeSpecMutation } from "../api/client";
import type { GraphNode, MutationProposalResponse, Topology, WorkflowSpecResponse } from "../types";

type InspectorTab = "node" | "source" | "validation" | "propose";

function formatValue(value: unknown): string {
  if (value == null || value === "") return "-";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}

function Field({ label, value, mono = false, pre = false }: { label: string; value: unknown; mono?: boolean; pre?: boolean }) {
  const text = formatValue(value);
  return (
    <div className="space-y-1">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div
        className={[
          "text-xs text-slate-800",
          mono ? "font-mono" : "",
          pre ? "whitespace-pre-wrap rounded border border-slate-200 bg-slate-50 p-2 leading-relaxed" : "break-words",
        ].join(" ")}
      >
        {text}
      </div>
    </div>
  );
}

function nodeFields(node: GraphNode): Array<{ label: string; value: unknown; mono?: boolean; pre?: boolean }> {
  const m = node.metadata ?? {};
  const base = [
    { label: "Node ID", value: node.id, mono: true },
    { label: "Kind", value: node.kind },
    { label: "Name", value: node.name },
  ];
  if (node.kind === "llm") {
    return [
      ...base,
      { label: "Provider", value: m.provider },
      { label: "Model", value: m.model, mono: true },
      { label: "Output State Key", value: m.output_state_key, mono: true },
      { label: "Temperature", value: m.temperature },
      { label: "Max Tokens", value: m.max_tokens },
      { label: "Max Retries", value: m.max_retries },
      { label: "System Prompt", value: m.system_prompt, pre: true },
      { label: "User Prompt Template", value: m.user_prompt_template, pre: true },
    ];
  }
  if (node.kind === "router") {
    return [
      ...base,
      { label: "Route State Key", value: m.route_state_key, mono: true },
      { label: "Routes", value: m.routes, mono: true, pre: true },
      { label: "Default Target", value: m.default_target, mono: true },
    ];
  }
  if (node.kind === "gate") {
    return [
      ...base,
      { label: "Verdict State Key", value: m.verdict_state_key, mono: true },
      { label: "Pass Target", value: m.pass_target, mono: true },
      { label: "Fail Target", value: m.fail_target, mono: true },
    ];
  }
  if (node.kind === "retriever") {
    return [
      ...base,
      { label: "Corpus Path", value: m.corpus_path, mono: true },
      { label: "Query State Key", value: m.query_state_key, mono: true },
      { label: "Output State Key", value: m.output_state_key, mono: true },
      { label: "Top K", value: m.top_k },
    ];
  }
  if (node.kind === "tester") {
    return [
      ...base,
      { label: "Provider", value: m.provider },
      { label: "Model", value: m.model, mono: true },
      { label: "Execution Mode", value: m.execution_mode },
      { label: "Candidate State Key", value: m.candidate_state_key, mono: true },
      { label: "Expected State Key", value: m.expected_state_key, mono: true },
      { label: "Test Code State Key", value: m.test_code_state_key, mono: true },
      { label: "Timeout Seconds", value: m.timeout_s },
      { label: "Max Output Bytes", value: m.max_output_bytes },
      { label: "Memory Limit MB", value: m.memory_limit_mb },
      { label: "System Prompt", value: m.system_prompt, pre: true },
    ];
  }
  return [...base, { label: "Metadata", value: m, mono: true, pre: true }];
}

function syntheticNode(nodeId: string, topology: Topology): GraphNode | undefined {
  if (nodeId === "START") {
    return {
      id: "START",
      kind: "router",
      name: "START",
      description: "Synthetic entry marker",
      metadata: { target: topology.entry },
    };
  }
  if (nodeId === "__end__") {
    return {
      id: "__end__",
      kind: "gate",
      name: "END",
      description: "Synthetic terminal marker",
      metadata: {},
    };
  }
  return undefined;
}

export default function SpecInspector({
  topology,
  spec,
  selectedNodeId,
  tab,
  onTabChange,
}: {
  topology?: Topology;
  spec?: WorkflowSpecResponse;
  selectedNodeId?: string;
  tab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
}) {
  const selectedNode =
    topology?.nodes.find((node) => node.id === selectedNodeId) ??
    (topology && selectedNodeId ? syntheticNode(selectedNodeId, topology) : undefined);
  const [goal, setGoal] = useState("");
  const [constraints, setConstraints] = useState(
    "Only change models, temperature, max_retries, prompts, or other node metadata. Preserve valid graph structure."
  );
  const [proposal, setProposal] = useState<MutationProposalResponse | undefined>(undefined);
  const [proposalError, setProposalError] = useState<string | undefined>(undefined);
  const [isProposing, setIsProposing] = useState(false);

  async function submitProposal() {
    if (!spec || !goal.trim()) return;
    setIsProposing(true);
    setProposal(undefined);
    setProposalError(undefined);
    try {
      const result = await proposeSpecMutation(spec.workflow, {
        goal,
        constraints,
        max_proposals: 1,
      });
      setProposal(result);
    } catch (error) {
      setProposalError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsProposing(false);
    }
  }

  return (
    <div className="h-full flex flex-col bg-white">
      <div className="border-b px-3 py-2">
        <div className="text-xs uppercase text-slate-500">Source inspector</div>
        <div className="text-sm font-semibold text-slate-800">{spec?.workflow ?? topology?.name ?? "-"}</div>
      </div>
      <div className="border-b flex text-xs">
        {(["node", "source", "validation", "propose"] as InspectorTab[]).map((item) => (
          <button
            key={item}
            type="button"
            className={[
              "px-3 py-2 border-r capitalize",
              tab === item ? "bg-slate-100 text-slate-900 font-medium" : "bg-white text-slate-500",
            ].join(" ")}
            onClick={() => onTabChange(item)}
          >
            {item}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {tab === "node" && (
          <div className="space-y-3">
            {!selectedNode && <div className="text-sm text-slate-500">Select a graph node to inspect its source metadata.</div>}
            {selectedNode && nodeFields(selectedNode).map((field) => <Field key={field.label} {...field} />)}
          </div>
        )}
        {tab === "source" && (
          <pre className="text-xs leading-relaxed whitespace-pre-wrap rounded border border-slate-200 bg-slate-950 text-slate-50 p-3 overflow-x-auto">
            {spec?.yaml ?? "Source YAML unavailable."}
          </pre>
        )}
        {tab === "validation" && (
          <div className="space-y-3 text-sm">
            <div className="rounded border border-emerald-200 bg-emerald-50 p-3 text-emerald-900">
              {spec ? "Validated by GraphSpec." : "Spec has not loaded."}
            </div>
            <Field label="Source Path" value={spec?.source_path ?? "-"} mono />
            <Field label="Schema Version" value={spec?.spec.schema_version ?? "-"} mono />
            <Field label="Entry Node" value={spec?.spec.entry ?? "-"} mono />
            <Field label="Budget" value={spec?.spec.budget ?? {}} mono pre />
            <Field label="Node Count" value={spec?.spec.nodes.length ?? 0} />
            <Field label="Edge Count" value={spec?.spec.edges.length ?? 0} />
            <Field label="Loop Count" value={spec?.spec.loops.length ?? 0} />
          </div>
        )}
        {tab === "propose" && (
          <div className="space-y-3 text-sm">
            <div className="rounded border border-amber-200 bg-amber-50 p-3 text-amber-900">
              Proposals are read-only. They validate and diff YAML but never modify workflow files.
            </div>
            <div className="space-y-1">
              <label className="text-[11px] uppercase tracking-wide text-slate-500">Mutation Goal</label>
              <textarea
                className="h-24 w-full resize-none rounded border border-slate-300 p-2 text-xs"
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                placeholder="Reduce cost while preserving behavior."
              />
            </div>
            <div className="space-y-1">
              <label className="text-[11px] uppercase tracking-wide text-slate-500">Constraints</label>
              <textarea
                className="h-20 w-full resize-none rounded border border-slate-300 p-2 text-xs"
                value={constraints}
                onChange={(event) => setConstraints(event.target.value)}
              />
            </div>
            <button
              type="button"
              className="rounded border border-slate-300 bg-slate-900 px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-400"
              disabled={!spec || !goal.trim() || isProposing}
              onClick={submitProposal}
            >
              {isProposing ? "Proposing..." : "Propose mutation"}
            </button>
            {proposalError && (
              <div className="whitespace-pre-wrap rounded border border-red-200 bg-red-50 p-3 text-xs text-red-800">
                {proposalError}
              </div>
            )}
            {proposal && (
              <div className="space-y-3">
                <div
                  className={[
                    "rounded border p-3 text-xs",
                    proposal.status === "valid"
                      ? "border-emerald-200 bg-emerald-50 text-emerald-900"
                      : "border-red-200 bg-red-50 text-red-900",
                  ].join(" ")}
                >
                  <div className="font-medium">Status: {proposal.status}</div>
                  <div>{proposal.summary}</div>
                </div>
                {proposal.validation_errors.length > 0 && (
                  <Field label="Validation Errors" value={proposal.validation_errors} mono pre />
                )}
                <Field label="Diff" value={proposal.diff || "No changes proposed."} mono pre />
                <Field label="Proposed YAML" value={proposal.proposed_yaml} mono pre />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
