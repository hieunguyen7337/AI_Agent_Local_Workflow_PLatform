"""WebSocket live updates over local run artifacts."""
from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException, WebSocket

from backend.runtime.artifacts import resolve_run_dir_any
from backend.server.node_metrics import compute_node_metrics
from backend.server.routes import list_runs_data, load_workflow


@dataclass
class _ClientState:
    client_id: str
    websocket: WebSocket
    workflow: str | None = None
    node_ids: set[str] = field(default_factory=set)
    run_fingerprints: dict[str, str] = field(default_factory=dict)
    run_statuses: dict[str, str] = field(default_factory=dict)
    run_detail_fingerprints: dict[str, tuple[int, int]] = field(default_factory=dict)
    workflow_metrics_fingerprint: str | None = None
    last_heartbeat_ns: int = 0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LiveUpdateService:
    def __init__(
        self,
        *,
        runs_root: Path,
        poll_interval_s: float = 1.0,
        heartbeat_interval_s: float = 15.0,
        node_metrics_limit: int = 50,
    ):
        self.runs_root = runs_root
        self.poll_interval_s = poll_interval_s
        self.heartbeat_interval_s = heartbeat_interval_s
        self.node_metrics_limit = node_metrics_limit
        self._clients: dict[str, _ClientState] = {}
        self._clients_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._poll_loop(), name="live-update-poller")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        async with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                await client.websocket.close()
            except Exception:
                pass

    async def register(self, websocket: WebSocket) -> str:
        await websocket.accept()
        client_id = f"c_{uuid.uuid4().hex[:12]}"
        state = _ClientState(client_id=client_id, websocket=websocket)
        async with self._clients_lock:
            self._clients[client_id] = state
        await self.send_heartbeat(client_id, reason="connected")
        return client_id

    async def unregister(self, client_id: str) -> None:
        async with self._clients_lock:
            self._clients.pop(client_id, None)

    async def subscribe(self, client_id: str, workflow: str) -> bool:
        workflow = workflow.strip()
        if not workflow:
            return False
        try:
            metadata = load_workflow(workflow)
        except HTTPException:
            return False

        async with self._clients_lock:
            client = self._clients.get(client_id)
            if client is None:
                return False
            client.workflow = workflow
            client.node_ids = set(metadata.nodes.keys())
            client.run_fingerprints.clear()
            client.run_statuses.clear()
            client.run_detail_fingerprints.clear()
            client.workflow_metrics_fingerprint = None

        await self.send_heartbeat(client_id, reason="subscribed")
        return True

    async def send_heartbeat(self, client_id: str, *, reason: str = "heartbeat") -> None:
        async with self._clients_lock:
            client = self._clients.get(client_id)
        if client is None:
            return
        ok = await self._send_event(
            client,
            self._event(
                event_type="heartbeat",
                workflow=client.workflow or "",
                data={"reason": reason, "subscribed_workflow": client.workflow},
            ),
        )
        if ok:
            client.last_heartbeat_ns = time.time_ns()

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.sleep(self.poll_interval_s)
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep polling for best-effort local live updates.
                continue

    async def _poll_once(self) -> None:
        async with self._clients_lock:
            clients = list(self._clients.values())
        if not clients:
            return

        runs = list_runs_data(runs_root=None, limit=500)
        now_ns = time.time_ns()

        for client in clients:
            if not client.workflow:
                await self._emit_heartbeat_if_due(client, now_ns=now_ns)
                continue

            workflow_runs = [r for r in runs if r.get("graph_name") == client.workflow]
            await self._emit_run_events(client, workflow_runs=workflow_runs)
            await self._emit_node_metrics_if_needed(client, workflow_runs=workflow_runs)
            await self._emit_heartbeat_if_due(client, now_ns=now_ns)

    async def _emit_run_events(self, client: _ClientState, *, workflow_runs: list[dict[str, Any]]) -> None:
        next_run_fingerprints: dict[str, str] = {}
        next_run_statuses: dict[str, str] = {}
        next_detail_fingerprints: dict[str, tuple[int, int]] = {}

        for run in workflow_runs:
            run_id = str(run.get("run_id") or "")
            if not run_id:
                continue
            current_status = str(run.get("status") or "")
            summary_fingerprint = self._summary_fingerprint(run)
            detail_fingerprint = self._run_detail_fingerprint(run_id)

            next_run_fingerprints[run_id] = summary_fingerprint
            next_run_statuses[run_id] = current_status
            if detail_fingerprint is not None:
                next_detail_fingerprints[run_id] = detail_fingerprint

            prev_summary = client.run_fingerprints.get(run_id)
            prev_status = client.run_statuses.get(run_id)
            prev_detail = client.run_detail_fingerprints.get(run_id)

            event_type: str | None = None
            if prev_summary is None:
                event_type = "run_started"
            elif prev_summary != summary_fingerprint:
                if prev_status == "running" and current_status != "running":
                    event_type = "run_finished"
                else:
                    event_type = "run_updated"
            elif detail_fingerprint is not None and prev_detail != detail_fingerprint:
                event_type = "run_updated"

            if event_type is None:
                continue

            detail_payload = None
            if detail_fingerprint is not None:
                detail_payload = {
                    "span_count": detail_fingerprint[0],
                    "latest_span_start_ns": detail_fingerprint[1],
                }

            ok = await self._send_event(
                client,
                self._event(
                    event_type=event_type,
                    workflow=client.workflow or "",
                    run_id=run_id,
                    data={"run": run, "detail": detail_payload},
                ),
            )
            if not ok:
                return

        client.run_fingerprints = next_run_fingerprints
        client.run_statuses = next_run_statuses
        client.run_detail_fingerprints = next_detail_fingerprints

    async def _emit_node_metrics_if_needed(
        self, client: _ClientState, *, workflow_runs: list[dict[str, Any]]
    ) -> None:
        workflow_fp = self._workflow_fingerprint(workflow_runs)
        if workflow_fp == client.workflow_metrics_fingerprint:
            return
        if not client.workflow:
            return

        metrics = compute_node_metrics(
            workflow=client.workflow,
            node_ids=client.node_ids,
            limit=self.node_metrics_limit,
        )
        runs_considered = max((m.get("runs_considered", 0) for m in metrics.values()), default=0)
        ok = await self._send_event(
            client,
            self._event(
                event_type="node_metrics_updated",
                workflow=client.workflow,
                data={
                    "workflow": client.workflow,
                    "limit": self.node_metrics_limit,
                    "runs_considered": runs_considered,
                    "metrics": metrics,
                },
            ),
        )
        if ok:
            client.workflow_metrics_fingerprint = workflow_fp

    async def _emit_heartbeat_if_due(self, client: _ClientState, *, now_ns: int) -> None:
        interval_ns = int(self.heartbeat_interval_s * 1_000_000_000)
        if interval_ns <= 0:
            interval_ns = 1
        if now_ns - client.last_heartbeat_ns < interval_ns:
            return
        ok = await self._send_event(
            client,
            self._event(
                event_type="heartbeat",
                workflow=client.workflow or "",
                data={"reason": "heartbeat", "subscribed_workflow": client.workflow},
            ),
        )
        if ok:
            client.last_heartbeat_ns = now_ns

    async def _send_event(self, client: _ClientState, event: dict[str, Any]) -> bool:
        try:
            async with client.send_lock:
                await client.websocket.send_json(event)
            return True
        except Exception:
            await self.unregister(client.client_id)
            return False

    @staticmethod
    def _event(
        *,
        event_type: str,
        workflow: str,
        run_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": event_type,
            "workflow": workflow,
            "run_id": run_id,
            "emitted_at_ns": time.time_ns(),
            "data": data or {},
        }

    @staticmethod
    def _summary_fingerprint(run: dict[str, Any]) -> str:
        return "|".join(
            [
                str(run.get("status")),
                str(run.get("ended_ns")),
                str(run.get("cost_usd")),
                str(run.get("latency_ms")),
                str(run.get("error")),
            ]
        )

    @staticmethod
    def _workflow_fingerprint(workflow_runs: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for run in workflow_runs:
            run_id = str(run.get("run_id") or "")
            if not run_id:
                continue
            parts.append(f"{run_id}:{LiveUpdateService._summary_fingerprint(run)}")
        parts.sort()
        return "||".join(parts)

    def _run_detail_fingerprint(self, run_id: str) -> tuple[int, int] | None:
        try:
            run_dir = resolve_run_dir_any(run_id)[0]
        except FileNotFoundError:
            return None
        db_path = run_dir / "telemetry.db"
        if not db_path.exists():
            return None
        try:
            with sqlite3.connect(db_path) as con:
                row = con.execute(
                    "SELECT COUNT(*), COALESCE(MAX(start_ns), 0) FROM spans WHERE run_id=?",
                    (run_id,),
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        if not row:
            return (0, 0)
        return (int(row[0] or 0), int(row[1] or 0))
