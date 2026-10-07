"""
LISTEN node — Duplug 0.6B INT8 listener state classifier.
Runs in parallel to the ASR/LLM/TTS chain.
Output: {state: speaking|listening|silent|backchannel, energy_rms, turn_confidence}
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

log = logging.getLogger("node.listen")

_DUPLUG_URL = os.environ.get("DUPLUG_API_URL", "http://localhost:9521/classify")
_TIMEOUT = float(os.environ.get("LISTEN_TIMEOUT_S", "0.5"))

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_STATES = ("speaking", "listening", "silent", "backchannel")


def _energy_rms(pcm_bytes: bytes) -> float:
    import struct
    n = len(pcm_bytes) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"{n}h", pcm_bytes[:n * 2])
    rms = (sum(s * s for s in samples) / n) ** 0.5
    return round(rms / 32768.0, 4)


def _heuristic_state(energy_rms: float, is_user_turn: bool) -> dict:
    if energy_rms > 0.05:
        state = "speaking" if is_user_turn else "backchannel"
    elif energy_rms > 0.01:
        state = "listening"
    else:
        state = "silent"
    return {
        "state": state,
        "energy_rms": energy_rms,
        "turn_confidence": 0.4,
        "source": "heuristic",
    }


async def classify_chunk(pcm_bytes: bytes, is_user_turn: bool = True) -> dict:
    """
    Classify listener state from a PCM audio chunk.
    Falls back to energy heuristic if Duplug is unavailable.
    """
    energy = _energy_rms(pcm_bytes)
    try:
        import base64
        payload = {
            "audio_b64": base64.b64encode(pcm_bytes).decode(),
            "sample_rate": 16000,
            "is_user_turn": is_user_turn,
        }
        resp = await _client.post(_DUPLUG_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return {
            "state": data.get("state", "silent"),
            "energy_rms": energy,
            "turn_confidence": data.get("confidence", 0.5),
            "source": "duplug",
        }
    except Exception as exc:
        log.debug("Duplug unavailable (%s) — heuristic fallback", exc)
        return _heuristic_state(energy, is_user_turn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("LISTEN node ready (Duplug fallback: energy heuristic)")
    yield
    await _client.aclose()


app = FastAPI(title="listen-node", lifespan=lifespan)


@app.post("/classify")
async def classify_endpoint(body: dict):
    """
    body: {audio_b64: str, sample_rate: int, is_user_turn: bool}
    """
    import base64
    pcm = base64.b64decode(body.get("audio_b64", ""))
    is_user = body.get("is_user_turn", True)
    return await classify_chunk(pcm, is_user)


@app.websocket("/ws/listen")
async def listen_ws(ws: WebSocket):
    """
    Receives raw PCM chunks, emits listener state JSON per chunk.
    Runs in parallel to the main ASR chain.
    """
    await ws.accept()
    try:
        while True:
            data = await ws.receive_bytes()
            result = await classify_chunk(data, is_user_turn=True)
            await ws.send_json(result)
    except WebSocketDisconnect:
        pass


@app.get("/health")
async def health():
    return {"status": "ok", "node": "listen", "model": "duplug-0.6b-int8"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=9521, log_level="info")
