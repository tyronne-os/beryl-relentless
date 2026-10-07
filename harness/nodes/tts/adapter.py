"""
TTS Node — server-side Kokoro caller.
Patches into _safe_chat so TTS fires BEFORE the "done" signal,
eliminating the client round-trip gap.

Usage: import and call tts_for_reply() inside _safe_chat after
jev.performance/proprioception and before sock.release().
"""
import asyncio
import base64
import logging
import os
import time

import httpx

from harness.nodes.tts.voices import FEMALE_VOICES, BY_ID, DEFAULT_VOICE

log = logging.getLogger("tts")

KOKORO_URL = os.environ.get("KOKORO_URL", "http://localhost:8012")
SPEACHES_URL = os.environ.get("SPEACHES_URL", "http://localhost:8013")
VOICE = os.environ.get("TTS_VOICE", DEFAULT_VOICE)
TTS_TIMEOUT = float(os.environ.get("TTS_TIMEOUT_S", "4.0"))

_client = httpx.AsyncClient(timeout=TTS_TIMEOUT)


async def tts_for_reply(ws, reply: str, rate: float = 1.0, pitch: float = 0.0) -> bool:
    """
    Call Kokoro TTS server-side, stream audio chunks to the WebSocket.
    Falls back to speaches if Kokoro is down.
    Returns True if audio was sent, False on failure (caller degrades gracefully).

    WebSocket events emitted:
      {type: "tts_start"}
      {type: "tts_chunk", audio_b64: str, sample_rate: 24000}  (repeated)
      {type: "tts_done"}
    """
    text = reply.strip()
    if not text:
        return False

    for url, label in [(KOKORO_URL, "kokoro"), (SPEACHES_URL, "speaches")]:
        try:
            t0 = time.perf_counter()
            resp = await _client.post(
                f"{url}/v1/audio/speech",
                json={"model": "kokoro", "input": text, "voice": VOICE,
                      "response_format": "pcm", "speed": rate},
            )
            if resp.status_code != 200:
                log.warning("TTS %s returned %s", label, resp.status_code)
                continue

            audio_bytes = resp.content
            latency_ms = round((time.perf_counter() - t0) * 1000)
            log.info("TTS %s: %d bytes in %dms", label, len(audio_bytes), latency_ms)

            await ws.send_json({"type": "tts_start", "latency_ms": latency_ms})

            # stream in 8 KB chunks so client can begin playback immediately
            CHUNK = 8192
            for i in range(0, len(audio_bytes), CHUNK):
                chunk = audio_bytes[i:i + CHUNK]
                await ws.send_json({
                    "type": "tts_chunk",
                    "audio_b64": base64.b64encode(chunk).decode(),
                    "sample_rate": 24000,
                })

            await ws.send_json({"type": "tts_done"})
            return True

        except Exception as exc:
            log.warning("TTS %s failed: %s", label, exc)

    log.error("All TTS backends failed — client will handle TTS")
    return False


async def health() -> dict:
    results = {}
    for url, label in [(KOKORO_URL, "kokoro"), (SPEACHES_URL, "speaches")]:
        try:
            r = await _client.get(f"{url}/health", timeout=1.5)
            results[label] = "up" if r.status_code < 400 else "down"
        except Exception:
            results[label] = "down"
    return results


def list_voices() -> list[dict]:
    """Return all female voice metadata (no I/O)."""
    return FEMALE_VOICES


async def preview(voice_id: str, text: str | None = None) -> bytes:
    """
    Render a short PCM preview for the given voice_id.
    Returns raw PCM bytes (24 kHz mono 16-bit).
    Raises ValueError for unknown voice, httpx.HTTPError on TTS failure.
    """
    meta = BY_ID.get(voice_id)
    if not meta:
        raise ValueError(f"Unknown voice: {voice_id!r}")

    sample = text or meta["sample_text"]
    resp = await _client.post(
        f"{KOKORO_URL}/v1/audio/speech",
        json={"model": "kokoro", "input": sample, "voice": voice_id,
              "response_format": "pcm", "speed": 1.0},
    )
    resp.raise_for_status()
    return resp.content
