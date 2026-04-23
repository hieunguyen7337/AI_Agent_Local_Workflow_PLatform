import { type ReactNode, useMemo } from "react";
import ReactFlow, { Background, Controls, MiniMap, type Edge, type Node } from "reactflow";
import dagre from "@dagrejs/dagre";
import "reactflow/dist/style.css";
import type { NodeMetric, Topology } from "../types";

const NODE_W = 220;
const NODE_H = 112;

type BaseNodeData = {
  id: string;
  name: string;
  kind: string;
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
      data: { id, name, kind },
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
  topology.nodes.forEach((n) => pushNode(n.id, n.name || n.id, n.kind));

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

function renderLabel(name: string, kind: string, metric?: NodeMetric) {
  const hasData = metric != null && metric.invocations > 0;
  return (
    <div className="space-y-1">
      <div className="font-semibold leading-tight">{name}</div>
      <div className="text-xs text-gray-500">{kind}</div>
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
}: {
  topology: Topology;
  nodeMetrics: Record<string, NodeMetric>;
}) {
  const base = useMemo(() => layout(topology), [topology]);
  const decoratedNodes = useMemo(
    () =>
      base.nodes.map((n) => {
        const data = n.data as BaseNodeData;
        return {
          ...n,
          data: {
            ...data,
            label: renderLabel(data.name, data.kind, nodeMetrics[data.id]),
          },
        };
      }),
    [base.nodes, nodeMetrics]
  );

  return (
    <div style={{ width: "100%", height: "100%" }}>
      <ReactFlow nodes={decoratedNodes} edges={base.edges} fitView>
        <MiniMap />
        <Controls />
        <Background />
      </ReactFlow>
    </div>
  );
}
