import { type ReactNode, useEffect, useMemo, useRef } from "react";
import ReactFlow, { Background, Controls, MiniMap, type Edge, type Node, useNodesState } from "reactflow";
import dagre from "@dagrejs/dagre";
import "reactflow/dist/style.css";
import type { NodeMetric, Topology } from "../types";

const NODE_W = 240;
const NODE_H = 176;

type BaseNodeData = {
  id: string;
  name: string;
  kind: string;
  metadata?: Record<string, unknown>;
  label?: ReactNode;
};

function layout(topology: Topology): { nodes: Node<BaseNodeData>[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 60, ranksep: 80 });
  g.setDefaultEdgeLabel(() => ({}));

  g.setNode("START", { width: NODE_W, height: NODE_H });
  g.setNode("__end__", { width: NODE_W, height: NODE_H });
  topology.nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: NODE_H }));

  g.setEdge("START", topology.entry);
  topology.edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  const rfNodes: Node<BaseNodeData>[] = [];
  const pushNode = (id: string, name: string, kind: string, extra?: Partial<Node<BaseNodeData>>) => {
    const p = g.node(id);
    if (!p) return;
    rfNodes.push({
      id,
      position: { x: p.x - NODE_W / 2, y: p.y - NODE_H / 2 },
      data: { id, name, kind, metadata: extra?.data?.metadata },
      style: {
        width: NODE_W,
        height: NODE_H,
        boxSizing: "border-box",
        overflow: "hidden",
        borderRadius: 8,
        border: "1px solid #475569",
        padding: 10,
        background: "#1e293b",
        color: "#f1f5f9",
        fontSize: 13,
      },
      ...extra,
    });
  };

  pushNode("START", "START", "entry", {
    style: {
      width: NODE_W,
      height: NODE_H,
      boxSizing: "border-box",
      overflow: "hidden",
      borderRadius: 40,
      background: "#14532d",
      padding: 10,
      border: "1px solid #16a34a",
      color: "#f1f5f9",
    },
  });
  pushNode("__end__", "END", "exit", {
    style: {
      width: NODE_W,
      height: NODE_H,
      boxSizing: "border-box",
      overflow: "hidden",
      borderRadius: 40,
      background: "#7f1d1d",
      padding: 10,
      border: "1px solid #dc2626",
      color: "#f1f5f9",
    },
  });
  topology.nodes.forEach((n) => {
    const extra: Partial<Node<BaseNodeData>> = {
      data: { id: n.id, name: n.name || n.id, kind: n.kind, metadata: n.metadata },
    };
    if (n.kind === "python_tool") {
      extra.style = {
        width: NODE_W,
        height: NODE_H,
        boxSizing: "border-box",
        overflow: "hidden",
        borderRadius: 8,
        border: "1px solid #0ea5e9",
        padding: 10,
        background: "#0c4a6e",
        color: "#f1f5f9",
        fontSize: 13,
      };
    }
    pushNode(n.id, n.name || n.id, n.kind, extra);
  });

  const rfEdges: Edge[] = [
    { id: "start->entry", source: "START", target: topology.entry, type: "smoothstep" },
    ...topology.edges.map((e, i) => ({
      id: `${e.source}->${e.target}-${i}`,
      source: e.source,
      target: e.target,
      label: e.label,
      type: "smoothstep",
      animated: e.kind === "conditional",
      style: e.kind === "conditional" ? { stroke: "#f59e0b" } : undefined,
    })),
    ...topology.loops.map((lp) => ({
      id: `loop-${lp.loop_id}`,
      source: lp.from,
      target: lp.to,
      label: `loop (max ${lp.max_iterations})`,
      type: "smoothstep",
      style: { stroke: "#ef4444", strokeDasharray: "6 4" },
    })),
  ];

  return { nodes: rfNodes, edges: rfEdges };
}

function metadataLines(kind: string, metadata?: Record<string, unknown>): string[] {
  if (!metadata) return [];
  if (kind === "llm") {
    return [
      [metadata.provider, metadata.model].filter(Boolean).join(" / "),
      metadata.output_state_key ? `out: ${String(metadata.output_state_key)}` : "",
    ].filter(Boolean);
  }
  if (kind === "embedding") {
    return [
      [metadata.provider, metadata.model].filter(Boolean).join(" / "),
      metadata.output_state_key ? `out: ${String(metadata.output_state_key)}` : "",
    ].filter(Boolean);
  }
  if (kind === "tester") {
    return [
      metadata.execution_mode ? `mode: ${String(metadata.execution_mode)}` : "",
      metadata.candidate_state_key ? `candidate: ${String(metadata.candidate_state_key)}` : "",
    ].filter(Boolean);
  }
  if (kind === "retriever") {
    return [
      metadata.corpus_path ? `corpus: ${String(metadata.corpus_path)}` : "",
      metadata.output_state_key ? `out: ${String(metadata.output_state_key)}` : "",
    ].filter(Boolean);
  }
  if (kind === "vector_retriever") {
    return [
      metadata.index_path ? `index: ${String(metadata.index_path)}` : "",
      metadata.output_state_key ? `out: ${String(metadata.output_state_key)}` : "",
    ].filter(Boolean);
  }
  if (kind === "gate") {
    return [`pass: ${String(metadata.pass_target)}`, `fail: ${String(metadata.fail_target)}`];
  }
  if (kind === "router") {
    return [
      metadata.route_state_key ? `route: ${String(metadata.route_state_key)}` : "",
      metadata.routes ? `routes: ${Object.keys(metadata.routes as Record<string, unknown>).join(", ")}` : "",
    ].filter(Boolean);
  }
  if (kind === "approval") {
    return [
      metadata.approval_state_key ? `state: ${String(metadata.approval_state_key)}` : "",
      metadata.approved_target ? `approve: ${String(metadata.approved_target)}` : "",
      metadata.rejected_target ? `reject: ${String(metadata.rejected_target)}` : "",
    ].filter(Boolean);
  }
  if (kind === "subgraph") {
    return [
      metadata.workflow ? `workflow: ${String(metadata.workflow)}` : "",
      metadata.inputs ? `inputs: ${Object.keys(metadata.inputs as Record<string, unknown>).length}` : "",
      metadata.outputs ? `outputs: ${Object.keys(metadata.outputs as Record<string, unknown>).length}` : "",
    ].filter(Boolean);
  }
  if (kind === "python_tool") {
    const inputKeys = metadata.inputs ? Object.keys(metadata.inputs as Record<string, unknown>) : [];
    return [
      metadata.callable_path ? `fn: ${String(metadata.callable_path).split(".").slice(-2).join(".")}` : "",
      inputKeys.length > 0 ? `in: ${inputKeys.join(", ")}` : "",
      metadata.output_state_key ? `out: ${String(metadata.output_state_key)}` : "",
    ].filter(Boolean);
  }
  return [];
}

