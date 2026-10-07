"""
ASR Node — faster-whisper INT8 server on port 9520.
Start: python -m harness.nodes.asr.adapter
Reference: github.com/SYSTRAN/faster-whisper
"""
import asyncio
import io
import logging
import os
import struct
import time
from contextlib import asynccontextmanager

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

log = logging.getLogger("asr")

_model = None
_model_name = os.environ.get("ASR_MODEL", "large-v3")
_device = os.environ.get("ASR_DEVICE", "cpu")
_compute = os.environ.get("ASR_COMPUTE_TYPE", "int8")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model
    from faster_whisper import WhisperModel
    log.info("Loading faster-whisper %s on %s/%s …", _model_name, _device, _compute)
    _model = WhisperModel(_model_name, device=_device, compute_type=_compute)
    log.info("ASR ready")
    yield
    _model = None


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "up" if _model else "loading", "model": _model_name}


def _pcm_bytes_to_float32(raw: bytes, sample_rate: int = 16000) -> np.ndarray:
    """Convert raw 16-bit LE PCM bytes to float32 numpy array."""
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return samples


def _transcribe(audio: np.ndarray) -> str:
    segments, _ = _model.transcribe(
        audio,
        language="en",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
    )
    return " ".join(s.text.strip() for s in segments)


@app.websocket("/ws/asr")
async def ws_asr(ws: WebSocket):
    """
    Receives raw 16kHz mono 16-bit PCM chunks from the client mic.
    Returns JSON transcript segments as they accumulate.
    Protocol:
      client → binary PCM chunks (any size)
      server → {type: "partial", text: str} | {type: "final", text: str}
    """
    await ws.accept()
    buffer = bytearray()
    CHUNK_SAMPLES = 16000 * 2  # 2 seconds of audio to trigger partial
    try:
        while True:
            data = await ws.receive_bytes()
            buffer.extend(data)
            if len(buffer) >= CHUNK_SAMPLES * 2:  # 2 bytes per sample
                audio = _pcm_bytes_to_float32(bytes(buffer))
                buffer.clear()
                loop = asyncio.get_event_loop()
                text = await loop.run_in_executor(None, _transcribe, audio)
                if text.strip():
                    await ws.send_json({"type": "partial", "text": text.strip()})
    except WebSocketDisconnect:
        if buffer:
            audio = _pcm_bytes_to_float32(bytes(buffer))
            loop = asyncio.get_event_loop()
            text = await loop.run_in_executor(None, _transcribe, audio)
            if text.strip():
                await ws.send_json({"type": "final", "text": text.strip()})


@app.post("/transcribe")
async def transcribe_endpoint(body: dict):
    """
    Synchronous transcription for a base64-encoded PCM chunk.
    Used by the avatar-chain to transcribe a buffered audio segment.
    body: {audio_b64: str, sample_rate: int}
    """
    import base64
    raw = base64.b64decode(body["audio_b64"])
    audio = _pcm_bytes_to_float32(raw, body.get("sample_rate", 16000))
    t0 = time.perf_counter()
    loop = asyncio.get_event_loop()
    text = await loop.run_in_executor(None, _transcribe, audio)
    latency_ms = round((time.perf_counter() - t0) * 1000)
    return {"text": text.strip(), "latency_ms": latency_ms}


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("harness.nodes.asr.adapter:app", host="0.0.0.0", port=9520, reload=False)
