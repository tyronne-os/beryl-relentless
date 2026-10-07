"""
avatar_chain.py — extended _safe_chat for the full CPU+API pipeline.

Replaces the bare LLM→done flow with:
  audio_bytes → ASR → LISTEN (parallel) → JEV·PERSONA gate → LLM → TTS chunks
  → JEV·DIRECTOR conditioning → MOTION latents → RENDER broadcast

Import and call handle_avatar_turn(ws, audio_bytes, state) from main.py's
/ws/avatar-chat handler instead of the old _safe_chat.
"""
import asyncio
import base64
import logging
import os
import time

import httpx

log = logging.getLogger("avatar_chain")

# ── service URLs (all local; override via env) ─────────────────────────────
_ASR_URL    = os.environ.get("ASR_URL",    "http://localhost:9520/transcribe")
_KOKORO_URL = os.environ.get("TTS_URL",    "http://localhost:8012")          # kokoro (port 8012)
_MOTION_URL = os.environ.get("MOTION_URL", "http://localhost:9522/generate") # motion (port 9522)
_LISTEN_URL = os.environ.get("LISTEN_URL", "http://localhost:9521/classify")
_LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT_S", "30.0"))
_CHAIN_TIMEOUT = float(os.environ.get("CHAIN_TIMEOUT_S", "45.0"))

_http = httpx.AsyncClient(timeout=_CHAIN_TIMEOUT)

# Persona card (override per deployment)
_PERSONA_CARD = {
    "name": "Beryl",
    "role": "Conversational AI companion",
    "personality_traits": ["warm", "curious", "direct", "supportive"],
    "forbidden_topics": [],
}


# ── ASR ────────────────────────────────────────────────────────────────────

async def transcribe(audio_bytes: bytes) -> str:
    """Convert raw PCM/WAV audio bytes to text via ASR node."""
    try:
        resp = await _http.post(_ASR_URL, json={
            "audio_b64": base64.b64encode(audio_bytes).decode(),
            "language": "en",
        })
        resp.raise_for_status()
        return resp.json().get("text", "").strip()
    except Exception as exc:
        log.warning("ASR failed (%s)", exc)
        return ""


# ── LISTEN (parallel) ─────────────────────────────────────────────────────

async def get_listen_state(audio_bytes: bytes) -> dict:
    """Classify listener state from audio — runs parallel to ASR."""
    try:
        resp = await _http.post(_LISTEN_URL, json={
            "audio_b64": base64.b64encode(audio_bytes).decode(),
            "sample_rate": 16000,
            "is_user_turn": True,
        })
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.debug("LISTEN node unavailable (%s)", exc)
        return {"state": "speaking", "energy_rms": 0.1, "turn_confidence": 0.4, "source": "fallback"}


# ── LLM via existing chat.py ───────────────────────────────────────────────

async def call_llm(transcript: str, history: list[dict], ws_send_fn) -> str:
    """
    Calls the existing /api/chat endpoint (chat.py) with the transcript.
    Streams partial tokens back via ws_send_fn.
    Returns the full reply text.
    """
    from chat import stream_reply  # existing chat.py function
    full_reply = ""
    try:
        async for token in stream_reply(transcript, history):
            full_reply += token
            await ws_send_fn({"type": "token", "text": token})
    except Exception as exc:
        log.warning("LLM stream failed (%s)", exc)
        full_reply = "I'm sorry, I couldn't process that just now."
    return full_reply


# ── TTS ────────────────────────────────────────────────────────────────────

async def tts_and_stream(ws, reply: str):
    """
    Calls Kokoro TTS via OpenAI-compatible /v1/audio/speech, streams PCM over ws.
    {type: tts_start} → {type: tts_chunk, audio_b64, sample_rate} × N → {type: tts_done}
    Falls back to signalling client to call /api/services/kokoro/tts directly.
    Returns prosody dict for JEV·VOICE.
    """
    await ws.send_json({"type": "tts_start"})
    prosody = {"pitch_hz": 170, "energy_db": -18, "rate_wpm": 145}
    try:
        resp = await _http.post(
            f"{_KOKORO_URL}/v1/audio/speech",
            json={"model": "kokoro", "input": reply, "voice": "af_heart",
                  "response_format": "pcm", "speed": 1.0},
        )
        resp.raise_for_status()
        audio_bytes = resp.content
        CHUNK = 8192
        for i in range(0, len(audio_bytes), CHUNK):
            await ws.send_json({
                "type": "tts_chunk",
                "audio_b64": base64.b64encode(audio_bytes[i:i + CHUNK]).decode(),
                "sample_rate": 24000,
            })
    except Exception as exc:
        log.warning("TTS server failed (%s) — client will call kokoro directly", exc)
        await ws.send_json({"type": "tts_fallback", "text": reply})
    await ws.send_json({"type": "tts_done"})
    return prosody


# ── JEV calls (with fallbacks in each module) ─────────────────────────────