function renderLabel(name: string, kind: string, metric?: NodeMetric, metadata?: Record<string, unknown>) {
  if (kind === "entry" || kind === "exit") {
    return (
      <div className="flex h-full min-h-0 flex-col items-center justify-center overflow-hidden text-center">
        <div className="truncate text-base font-semibold leading-tight" title={name}>{name}</div>
        <div className="mt-2 truncate text-xs leading-tight text-slate-300">{kind}</div>
      </div>
    );
  }

  const hasData = metric != null && metric.invocations > 0;
  const lines = metadataLines(kind, metadata);
  return (
    <div className="flex h-full min-h-0 flex-col gap-1 overflow-hidden" title={metadata ? JSON.stringify(metadata, null, 2) : undefined}>
      <div
        className="min-h-[32px] overflow-hidden font-semibold leading-tight"
        style={{ display: "-webkit-box", WebkitBoxOrient: "vertical", WebkitLineClamp: 2 }}
        title={name}
      >
        {name}
      </div>
      <div className="flex-none truncate text-xs leading-tight text-slate-400">{kind}</div>
      {lines.length > 0 && (
        <div className="min-h-0 flex-1 overflow-hidden font-mono text-[10px] leading-tight text-slate-400">
          {lines.map((line) => (
            <div key={line} className="truncate">{line}</div>
          ))}
        </div>
      )}
      <div className="mt-auto grid flex-none grid-cols-2 gap-0.5 text-[10px]">
        <Badge label="Fail %" value={hasData ? `${metric!.fail_pct.toFixed(1)}%` : "-"} />
        <Badge label="P95" value={hasData ? `${metric!.p95_latency_ms.toFixed(0)}ms` : "-"} />
        <Badge label="$/run" value={hasData ? `$${metric!.cost_per_run_usd.toFixed(4)}` : "-"} />
        <Badge label="Retries/run" value={hasData ? `${metric!.avg_retries_per_run.toFixed(2)} (${metric!.max_retries_in_run})` : "-"} />
      </div>
    </div>
  );
}

function Badge({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 overflow-hidden rounded border border-slate-600 bg-slate-700 px-1 py-[1px]">
      <div className="truncate text-[10px] leading-tight text-slate-400">{label}</div>
      <div className="truncate text-[10px] font-medium leading-tight text-slate-300">{value}</div>
    </div>
  );
}

export default function GraphView({
  topology,
  nodeMetrics,
  selectedNodeId,
  onSelectNode,
}: {
  topology: Topology;
  nodeMetrics: Record<string, NodeMetric>;
  selectedNodeId?: string;
  onSelectNode?: (nodeId: string) => void;
}) {
  const base = useMemo(() => layout(topology), [topology]);
  const layoutKey = useMemo(
    () => [topology.name, ...base.nodes.map((node) => node.id), ...base.edges.map((edge) => edge.id)].join("|"),
    [base.edges, base.nodes, topology.name]
  );
  const layoutKeyRef = useRef<string>();
  const decoratedNodes = useMemo(
    () =>
      base.nodes.map((n) => {
        const data = n.data as BaseNodeData;
        return {
          ...n,
          selected: n.id === selectedNodeId,
          data: {
            ...data,
            label: renderLabel(data.name, data.kind, nodeMetrics[data.id], data.metadata),
          },
        };
      }),
    [base.nodes, nodeMetrics, selectedNodeId]
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<BaseNodeData>(decoratedNodes);

  useEffect(() => {
    setNodes((currentNodes) => {
      if (layoutKeyRef.current !== layoutKey) {
        layoutKeyRef.current = layoutKey;
        return decoratedNodes;
      }
      const currentById = new Map(currentNodes.map((node) => [node.id, node]));
      return decoratedNodes.map((node) => {
        const current = currentById.get(node.id);
        return current ? { ...node, position: current.position } : node;
      });
    });
  }, [decoratedNodes, layoutKey, setNodes]);

  return (
    <div style={{ width: "100%", height: "100%", background: "#020617" }}>
      <ReactFlow
        nodes={nodes}
        edges={base.edges}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        onNodesChange={onNodesChange}
        panOnDrag
        zoomOnScroll
        onNodeClick={(_event, node) => onSelectNode?.(node.id)}
      >
        <MiniMap
          style={{ background: "#0f172a" }}
          nodeColor="#475569"
          maskColor="rgba(2,6,23,0.8)"
        />
        <Controls />
        <Background color="#334155" gap={16} />
      </ReactFlow>
    </div>
  );
}
