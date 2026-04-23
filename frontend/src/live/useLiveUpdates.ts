import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { NodeMetricsResponse, RunSummary } from "../types";

type LiveEventType = "run_started" | "run_updated" | "run_finished" | "node_metrics_updated" | "heartbeat";

type LiveEventEnvelope = {
  type: LiveEventType;
  workflow: string;
  run_id?: string | null;
  emitted_at_ns: number;
  data: Record<string, unknown>;
};

const MAX_RECONNECT_BACKOFF_MS = 8000;

function liveWsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}/ws/live`;
}

function upsertRun(runs: RunSummary[] | undefined, nextRun: RunSummary): RunSummary[] {
  const out = [...(runs ?? [])];
  const idx = out.findIndex((r) => r.run_id === nextRun.run_id);
  if (idx >= 0) {
    out[idx] = nextRun;
  } else {
    out.push(nextRun);
  }
  out.sort((a, b) => (b.started_ns ?? 0) - (a.started_ns ?? 0));
  return out;
}

export function useLiveUpdates(workflow: string, selectedRunId?: string) {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const closedRef = useRef(false);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const reconnectAttemptRef = useRef(0);
  const generationRef = useRef(0);
  const hydratedRef = useRef(false);
  const workflowRef = useRef(workflow);
  const selectedRunRef = useRef<string | undefined>(selectedRunId);

  useEffect(() => {
    workflowRef.current = workflow;
  }, [workflow]);

  useEffect(() => {
    selectedRunRef.current = selectedRunId;
  }, [selectedRunId]);

  useEffect(() => {
    closedRef.current = false;

    const subscribeAndResync = async (ws: WebSocket, generation: number, wf: string) => {
      if (ws.readyState !== WebSocket.OPEN) return;
      hydratedRef.current = false;
      ws.send(JSON.stringify({ action: "subscribe", workflow: wf }));
      const selected = selectedRunRef.current;
      await Promise.all([
        queryClient.refetchQueries({ queryKey: ["runs"], type: "active" }),
        queryClient.refetchQueries({ queryKey: ["node-metrics", wf], type: "active" }),
        selected
          ? queryClient.refetchQueries({
              queryKey: ["run", selected],
              type: "active",
            })
          : Promise.resolve(),
      ]);
      if (generation !== generationRef.current) return;
      hydratedRef.current = true;
    };

    const onMessage = (ev: MessageEvent<string>) => {
      let parsed: LiveEventEnvelope;
      try {
        parsed = JSON.parse(ev.data) as LiveEventEnvelope;
      } catch {
        return;
      }
      if (!hydratedRef.current) return;
      if (parsed.type === "heartbeat") return;

      if (parsed.type === "node_metrics_updated") {
        const payload = parsed.data as unknown as NodeMetricsResponse;
        if (!payload || payload.workflow !== workflowRef.current) return;
        queryClient.setQueryData(["node-metrics", payload.workflow], payload);
        return;
      }

      const run = (parsed.data as { run?: RunSummary }).run;
      if (!run) return;
      if (run.graph_name !== workflowRef.current) return;

      queryClient.setQueryData(["runs"], (current: RunSummary[] | undefined) => upsertRun(current, run));

      const selected = selectedRunRef.current;
      const runId = parsed.run_id ?? run.run_id;
      if (selected && runId === selected) {
        void queryClient.invalidateQueries({ queryKey: ["run", selected] });
      }
    };

    const scheduleReconnect = () => {
      if (closedRef.current) return;
      const attempt = reconnectAttemptRef.current++;
      const backoff = Math.min(1000 * 2 ** attempt, MAX_RECONNECT_BACKOFF_MS);
      reconnectTimerRef.current = window.setTimeout(connect, backoff);
    };

    const connect = () => {
      if (closedRef.current) return;
      const ws = new WebSocket(liveWsUrl());
      wsRef.current = ws;
      const generation = ++generationRef.current;

      ws.onopen = () => {
        reconnectAttemptRef.current = 0;
        void subscribeAndResync(ws, generation, workflowRef.current);
      };

      ws.onmessage = onMessage;
      ws.onerror = () => ws.close();
      ws.onclose = () => {
        wsRef.current = null;
        hydratedRef.current = false;
        scheduleReconnect();
      };
    };

    connect();

    return () => {
      closedRef.current = true;
      hydratedRef.current = false;
      if (reconnectTimerRef.current != null) {
        window.clearTimeout(reconnectTimerRef.current);
      }
      if (wsRef.current && wsRef.current.readyState < WebSocket.CLOSING) {
        wsRef.current.close();
      }
      wsRef.current = null;
    };
  }, [queryClient]);

  useEffect(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const generation = ++generationRef.current;
    hydratedRef.current = false;
    ws.send(JSON.stringify({ action: "subscribe", workflow }));
    const selected = selectedRunRef.current;
    void Promise.all([
      queryClient.refetchQueries({ queryKey: ["runs"], type: "active" }),
      queryClient.refetchQueries({ queryKey: ["node-metrics", workflow], type: "active" }),
      selected
        ? queryClient.refetchQueries({
            queryKey: ["run", selected],
            type: "active",
          })
        : Promise.resolve(),
    ]).finally(() => {
      if (generation !== generationRef.current) return;
      hydratedRef.current = true;
    });
  }, [queryClient, workflow]);
}
