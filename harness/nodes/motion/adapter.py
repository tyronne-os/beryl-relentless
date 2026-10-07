"""
Motion Node — audio → motion latents server on port 8022.
Primary: JoyVASA (2411.09209) — open VASA-style, decoupled facial representation.
Fallback: FLOAT (2412.01064) — flow matching in motion latent space.
Last resort: css_labels — pass JEV performance dict as-is to client CSS animator.

Start: python -m harness.nodes.motion.adapter

Reference implementations:
  JoyVASA: github.com/jdh-algo/JoyVASA
  FLOAT:   github.com/deepbrainai-research/float
"""
import asyncio
import base64
import io
import logging
import os
import time
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

log = logging.getLogger("motion")

MODEL_SLOT = os.environ.get("MOTION_MODEL", "joyvasa")  # joyvasa | float | css_labels
DEVICE = os.environ.get("MOTION_DEVICE", "cpu")
PORT = int(os.environ.get("MOTION_PORT", "8022"))

_model = None
_model_ready = False


def _load_joyvasa():
    """Load JoyVASA model. Requires: pip install joyvasa (or clone + pip install -e .)"""
    try:
        from joyvasa.pipeline import JoyVASAPipeline
        model = JoyVASAPipeline(device=DEVICE)
        log.info("JoyVASA loaded on %s", DEVICE)
        return model
    except ImportError:
        log.warning("JoyVASA not installed — falling back to FLOAT")
        return None


def _load_float():
    """Load FLOAT model. Requires: pip install float-motion (or clone)"""
    try:
        from float_motion.pipeline import FLOATPipeline
        model = FLOATPipeline(device=DEVICE)
        log.info("FLOAT loaded on %s", DEVICE)
        return model
    except ImportError:
        log.warning("FLOAT not installed — falling back to css_labels passthrough")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _model_ready
    if MODEL_SLOT == "joyvasa":
        _model = _load_joyvasa() or _load_float()
    elif MODEL_SLOT == "float":
        _model = _load_float()
    else:
        _model = None  # css_labels passthrough

    _model_ready = True
    log.info("Motion node ready — slot: %s, model: %s", MODEL_SLOT, type(_model).__name__ if _model else "css_passthrough")
    yield
    _model = None
    _model_ready = False


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "status": "up" if _model_ready else "loading",
        "slot": MODEL_SLOT,
        "model": type(_model).__name__ if _model else "css_passthrough",
        "device": DEVICE,
    }


def _audio_to_latents(audio_bytes: bytes, jev_director: dict | None = None) -> dict:
    """
    Convert audio bytes to motion latents.
    Returns: {latents: list[float], fps: float, frame_count: int}

    If no model loaded, returns css_labels passthrough from jev_director.
    """
    if _model is None:
        return {
            "mode": "css_passthrough",
            "jev_labels": jev_director or {},
            "latents": [],
            "fps": 0,
            "frame_count": 0,
        }

    audio_np = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0

    # JEV·DIRECTOR conditioning — inject into motion generation if supported
    conditioning = {}
    if jev_director:
        conditioning = {
            "gaze": jev_director.get("gaze", "hold"),
            "blink_rate": jev_director.get("blink_rate", 0.3),
            "nod": jev_director.get("nod", 0.0),
            "micro_expression": jev_director.get("micro_expression", "none"),
            "intensity": jev_director.get("intensity", 0.5),
        }

    t0 = time.perf_counter()
    result = _model.generate(audio_np, conditioning=conditioning)
    latency_ms = round((time.perf_counter() - t0) * 1000)

    latents = result.latents.flatten().tolist() if hasattr(result, "latents") else []
    fps = getattr(result, "fps", 25.0)
    frames = getattr(result, "frame_count", len(latents) // 512)

    log.info("Motion latents: %d values, %.1f fps, %dms", len(latents), fps, latency_ms)
    return {"mode": MODEL_SLOT, "latents": latents, "fps": fps, "frame_count": frames,
            "latency_ms": latency_ms}


@app.post("/generate")
async def generate_endpoint(body: dict):
    """
    POST {audio_b64: str, jev_director: dict | null}
    Returns motion latents for streaming to client WebGPU renderer.
    """
    raw = base64.b64decode(body["audio_b64"])
    director = body.get("jev_director")
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _audio_to_latents, raw, director)
    return result


@app.websocket("/ws/motion")
async def ws_motion(ws: WebSocket):
    """
    Streaming motion latents WebSocket.
    Client sends: {type: "audio_chunk", audio_b64: str, jev_director: dict}
    Server sends: {type: "latents", data: list[float], fps: float} per chunk
    """
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") != "audio_chunk":
                continue
            raw = base64.b64decode(msg["audio_b64"])
            director = msg.get("jev_director")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, _audio_to_latents, raw, director)
            await ws.send_json({"type": "latents", **result})
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("harness.nodes.motion.adapter:app", host="0.0.0.0", port=PORT, reload=False)
