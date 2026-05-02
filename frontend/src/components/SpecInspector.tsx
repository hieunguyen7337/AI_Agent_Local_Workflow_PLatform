import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  applySpecProposal,
  copyWorkflowTemplate,
  evaluateSpecProposal,
  fetchRollbackPreview,
  fetchRollbackSnapshots,
  fetchTopology,
  fetchWorkflowSpec,
  optimizeSpecProposals,
  proposeSpecMutation,
  restoreRollbackSnapshot,
} from "../api/client";
import GraphView from "./GraphView";
import type {
  ApplyProposalResponse,
  CopyTemplateResponse,
  GraphNode,
  MutationProposalResponse,
  OptimizationCandidate,
  OptimizeProposalsResponse,
  ProposalEvaluationResponse,
  RestoreRollbackResponse,
  RollbackPreviewResponse,
  Topology,
  WorkflowSpecResponse,
} from "../types";

type InspectorTab = "node" | "source" | "validation" | "propose" | "rollback";
const WORKFLOW_ID_RE = /^[a-z][a-z0-9_]*$/;

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
          "text-xs text-slate-200",
          mono ? "font-mono" : "",
          pre ? "whitespace-pre-wrap rounded border border-slate-700 bg-slate-800 p-2 leading-relaxed" : "break-words",
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
  if (node.kind === "embedding") {
    return [
      ...base,
      { label: "Provider", value: m.provider },
      { label: "Model", value: m.model, mono: true },
      { label: "Output State Key", value: m.output_state_key, mono: true },
      { label: "Dimensions", value: m.dimensions },
      { label: "Max Retries", value: m.max_retries },
      { label: "Input Template", value: m.input_template, pre: true },
      { label: "Image Inputs", value: m.image_inputs, mono: true, pre: true },
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
  if (node.kind === "approval") {
    return [
      ...base,
      { label: "Prompt", value: m.prompt, pre: true },
      { label: "Approval State Key", value: m.approval_state_key, mono: true },
      { label: "Approved Target", value: m.approved_target, mono: true },
      { label: "Rejected Target", value: m.rejected_target, mono: true },
    ];
  }
  if (node.kind === "subgraph") {
    return [
      ...base,
      { label: "Referenced Workflow", value: m.workflow, mono: true },
      { label: "Description", value: node.description || m.description, pre: true },
      { label: "Input Mapping", value: m.inputs, mono: true, pre: true },
      { label: "Output Mapping", value: m.outputs, mono: true, pre: true },
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
  if (node.kind === "vector_retriever") {
    return [
      ...base,
      { label: "Index Path", value: m.index_path, mono: true },
      { label: "Query Embedding State Key", value: m.query_embedding_state_key, mono: true },
      { label: "Output State Key", value: m.output_state_key, mono: true },
      { label: "ID Output State Key", value: m.id_output_state_key, mono: true },
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
  workflowIds = [],
  selectedNodeId,
  tab,
  onTabChange,
  onApplied,
  onTemplateCopied,
  availableTabs,
  title = "Source inspector",
}: {
  topology?: Topology;
  spec?: WorkflowSpecResponse;
  workflowIds?: string[];
  selectedNodeId?: string;
  tab: InspectorTab;
  onTabChange: (tab: InspectorTab) => void;
  onApplied?: () => void | Promise<void>;
  onTemplateCopied?: (result: CopyTemplateResponse) => void | Promise<void>;
  availableTabs?: InspectorTab[];
  title?: string;
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
  const [evaluation, setEvaluation] = useState<ProposalEvaluationResponse | undefined>(undefined);
  const [evaluationError, setEvaluationError] = useState<string | undefined>(undefined);
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [evalRunsPerFixture, setEvalRunsPerFixture] = useState(1);
  const [evalCostCap, setEvalCostCap] = useState(2);
  const [candidateCount, setCandidateCount] = useState(3);
  const [optimizationRunsPerFixture, setOptimizationRunsPerFixture] = useState(1);
  const [optimizationCostCap, setOptimizationCostCap] = useState(5);
  const [optimization, setOptimization] = useState<OptimizeProposalsResponse | undefined>(undefined);
  const [optimizationError, setOptimizationError] = useState<string | undefined>(undefined);
  const [isOptimizing, setIsOptimizing] = useState(false);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | undefined>(undefined);
  const [applyConfirmed, setApplyConfirmed] = useState(false);
  const [applyResult, setApplyResult] = useState<ApplyProposalResponse | undefined>(undefined);
  const [applyError, setApplyError] = useState<string | undefined>(undefined);
  const [isApplying, setIsApplying] = useState(false);
  const [selectedRollbackId, setSelectedRollbackId] = useState<string | undefined>(undefined);
  const [rollbackPreview, setRollbackPreview] = useState<RollbackPreviewResponse | undefined>(undefined);
  const [rollbackPreviewError, setRollbackPreviewError] = useState<string | undefined>(undefined);
  const [isLoadingRollbackPreview, setIsLoadingRollbackPreview] = useState(false);
  const [rollbackConfirmed, setRollbackConfirmed] = useState(false);
  const [rollbackResult, setRollbackResult] = useState<RestoreRollbackResponse | undefined>(undefined);
  const [rollbackError, setRollbackError] = useState<string | undefined>(undefined);
  const [isRestoringRollback, setIsRestoringRollback] = useState(false);
  const [openedChildWorkflow, setOpenedChildWorkflow] = useState<string | undefined>(undefined);
  const [copyWorkflowId, setCopyWorkflowId] = useState("");
  const [copyName, setCopyName] = useState("");
  const [copyDescription, setCopyDescription] = useState("");
  const [copyCategory, setCopyCategory] = useState("");
  const [copyTags, setCopyTags] = useState("");
  const [copyAcceptedBy, setCopyAcceptedBy] = useState("local-user");
  const [copyConfirmed, setCopyConfirmed] = useState(false);
  const [copyResult, setCopyResult] = useState<CopyTemplateResponse | undefined>(undefined);
  const [copyError, setCopyError] = useState<string | undefined>(undefined);
  const [isCopyingTemplate, setIsCopyingTemplate] = useState(false);
  const copyWorkflowIdTrimmed = copyWorkflowId.trim();
  const copyWorkflowIdExists = copyWorkflowIdTrimmed !== "" && workflowIds.includes(copyWorkflowIdTrimmed);
  const copyWorkflowIdError =
    copyWorkflowIdTrimmed === ""
      ? undefined
      : !WORKFLOW_ID_RE.test(copyWorkflowIdTrimmed)
        ? "Workflow id must be lowercase snake_case and start with a letter."
        : copyWorkflowIdExists
          ? "Workflow id already exists."
          : undefined;
  const canCopyTemplate =
    Boolean(spec) && copyWorkflowIdTrimmed !== "" && !copyWorkflowIdError && copyConfirmed && !isCopyingTemplate;

  const selectedSubgraphWorkflow =
    selectedNode?.kind === "subgraph" && typeof selectedNode.metadata?.workflow === "string"
      ? selectedNode.metadata.workflow
      : undefined;
  const childWorkflow = openedChildWorkflow && openedChildWorkflow === selectedSubgraphWorkflow ? openedChildWorkflow : undefined;
  const childTopology = useQuery({
    queryKey: ["child-topology", childWorkflow],
    queryFn: () => fetchTopology(childWorkflow!),
    enabled: Boolean(childWorkflow),
    staleTime: Infinity,
    refetchInterval: false,
  });
  const childSpec = useQuery({
    queryKey: ["child-spec", childWorkflow],
    queryFn: () => fetchWorkflowSpec(childWorkflow!),
    enabled: Boolean(childWorkflow),
    staleTime: Infinity,
    refetchInterval: false,
  });
  const rollbackSnapshots = useQuery({
    queryKey: ["rollback-snapshots", spec?.workflow],
    queryFn: () => fetchRollbackSnapshots(spec!.workflow),
    enabled: Boolean(spec?.workflow) && tab === "rollback",
    staleTime: 0,
    refetchInterval: false,
  });

  useEffect(() => {
    setProposal(undefined);
    setProposalError(undefined);
    setEvaluation(undefined);
    setEvaluationError(undefined);
    setOptimization(undefined);
    setOptimizationError(undefined);
    setSelectedCandidateId(undefined);
    setApplyConfirmed(false);
    setApplyResult(undefined);
    setApplyError(undefined);
    setSelectedRollbackId(undefined);
    setRollbackPreview(undefined);
    setRollbackPreviewError(undefined);
    setRollbackConfirmed(false);
    setRollbackResult(undefined);
    setRollbackError(undefined);
    setOpenedChildWorkflow(undefined);
    setCopyWorkflowId("");
    setCopyName("");
    setCopyDescription("");
    setCopyCategory("");
    setCopyTags("");
    setCopyAcceptedBy("local-user");
    setCopyConfirmed(false);
    setCopyResult(undefined);
    setCopyError(undefined);
  }, [spec?.workflow]);

  useEffect(() => {
    setOpenedChildWorkflow(undefined);
  }, [selectedNodeId]);

  async function submitProposal() {
    if (!spec || !goal.trim()) return;
    setIsProposing(true);
    setProposal(undefined);
    setProposalError(undefined);
    setEvaluation(undefined);
    setEvaluationError(undefined);
    setOptimization(undefined);
    setOptimizationError(undefined);
    setSelectedCandidateId(undefined);
    setApplyConfirmed(false);
    setApplyResult(undefined);
    setApplyError(undefined);
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

  async function submitEvaluation() {
    if (!spec || !proposal || proposal.status !== "valid") return;
    setIsEvaluating(true);
    setEvaluation(undefined);
    setEvaluationError(undefined);
    try {
      const result = await evaluateSpecProposal(spec.workflow, {
        proposed_yaml: proposal.proposed_yaml,
        n_per_fixture: evalRunsPerFixture,
        max_cost_usd: evalCostCap,
      });
      setEvaluation(result);
    } catch (error) {
      setEvaluationError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsEvaluating(false);
    }
  }

  async function submitOptimization() {
    if (!spec || !goal.trim()) return;
    setIsOptimizing(true);
    setOptimization(undefined);
    setOptimizationError(undefined);
    setSelectedCandidateId(undefined);
    setApplyConfirmed(false);
    setApplyResult(undefined);
    setApplyError(undefined);
    try {
      const result = await optimizeSpecProposals(spec.workflow, {
        goal,
        constraints,
        candidate_count: candidateCount,
        n_per_fixture: optimizationRunsPerFixture,
        max_cost_usd: optimizationCostCap,
      });
      setOptimization(result);
      setSelectedCandidateId(
        result.recommended_candidate_id ??
          result.candidates.find((candidate) => candidate.status === "valid")?.candidate_id
      );
    } catch (error) {
      setOptimizationError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsOptimizing(false);
    }
  }

  async function submitApply(candidate?: OptimizationCandidate) {
    if (!spec || !applyConfirmed) return;
    const proposedYaml = candidate?.proposed_yaml ?? proposal?.proposed_yaml;
    const summary = candidate?.summary ?? proposal?.summary;
    const evaluationArtifact = candidate?.run_artifact ?? evaluation?.run_artifact ?? null;
    if (!proposedYaml || (candidate ? candidate.status !== "valid" : proposal?.status !== "valid")) return;
    setIsApplying(true);
    setApplyResult(undefined);
    setApplyError(undefined);
    try {
      const result = await applySpecProposal(spec.workflow, {
        proposed_yaml: proposedYaml,
        proposal_summary: summary,
        evaluation_artifact: evaluationArtifact,
        accepted_by: "local-user",
      });
      setApplyResult(result);
      await onApplied?.();
    } catch (error) {
      setApplyError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsApplying(false);
    }
  }

  async function selectRollbackSnapshot(snapshotId: string) {
    if (!spec) return;
    setSelectedRollbackId(snapshotId);
    setRollbackPreview(undefined);
    setRollbackPreviewError(undefined);
    setRollbackConfirmed(false);
    setRollbackResult(undefined);
    setRollbackError(undefined);
    setIsLoadingRollbackPreview(true);
    try {
      const result = await fetchRollbackPreview(spec.workflow, snapshotId);
      setRollbackPreview(result);
    } catch (error) {
      setRollbackPreviewError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsLoadingRollbackPreview(false);
    }
  }

  async function submitRollbackRestore() {
    if (!spec || !selectedRollbackId || !rollbackConfirmed || isRestoringRollback) return;
    setIsRestoringRollback(true);
    setRollbackError(undefined);
    setRollbackResult(undefined);
    try {
      const result = await restoreRollbackSnapshot(spec.workflow, selectedRollbackId, {
        accepted_by: "local-user",
      });
      setRollbackResult(result);
      setRollbackConfirmed(false);
      await Promise.all([rollbackSnapshots.refetch(), onApplied?.()]);
    } catch (error) {
      setRollbackError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsRestoringRollback(false);
    }
  }

  async function submitTemplateCopy() {
    if (!spec || !canCopyTemplate) return;
    setIsCopyingTemplate(true);
    setCopyError(undefined);
    setCopyResult(undefined);
    try {
      const tags = copyTags
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      const result = await copyWorkflowTemplate(spec.workflow, {
        new_workflow_id: copyWorkflowIdTrimmed,
        name: copyName.trim() || undefined,
        description: copyDescription.trim() || undefined,
        category: copyCategory.trim() || undefined,
        tags: tags.length > 0 ? tags : undefined,
        accepted_by: copyAcceptedBy.trim() || undefined,
      });
      setCopyResult(result);
      setCopyConfirmed(false);
      await onTemplateCopied?.(result);
    } catch (error) {
      setCopyError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsCopyingTemplate(false);
    }
  }

  const selectedCandidate = optimization?.candidates.find(
    (candidate) => candidate.candidate_id === selectedCandidateId
  );
  const tabs = availableTabs ?? (["node", "source", "validation", "propose", "rollback"] as InspectorTab[]);

  return (
    <div className="h-full flex flex-col bg-slate-950">
      <div className="border-b border-slate-700 px-3 py-2">
        <div className="text-xs uppercase text-slate-400">{title}</div>
        <div className="text-sm font-semibold text-slate-100">{spec?.workflow ?? topology?.name ?? "-"}</div>
      </div>
      {tabs.length > 1 && (
        <div className="border-b border-slate-700 flex text-xs">
          {tabs.map((item) => (
            <button
              key={item}
              type="button"
              className={[
                "px-3 py-2 border-r border-slate-700 capitalize",
                tab === item ? "bg-slate-700 text-slate-100 font-medium" : "bg-slate-950 text-slate-400",
              ].join(" ")}
              onClick={() => onTabChange(item)}
            >
              {item}
            </button>
          ))}
        </div>
      )}
      <div className="flex-1 overflow-y-auto p-3">
        {tab === "node" && (
          <div className="space-y-3">
            {spec?.spec.template && (
              <div className="space-y-3 rounded border border-violet-700 bg-violet-900/20 p-3">
                <div>
                  <div className="text-xs font-medium text-violet-200">Copy template</div>
                  <div className="text-xs text-violet-300">
                    Copy this YAML template into a new canonical workflow. The copy is written with template: false.
                  </div>
                  <div className="mt-1 text-xs text-violet-300">
                    Prompt placeholders such as {"{user_input}"} are copied unchanged; customize them after copy through the normal source or proposal flow.
                  </div>
                </div>
                {spec.spec.template_parameters.length > 0 && (
                  <div className="space-y-2 rounded border border-violet-700 bg-slate-800 p-2">
                    <div className="text-[11px] uppercase tracking-wide text-violet-400">Expected inputs</div>
                    <div className="space-y-2">
                      {spec.spec.template_parameters.map((parameter) => (
                        <div key={parameter.key} className="text-xs text-slate-200">
                          <div className="font-mono font-medium text-violet-300">{parameter.key}</div>
                          <div className="text-slate-300">{parameter.description}</div>
                          {(parameter.state_key || parameter.example) && (
                            <div className="mt-1 font-mono text-[11px] text-slate-500">
                              {parameter.state_key ? `state: ${parameter.state_key}` : ""}
                              {parameter.state_key && parameter.example ? " | " : ""}
                              {parameter.example ? `example: ${parameter.example}` : ""}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <label className="space-y-1">
                    <div className="text-[11px] uppercase tracking-wide text-violet-400">New workflow id</div>
                    <input
                      className={[
                        "w-full rounded border bg-slate-800 text-slate-100 p-1.5 text-xs",
                        copyWorkflowIdError ? "border-red-500" : "border-violet-600",
                      ].join(" ")}
                      value={copyWorkflowId}
                      onChange={(event) => setCopyWorkflowId(event.target.value)}
                      placeholder="my_new_workflow"
                    />
                    {copyWorkflowIdError && <div className="text-xs text-red-400">{copyWorkflowIdError}</div>}
                  </label>
                  <label className="space-y-1">
                    <div className="text-[11px] uppercase tracking-wide text-violet-400">Accepted by</div>
                    <input
                      className="w-full rounded border border-violet-600 bg-slate-800 text-slate-100 p-1.5 text-xs"
                      value={copyAcceptedBy}
                      onChange={(event) => setCopyAcceptedBy(event.target.value)}
                    />
                  </label>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <label className="space-y-1">
                    <div className="text-[11px] uppercase tracking-wide text-violet-400">Name</div>
                    <input
                      className="w-full rounded border border-violet-600 bg-slate-800 text-slate-100 p-1.5 text-xs"
                      value={copyName}
                      onChange={(event) => setCopyName(event.target.value)}
                      placeholder="defaults to workflow id"
                    />
                  </label>
                  <label className="space-y-1">
                    <div className="text-[11px] uppercase tracking-wide text-violet-400">Category</div>
                    <input
                      className="w-full rounded border border-violet-600 bg-slate-800 text-slate-100 p-1.5 text-xs"
                      value={copyCategory}
                      onChange={(event) => setCopyCategory(event.target.value)}
                      placeholder="defaults to template category"
                    />
                  </label>
                </div>
                <label className="space-y-1 block">
                  <div className="text-[11px] uppercase tracking-wide text-violet-400">Description</div>
                  <textarea
                    className="h-14 w-full resize-none rounded border border-violet-600 bg-slate-800 text-slate-100 p-1.5 text-xs"
                    value={copyDescription}
                    onChange={(event) => setCopyDescription(event.target.value)}
                    placeholder="defaults to template description"
                  />
                </label>
                <label className="space-y-1 block">
                  <div className="text-[11px] uppercase tracking-wide text-violet-400">Tags</div>
                  <input
                    className="w-full rounded border border-violet-600 bg-slate-800 text-slate-100 p-1.5 text-xs"
                    value={copyTags}
                    onChange={(event) => setCopyTags(event.target.value)}
                    placeholder="comma-separated, defaults to template tags"
                  />
                </label>
                <label className="flex items-start gap-2 text-xs text-violet-200">
                  <input
                    className="mt-0.5"
                    type="checkbox"
                    checked={copyConfirmed}
                    onChange={(event) => setCopyConfirmed(event.target.checked)}
                  />
                  <span>I reviewed this template and want to create a new canonical workflow YAML file.</span>
                </label>
                <button
                  type="button"
                  className="w-fit rounded border border-violet-500 bg-violet-700 px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-700 disabled:border-slate-700"
                  disabled={!canCopyTemplate}
                  onClick={submitTemplateCopy}
                >
                  {isCopyingTemplate ? "Copying..." : "Copy template"}
                </button>
                {copyError && (
                  <div className="whitespace-pre-wrap rounded border border-red-700 bg-red-900/30 p-3 text-xs text-red-400">
                    {copyError}
                  </div>
                )}
                {copyResult && (
                  <div className="space-y-2 rounded border border-emerald-700 bg-emerald-900/30 p-3 text-xs text-emerald-300">
                    <div className="font-medium">Copied to {copyResult.workflow}</div>
                    <div>
                      This is now a normal template: false workflow. Customize prompts and metadata through source review or the proposal flow.
                    </div>
                    <Field label="Source Path" value={copyResult.source_path} mono />
                    <Field label="Audit Path" value={copyResult.audit_path} mono />
                  </div>
                )}
              </div>
            )}
            {!selectedNode && <div className="text-sm text-slate-500">Select a graph node to inspect its source metadata.</div>}
            {selectedNode && nodeFields(selectedNode).map((field) => <Field key={field.label} {...field} />)}
            {selectedSubgraphWorkflow && (
              <div className="space-y-3 rounded border border-slate-700 bg-slate-900 p-3">
                <div className="text-xs text-slate-400">
                  Subgraph v1 supports acyclic, non-approval child workflows. Nested approval resume is deferred.
                </div>
                <button
                  type="button"
                  className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-xs font-medium text-slate-200"
                  onClick={() =>
                    setOpenedChildWorkflow(
                      openedChildWorkflow === selectedSubgraphWorkflow ? undefined : selectedSubgraphWorkflow
                    )
                  }
                >
                  {openedChildWorkflow === selectedSubgraphWorkflow ? "Close child graph" : "Open child graph"}
                </button>
                {childWorkflow && (
                  <div className="space-y-3">
                    {childTopology.isLoading && <div className="text-xs text-slate-500">Loading child graph...</div>}
                    {childTopology.error && <div className="text-xs text-red-400">Error loading child graph.</div>}
                    {childTopology.data && (
                      <div className="h-72 rounded border border-slate-700 bg-slate-950">
                        <GraphView topology={childTopology.data} nodeMetrics={{}} />
                      </div>
                    )}
                    {childSpec.data && (
                      <div className="space-y-2">
                        <Field label="Child Source Path" value={childSpec.data.source_path} mono />
                        <Field label="Child Entry" value={childSpec.data.spec.entry} mono />
                        <Field label="Child Budget" value={childSpec.data.spec.budget} mono pre />
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
        {tab === "source" && (
          <pre className="text-xs leading-relaxed whitespace-pre-wrap rounded border border-slate-700 bg-slate-950 text-slate-50 p-3 overflow-x-auto">
            {spec?.yaml ?? "Source YAML unavailable."}
          </pre>
        )}
        {tab === "validation" && (
          <div className="space-y-3 text-sm">
            <div className="rounded border border-emerald-700 bg-emerald-900/30 p-3 text-emerald-300">
              {spec ? "Validated by GraphSpec." : "Spec has not loaded."}
            </div>
            {spec?.spec.nodes.some((node) => node.kind === "subgraph") && (
              <div className="rounded border border-violet-700 bg-violet-900/20 p-3 text-xs text-violet-300">
                Subgraph validation checks referenced workflow files and cycles. M5.2 still limits child workflows to acyclic, non-approval specs.
              </div>
            )}
            <Field label="Source Path" value={spec?.source_path ?? "-"} mono />
            <Field label="Schema Version" value={spec?.spec.schema_version ?? "-"} mono />
            <Field label="Entry Node" value={spec?.spec.entry ?? "-"} mono />
            <Field label="Budget" value={spec?.spec.budget ?? {}} mono pre />
            <Field label="Node Count" value={spec?.spec.nodes.length ?? 0} />
            <Field label="Edge Count" value={spec?.spec.edges.length ?? 0} />
            <Field label="Loop Count" value={spec?.spec.loops.length ?? 0} />
          </div>
        )}
        {tab === "rollback" && (
          <div className="space-y-3 text-sm">
            <div className="rounded border border-sky-700 bg-sky-900/20 p-3 text-sky-300">
              Rollback restores are validated, human-confirmed, and create a new audit snapshot before writing the canonical YAML.
            </div>
            {rollbackSnapshots.isLoading && <div className="text-xs text-slate-500">Loading rollback snapshots...</div>}
            {rollbackSnapshots.error && (
              <div className="whitespace-pre-wrap rounded border border-red-700 bg-red-900/30 p-3 text-xs text-red-400">
                Error loading rollback snapshots.
              </div>
            )}
            {rollbackSnapshots.data?.snapshots.length === 0 && (
              <div className="rounded border border-slate-700 bg-slate-800 p-3 text-xs text-slate-400">
                No rollback snapshots found for this workflow.
              </div>
            )}
            {rollbackSnapshots.data && rollbackSnapshots.data.snapshots.length > 0 && (
              <div className="space-y-2">
                {rollbackSnapshots.data.snapshots.map((snapshot) => {
                  const selected = snapshot.snapshot_id === selectedRollbackId;
                  return (
                    <button
                      key={snapshot.snapshot_id}
                      type="button"
                      className={[
                        "w-full rounded border p-3 text-left text-xs",
                        selected ? "border-sky-400 bg-slate-800" : "border-sky-700 bg-sky-900/20",
                      ].join(" ")}
                      onClick={() => selectRollbackSnapshot(snapshot.snapshot_id)}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="font-mono font-medium">{snapshot.snapshot_id}</div>
                        <div>{snapshot.created_at}</div>
                      </div>
                      <div className="text-slate-300">{snapshot.proposal_summary || "No summary recorded."}</div>
                      <div className="font-mono text-[11px] text-slate-500">
                        by {snapshot.accepted_by} | {snapshot.rollback_path}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
            {isLoadingRollbackPreview && <div className="text-xs text-slate-500">Loading rollback preview...</div>}
            {rollbackPreviewError && (
              <div className="whitespace-pre-wrap rounded border border-red-700 bg-red-900/30 p-3 text-xs text-red-400">
                {rollbackPreviewError}
              </div>
            )}
            {rollbackPreview && (
              <div className="space-y-3 rounded border border-sky-700 bg-slate-900 p-3">
                <div className="text-xs font-medium text-slate-200">Rollback preview</div>
                <Field label="Snapshot" value={rollbackPreview.snapshot_id} mono />
                <Field label="Diff" value={rollbackPreview.diff || "No changes."} mono pre />
                <Field label="Rollback YAML" value={rollbackPreview.rollback_yaml} mono pre />
                <div className="space-y-2 rounded border border-slate-700 bg-slate-900 p-3">
                  <label className="flex items-start gap-2 text-xs text-slate-300">
                    <input
                      className="mt-0.5"
                      type="checkbox"
                      checked={rollbackConfirmed}
                      onChange={(event) => setRollbackConfirmed(event.target.checked)}
                    />
                    <span>I reviewed this rollback snapshot and want to restore it as the canonical workflow YAML.</span>
                  </label>
                  <button
                    type="button"
                    className="rounded border border-sky-500 bg-sky-700 px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-700 disabled:border-slate-700"
                    disabled={!rollbackConfirmed || isRestoringRollback}
                    onClick={submitRollbackRestore}
                  >
                    {isRestoringRollback ? "Restoring..." : "Restore snapshot"}
                  </button>
                </div>
              </div>
            )}
            {rollbackError && (
              <div className="whitespace-pre-wrap rounded border border-red-700 bg-red-900/30 p-3 text-xs text-red-400">
                {rollbackError}
              </div>
            )}
            {rollbackResult && (
              <div className="space-y-2 rounded border border-emerald-700 bg-emerald-900/30 p-3 text-xs text-emerald-300">
                <div className="font-medium">Restored {rollbackResult.snapshot_id}</div>
                <Field label="Source Path" value={rollbackResult.source_path} mono />
                <Field label="Audit Path" value={rollbackResult.audit_path} mono />
                <Field label="Rollback Snapshot" value={rollbackResult.rollback_path} mono />
              </div>
            )}
          </div>
        )}
        {tab === "propose" && (
          <div className="space-y-3 text-sm">
            <div className="rounded border border-amber-700 bg-amber-900/30 p-3 text-amber-300">
              Proposals validate and diff YAML first. Applying a valid proposal writes the canonical workflow file and creates a rollback snapshot.
            </div>
            <div className="space-y-1">
              <label className="text-[11px] uppercase tracking-wide text-slate-500">Mutation Goal</label>
              <textarea
                className="h-24 w-full resize-none rounded border border-slate-600 bg-slate-800 text-slate-100 p-2 text-xs"
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                placeholder="Reduce cost while preserving behavior."
              />
            </div>
            <div className="space-y-1">
              <label className="text-[11px] uppercase tracking-wide text-slate-500">Constraints</label>
              <textarea
                className="h-20 w-full resize-none rounded border border-slate-600 bg-slate-800 text-slate-100 p-2 text-xs"
                value={constraints}
                onChange={(event) => setConstraints(event.target.value)}
              />
            </div>
            <button
              type="button"
              className="rounded border border-slate-500 bg-slate-700 px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
              disabled={!spec || !goal.trim() || isProposing}
              onClick={submitProposal}
            >
              {isProposing ? "Proposing..." : "Propose mutation"}
            </button>
            {proposalError && (
              <div className="whitespace-pre-wrap rounded border border-red-700 bg-red-900/30 p-3 text-xs text-red-400">
                {proposalError}
              </div>
            )}
            <div className="space-y-3 rounded border border-blue-700 bg-blue-900/20 p-3">
              <div className="text-xs font-medium text-blue-200">Optimize candidates</div>
              <div className="grid grid-cols-3 gap-2">
                <label className="space-y-1">
                  <div className="text-[11px] uppercase tracking-wide text-blue-400">Candidates</div>
                  <input
                    className="w-full rounded border border-blue-600 bg-slate-800 text-slate-100 p-1.5 text-xs"
                    type="number"
                    min={2}
                    max={5}
                    value={candidateCount}
                    onChange={(event) => setCandidateCount(Number(event.target.value))}
                  />
                </label>
                <label className="space-y-1">
                  <div className="text-[11px] uppercase tracking-wide text-blue-400">Runs / Fixture</div>
                  <input
                    className="w-full rounded border border-blue-600 bg-slate-800 text-slate-100 p-1.5 text-xs"
                    type="number"
                    min={1}
                    max={8}
                    value={optimizationRunsPerFixture}
                    onChange={(event) => setOptimizationRunsPerFixture(Number(event.target.value))}
                  />
                </label>
                <label className="space-y-1">
                  <div className="text-[11px] uppercase tracking-wide text-blue-400">Cost Cap USD</div>
                  <input
                    className="w-full rounded border border-blue-600 bg-slate-800 text-slate-100 p-1.5 text-xs"
                    type="number"
                    min={0.01}
                    step={0.01}
                    value={optimizationCostCap}
                    onChange={(event) => setOptimizationCostCap(Number(event.target.value))}
                  />
                </label>
              </div>
              <button
                type="button"
                className="rounded border border-blue-500 bg-blue-700 px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-700 disabled:border-slate-700"
                disabled={!spec || !goal.trim() || isOptimizing}
                onClick={submitOptimization}
              >
                {isOptimizing ? "Optimizing..." : "Optimize candidates"}
              </button>
              {optimizationError && (
                <div className="whitespace-pre-wrap rounded border border-red-700 bg-red-900/30 p-3 text-xs text-red-400">
                  {optimizationError}
                </div>
              )}
              {optimization && (
                <div className="space-y-3">
                  <div
                    className={[
                      "rounded border p-3 text-xs",
                      optimization.status === "ok"
                        ? "border-emerald-700 bg-emerald-900/30 text-emerald-300"
                        : "border-amber-700 bg-amber-900/30 text-amber-300",
                    ].join(" ")}
                  >
                    <div className="font-medium">Optimization status: {optimization.status}</div>
                    <div>
                      Recommended:{" "}
                      <span className="font-mono">{optimization.recommended_candidate_id ?? "no recommendation"}</span>
                    </div>
                    <div className="font-mono text-[11px]">{optimization.report_artifact}</div>
                  </div>
                  <div className="grid grid-cols-1 gap-2">
                    {optimization.candidates.map((candidate) => {
                      const overall = candidate.eval?.overall ?? {};
                      const selected = candidate.candidate_id === selectedCandidateId;
                      return (
                        <button
                          key={candidate.candidate_id}
                          type="button"
                          className={[
                            "text-left rounded border p-3 text-xs",
                            selected ? "border-blue-400 bg-slate-800" : "border-blue-700 bg-blue-900/20",
                          ].join(" ")}
                          onClick={() => setSelectedCandidateId(candidate.candidate_id)}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <div className="font-medium">
                              {candidate.candidate_id}
                              {candidate.recommended ? " | recommended" : ""}
                            </div>
                            <div>{candidate.status}</div>
                          </div>
                          <div className="text-slate-300">{candidate.summary}</div>
                          <div className="font-mono text-[11px] text-slate-400">
                            rank {candidate.rank ?? "-"} | pass{" "}
                            {typeof overall.pass_rate === "number" ? `${(overall.pass_rate * 100).toFixed(1)}%` : "-"} | cost{" "}
                            {typeof overall.mean_cost_usd === "number" ? `$${overall.mean_cost_usd.toFixed(4)}` : "-"} | latency{" "}
                            {typeof overall.mean_latency_ms === "number" ? `${overall.mean_latency_ms.toFixed(0)}ms` : "-"}
                          </div>
                        </button>
                      );
                    })}
                  </div>
                  {selectedCandidate && (
                    <div className="space-y-3 rounded border border-blue-700 bg-slate-900 p-3">
                      <div className="text-xs font-medium text-slate-200">Selected candidate</div>
                      {selectedCandidate.validation_errors.length > 0 && (
                        <Field label="Validation Errors" value={selectedCandidate.validation_errors} mono pre />
                      )}
                      <Field label="Eval" value={selectedCandidate.eval ?? {}} mono pre />
                      <Field label="Diff" value={selectedCandidate.diff || "No changes proposed."} mono pre />
                      <Field label="Proposed YAML" value={selectedCandidate.proposed_yaml} mono pre />
                      {selectedCandidate.status === "valid" && (
                        <div className="space-y-2 rounded border border-slate-700 bg-slate-900 p-3">
                          <label className="flex items-start gap-2 text-xs text-slate-300">
                            <input
                              className="mt-0.5"
                              type="checkbox"
                              checked={applyConfirmed}
                              onChange={(event) => setApplyConfirmed(event.target.checked)}
                            />
                            <span>I reviewed this candidate and want to write it to the canonical workflow file.</span>
                          </label>
                          <button
                            type="button"
                            className="rounded border border-slate-500 bg-slate-700 px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
                            disabled={!applyConfirmed || isApplying}
                            onClick={() => submitApply(selectedCandidate)}
                          >
                            {isApplying ? "Applying..." : "Apply selected candidate"}
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
            {proposal && (
              <div className="space-y-3">
                <div
                  className={[
                    "rounded border p-3 text-xs",
                    proposal.status === "valid"
                      ? "border-emerald-700 bg-emerald-900/30 text-emerald-300"
                      : "border-red-700 bg-red-900/30 text-red-300",
                  ].join(" ")}
                >
                  <div className="font-medium">Status: {proposal.status}</div>
                  <div>{proposal.summary}</div>
                </div>
                {proposal.validation_errors.length > 0 && (
                  <Field label="Validation Errors" value={proposal.validation_errors} mono pre />
                )}
                {proposal.status === "valid" && (
                  <>
                    <div className="space-y-3 rounded border border-slate-700 bg-slate-900 p-3">
                      <div className="text-xs font-medium text-slate-200">Evaluate proposal</div>
                      <div className="grid grid-cols-2 gap-2">
                        <label className="space-y-1">
                          <div className="text-[11px] uppercase tracking-wide text-slate-500">Runs / Fixture</div>
                          <input
                            className="w-full rounded border border-slate-600 bg-slate-800 text-slate-100 p-1.5 text-xs"
                            type="number"
                            min={1}
                            max={8}
                            value={evalRunsPerFixture}
                            onChange={(event) => setEvalRunsPerFixture(Number(event.target.value))}
                          />
                        </label>
                        <label className="space-y-1">
                          <div className="text-[11px] uppercase tracking-wide text-slate-500">Cost Cap USD</div>
                          <input
                            className="w-full rounded border border-slate-600 bg-slate-800 text-slate-100 p-1.5 text-xs"
                            type="number"
                            min={0.01}
                            step={0.01}
                            value={evalCostCap}
                            onChange={(event) => setEvalCostCap(Number(event.target.value))}
                          />
                        </label>
                      </div>
                      <button
                        type="button"
                        className="rounded border border-slate-600 bg-slate-800 px-3 py-2 text-xs font-medium text-slate-200 disabled:cursor-not-allowed disabled:text-slate-500"
                        disabled={isEvaluating}
                        onClick={submitEvaluation}
                      >
                        {isEvaluating ? "Evaluating..." : "Evaluate proposal"}
                      </button>
                      {evaluationError && (
                        <div className="whitespace-pre-wrap rounded border border-red-700 bg-red-900/30 p-3 text-xs text-red-400">
                          {evaluationError}
                        </div>
                      )}
                      {evaluation && (
                        <div className="space-y-2">
                          <div
                            className={[
                              "rounded border p-3 text-xs",
                              evaluation.status === "ok"
                                ? "border-emerald-700 bg-emerald-900/30 text-emerald-300"
                                : "border-amber-700 bg-amber-900/30 text-amber-300",
                            ].join(" ")}
                          >
                            <div className="font-medium">Eval status: {evaluation.status}</div>
                            {evaluation.eval && (
                              <div>
                                Runs: {evaluation.eval.completed_run_count} | Pass rate:{" "}
                                {(evaluation.eval.overall.pass_rate * 100).toFixed(1)}% | Mean cost: $
                                {evaluation.eval.overall.mean_cost_usd.toFixed(4)} | Mean latency:{" "}
                                {evaluation.eval.overall.mean_latency_ms.toFixed(0)}ms
                              </div>
                            )}
                          </div>
                          {evaluation.validation_errors.length > 0 && (
                            <Field label="Eval Validation Errors" value={evaluation.validation_errors} mono pre />
                          )}
                          <Field label="Baseline Comparison" value={evaluation.eval?.baseline_comparison ?? {}} mono pre />
                          <Field label="Run Artifact" value={evaluation.run_artifact ?? "-"} mono />
                        </div>
                      )}
                    </div>
                    <div className="space-y-3 rounded border border-slate-700 bg-slate-900 p-3">
                      <div className="text-xs font-medium text-slate-200">Apply proposal</div>
                      {evaluation?.eval && (
                        <div className="rounded border border-slate-700 bg-slate-800 p-2 text-xs text-slate-300">
                          Eval evidence: {evaluation.eval.completed_run_count} runs,{" "}
                          {(evaluation.eval.overall.pass_rate * 100).toFixed(1)}% pass rate, $
                          {evaluation.eval.overall.mean_cost_usd.toFixed(4)} mean cost.
                        </div>
                      )}
                      <label className="flex items-start gap-2 text-xs text-slate-300">
                        <input
                          className="mt-0.5"
                          type="checkbox"
                          checked={applyConfirmed}
                          onChange={(event) => setApplyConfirmed(event.target.checked)}
                        />
                        <span>I reviewed the diff and want to write this YAML to the canonical workflow file.</span>
                      </label>
                      <button
                        type="button"
                        className="rounded border border-slate-500 bg-slate-700 px-3 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:bg-slate-800 disabled:text-slate-500"
                        disabled={!applyConfirmed || isApplying}
                        onClick={() => submitApply()}
                      >
                        {isApplying ? "Applying..." : "Apply proposal"}
                      </button>
                      {applyError && (
                        <div className="whitespace-pre-wrap rounded border border-red-700 bg-red-900/30 p-3 text-xs text-red-400">
                          {applyError}
                        </div>
                      )}
                      {applyResult && (
                        <div className="space-y-2 rounded border border-emerald-700 bg-emerald-900/30 p-3 text-xs text-emerald-300">
                          <div className="font-medium">Applied to {applyResult.source_path}</div>
                          <Field label="Audit Path" value={applyResult.audit_path} mono />
                          <Field label="Rollback Snapshot" value={applyResult.rollback_path} mono />
                        </div>
                      )}
                    </div>
                  </>
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