async def run_jev_pipeline(
    user_aus: dict,
    beryl_blendshapes: dict,
    user_prosody: dict,
    beryl_prosody: dict,
    history: list[dict],
    reply: str,
    performance: dict,
) -> tuple[dict, dict, dict]:
    """
    Runs JEV·FACE, JEV·VOICE, JEV·PERSONA in dependency order.
    Returns (face_result, voice_result, persona_result).
    All three have deterministic fallbacks — never raise.
    """
    from harness.jev import face as jev_face
    from harness.jev import voice as jev_voice
    from harness.jev import persona as jev_persona

    face_res, voice_res = await asyncio.gather(
        jev_face.check(user_aus, beryl_blendshapes),
        jev_voice.check(user_prosody, beryl_prosody, reply),
    )
    persona_res = await jev_persona.check(
        face_res, voice_res, history, _PERSONA_CARD, reply
    )
    return face_res, voice_res, persona_res


async def run_jev_director(persona: dict, performance: dict, reply: str) -> dict:
    from harness.jev import director as jev_director
    return await jev_director.direct(persona, performance, reply)


# ── MOTION latents ────────────────────────────────────────────────────────

async def generate_motion_latents(audio_b64: str, conditioning: dict) -> dict | None:
    """
    Calls the motion node (JoyVASA/FLOAT) with audio + JEV·DIRECTOR conditioning.
    Returns {latents, fps, frame_count} or None on failure.
    """
    try:
        resp = await _http.post(_MOTION_URL, json={
            "audio_b64": audio_b64,
            "conditioning": conditioning,
        })
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        log.warning("MOTION node failed (%s) — no latents this turn", exc)
        return None


# ── Main chain entry point ────────────────────────────────────────────────

async def handle_avatar_turn(ws, audio_bytes: bytes, state: dict):
    """
    Full CPU+API turn handler. Call this from /ws/avatar-chat instead of _safe_chat.

    state: {
        history: list[dict],          # conversation history
        user_aus: dict,               # MediaPipe action units from client
        beryl_blendshapes: dict,      # current rendered blendshapes
        performance: dict,            # last jev.performance result
        stage: str,                   # L0 | L1 | L2
    }
    """
    t_start = time.monotonic()
    history: list[dict] = state.get("history", [])
    user_aus: dict = state.get("user_aus", {})
    beryl_blendshapes: dict = state.get("beryl_blendshapes", {})
    performance: dict = state.get("performance", {"emotion": "neutral"})
    audio_b64 = base64.b64encode(audio_bytes).decode()

    # 1. ASR + LISTEN in parallel
    transcript, listen_state = await asyncio.gather(
        transcribe(audio_bytes),
        get_listen_state(audio_bytes),
    )

    if not transcript:
        await ws.send_json({"type": "no_speech"})
        return

    await ws.send_json({"type": "transcript", "text": transcript})

    # 2. LLM — stream tokens while building full reply
    reply_tokens: list[str] = []

    async def _collect_and_send(msg):
        if msg.get("type") == "token":
            reply_tokens.append(msg["text"])
        await ws.send_json(msg)

    full_reply = await call_llm(transcript, history, _collect_and_send)

    # 3. JEV pipeline — face+voice+persona gate (fallbacks ensure no exception)
    user_prosody = {"pitch_hz": 150, "energy_db": -20, "rate_wpm": 140}
    beryl_prosody = {"pitch_hz": 170, "energy_db": -18, "rate_wpm": 145}

    face_res, voice_res, persona_res = await run_jev_pipeline(
        user_aus, beryl_blendshapes,
        user_prosody, beryl_prosody,
        history, full_reply, performance,
    )

    if persona_res.get("action") == "yield":
        log.info("JEV·PERSONA yield — reply withheld")
        await ws.send_json({"type": "yield", "reason": "persona_gate"})
        return

    # 4. JEV·DIRECTOR → motion conditioning
    director_res = await run_jev_director(persona_res, performance, full_reply)

    # 5. TTS — stream audio chunks, capture prosody
    beryl_prosody = await tts_and_stream(ws, full_reply)

    # 6. MOTION latents (non-blocking — send after TTS starts)
    motion = await generate_motion_latents(audio_b64, director_res)

    # 7. Emit done with all signals
    elapsed_ms = round((time.monotonic() - t_start) * 1000)
    await ws.send_json({
        "type": "done",
        "transcript": transcript,
        "reply": full_reply,
        "latents": motion.get("latents") if motion else None,
        "latents_fps": motion.get("fps", 25) if motion else None,
        "jev": {
            "face": face_res,
            "voice": voice_res,
            "persona": persona_res,
            "director": director_res,
            "listen": listen_state,
        },
        "elapsed_ms": elapsed_ms,
        "stage": state.get("stage", "L1"),
    })

    # Update conversation history (caller should persist this)
    state["history"] = history + [
        {"role": "user", "content": transcript},
        {"role": "assistant", "content": full_reply},
    ]
