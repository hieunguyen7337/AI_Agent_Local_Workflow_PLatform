import { type ReactNode, useMemo } from "react";
import ReactFlow, { Background, Controls, MiniMap, type Edge, type Node } from "reactflow";
import dagre from "@dagrejs/dagre";
import "reactflow/dist/style.css";
import type { NodeMetric, Topology } from "../types";

const NODE_W = 220;
const NODE_H = 148;

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
        borderRadius: 8,
        border: "1px solid #cbd5e1",
        padding: 10,
        background: "white",
        fontSize: 13,
      },
      ...extra,
    });
  };

  pushNode("START", "START", "entry", {
    style: {
      width: NODE_W,
      height: NODE_H,
      borderRadius: 40,
      background: "#dcfce7",
      padding: 10,
      border: "1px solid #86efac",
    },
  });
  pushNode("__end__", "END", "exit", {
    style: {
      width: NODE_W,
      height: NODE_H,
      borderRadius: 40,
      background: "#fee2e2",
      padding: 10,
      border: "1px solid #fca5a5",
    },
  });
  topology.nodes.forEach((n) =>
    pushNode(n.id, n.name || n.id, n.kind, {
      data: { id: n.id, name: n.name || n.id, kind: n.kind, metadata: n.metadata },
    })
  );

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
  return [];
}

function renderLabel(name: string, kind: string, metric?: NodeMetric, metadata?: Record<string, unknown>) {
  const hasData = metric != null && metric.invocations > 0;
  const lines = metadataLines(kind, metadata);
  return (
    <div className="space-y-1" title={metadata ? JSON.stringify(metadata, null, 2) : undefined}>
      <div className="font-semibold leading-tight">{name}</div>
      <div className="text-xs text-gray-500">{kind}</div>
      {lines.length > 0 && (
        <div className="text-[10px] leading-tight text-slate-600 font-mono truncate">
          {lines.map((line) => (
            <div key={line} className="truncate">{line}</div>
          ))}
        </div>
      )}
      <div className="grid grid-cols-2 gap-1 text-[11px] mt-1">
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
    <div className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5">
      <div className="text-[10px] text-slate-500 leading-none">{label}</div>
      <div className="text-[11px] font-medium text-slate-700 leading-tight">{value}</div>
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

  return (
    <div style={{ width: "100%", height: "100%" }}>
      <ReactFlow
        nodes={decoratedNodes}
        edges={base.edges}
        fitView
        onNodeClick={(_event, node) => onSelectNode?.(node.id)}
      >
        <MiniMap />
        <Controls />
        <Background />
      </ReactFlow>
    </div>
  );
}
