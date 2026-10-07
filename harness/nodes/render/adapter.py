"""
RENDER node — latent-to-frame renderer.
L1: forwards latents to client WebGPU warp renderer via WebSocket stream.
L2: calls server-side diffusion student (FlashHead/LeapTalk/AvatarForcing) on GPU.

Client receives:
  {type: "latents", data: list[float], fps: int, frame_count: int}  -- L1 (kb/s, not video)
  {type: "frame", data: str (base64 jpg), width: int, height: int}  -- L2 (server render)
"""
import asyncio
import base64
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

log = logging.getLogger("node.render")

_GPU_RENDER_URL = os.environ.get("GPU_RENDER_URL", "http://localhost:9523/render")
_STAGE = os.environ.get("RENDER_STAGE", "L1")
_TIMEOUT = float(os.environ.get("RENDER_TIMEOUT_S", "5.0"))

_client = httpx.AsyncClient(timeout=_TIMEOUT)

# Connected WebSocket clients (client WebGPU renderer connections)
_clients: set[WebSocket] = set()


async def broadcast_latents(latents: list, fps: int = 25, frame_count: int = 1):
    """Push latent vector to all connected client renderers."""
    msg = {"type": "latents", "data": latents, "fps": fps, "frame_count": frame_count}
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


async def render_l2(latents: list, width: int = 512, height: int = 512) -> bytes | None:
    """
    Call GPU render service (FlashHead/LeapTalk/AvatarForcing).
    Returns JPEG bytes or None on failure.
    """
    try:
        resp = await _client.post(_GPU_RENDER_URL, json={
            "latents": latents,
            "width": width,
            "height": height,
        })
        resp.raise_for_status()
        data = resp.json()
        frame_b64 = data.get("frame_b64", "")
        return base64.b64decode(frame_b64) if frame_b64 else None
    except Exception as exc:
        log.warning("L2 render failed (%s) — falling back to L1 latent stream", exc)
        return None


async def render_frame(latents: list, stage: str | None = None) -> dict:
    """
    Render one frame. Returns a dict the WS handler sends to the client.
    stage overrides the module-level _STAGE.
    """
    effective_stage = stage or _STAGE
    if effective_stage == "L2":
        frame_bytes = await render_l2(latents)
        if frame_bytes:
            return {
                "type": "frame",
                "data": base64.b64encode(frame_bytes).decode(),
                "width": 512,
                "height": 512,
            }
    # L1 or L2 fallback: send raw latents
    return {"type": "latents", "data": latents, "fps": 25, "frame_count": 1}


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("RENDER node ready (stage=%s)", _STAGE)
    yield
    await _client.aclose()


app = FastAPI(title="render-node", lifespan=lifespan)


@app.websocket("/ws/render-client")
async def render_client_ws(ws: WebSocket):
    """
    Client WebGPU renderer connects here to receive latents/frames.
    """
    await ws.accept()
    _clients.add(ws)
    log.info("Render client connected (%d total)", len(_clients))
    try:
        while True:
            # Keep connection alive; server pushes — client just listens
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        _clients.discard(ws)
        log.info("Render client disconnected (%d remaining)", len(_clients))


@app.websocket("/ws/render-ingest")
async def render_ingest_ws(ws: WebSocket):
    """
    Motion node sends latents here; render node fans out to connected clients.
    """
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            latents = msg.get("latents", [])
            fps = msg.get("fps", 25)
            frame_count = msg.get("frame_count", 1)
            stage = msg.get("stage", _STAGE)

            result = await render_frame(latents, stage=stage)
            await broadcast_latents(
                result.get("data", latents),
                fps=fps,
                frame_count=frame_count,
            ) if result.get("type") == "latents" else await _broadcast_frame(result)
    except WebSocketDisconnect:
        pass


async def _broadcast_frame(msg: dict):
    dead = set()
    for ws in list(_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


@app.post("/render")
async def render_endpoint(body: dict):
    """POST {latents, stage?} → {type, data, ...}"""
    latents = body.get("latents", [])
    stage = body.get("stage", _STAGE)
    return await render_frame(latents, stage=stage)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "node": "render",
        "stage": _STAGE,
        "connected_clients": len(_clients),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9524, log_level="info")
