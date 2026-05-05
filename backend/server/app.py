"""FastAPI app entry. Bound to 127.0.0.1, no auth (local-first)."""
from __future__ import annotations

from contextlib import asynccontextmanager

import backend.runtime.artifacts as run_artifacts
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .live import LiveUpdateService
from .routes import router

live_service = LiveUpdateService(runs_root=run_artifacts.RUNS_ROOT)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    await live_service.start()
    try:
        yield
    finally:
        await live_service.stop()


app = FastAPI(title="Workflow Platform", version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    client_id = await live_service.register(websocket)
    try:
        while True:
            payload = await websocket.receive_json()
            if not isinstance(payload, dict):
                await live_service.send_heartbeat(client_id, reason="invalid_message")
                continue
            action = str(payload.get("action") or "")
            if action != "subscribe":
                await live_service.send_heartbeat(client_id, reason="unknown_action")
                continue
            workflow = str(payload.get("workflow") or "")
            ok = await live_service.subscribe(client_id, workflow)
            if not ok:
                await live_service.send_heartbeat(client_id, reason="invalid_workflow")
    except WebSocketDisconnect:
        pass
    finally:
        await live_service.unregister(client_id)


@app.get("/")
def root() -> dict:
    return {"service": "workflow-platform", "status": "ok"}
