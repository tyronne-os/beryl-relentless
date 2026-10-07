"""
What the stages call out to. Stages only use this small duck-typed surface, so tests swap in a fake and the first
live run (increment 1) swaps in LiveServices. LiveServices is NOT yet exercised against the real cluster.
"""
import base64
import os
import time
from collections import defaultdict
from pathlib import Path

from harness.selftest.spec import SPEC

PERSONA_PROMPT = (
    "You are Beryl: warm, curious, direct and supportive. You are speaking out loud in a live voice conversation, "
    "so answer the way a person talks: short sentences, contractions, no lists, no markdown, no emoji."
)  # must mirror the production chat.py prompt; replace with the real call once chat.py is vendored here


class Recorder:
    """Wraps any services object; records per-call latency, errors and silent failures for stage 3."""

    def __init__(self, inner):
        self._inner = inner
        self.latency_ms = defaultdict(list)
        self.errors = defaultdict(list)
        self.silent_failures = []
        self.turn_ms = []

    def note_silent_failure(self, node: str, what: str):
        self.silent_failures.append({"node": node, "what": what})

    def note_turn(self, ms: float):
        self.turn_ms.append(ms)

    def __getattr__(self, name):
        fn = getattr(self._inner, name)
        if not callable(fn):
            return fn

        async def wrapped(*a, **k):
            t0 = time.monotonic()
            try:
                return await fn(*a, **k)
            except Exception as exc:
                self.errors[name].append(repr(exc))
                raise
            finally:
                self.latency_ms[name].append((time.monotonic() - t0) * 1000)
        return wrapped

    def snapshot(self) -> dict:
        def p95(v):
            v = sorted(v)
            return round(v[min(len(v) - 1, int(0.95 * len(v)))], 1) if v else None
        return {"latency_ms": {k: {"n": len(v), "p50": round(sorted(v)[len(v) // 2], 1), "p95": p95(v)}
                               for k, v in self.latency_ms.items()},
                "errors": {k: v[:3] for k, v in self.errors.items()},
                "silent_failures": list(self.silent_failures),
                "turn_ms": {"n": len(self.turn_ms), "p95": p95(self.turn_ms)}}


class LiveServices:
    def __init__(self, host: str = "localhost", tts_voice: str = "af_heart"):
        import httpx
        self.host, self.voice = host, tts_voice
        self.http = httpx.AsyncClient(timeout=120.0)
        self.p = SPEC["ports"]

    def _url(self, node: str, path: str) -> str:
        return f"http://{self.host}:{self.p[node]}{path}"

    async def tts(self, text: str, voice: str | None = None) -> bytes:
        r = await self.http.post(self._url("tts", "/v1/audio/speech"), json={
            "model": "kokoro", "input": text, "voice": voice or self.voice, "response_format": "wav", "speed": 1.0})
        r.raise_for_status()
        return r.content

    async def asr(self, wav: bytes) -> str:
        from harness.selftest.evidence import read_wav, to_pcm16
        x, sr = read_wav(wav)
        r = await self.http.post(self._url("asr", "/transcribe"), json={
            "audio_b64": base64.b64encode(to_pcm16(x)).decode(), "sample_rate": sr})
        r.raise_for_status()
        return r.json().get("text", "")

    async def llm_reply(self, user_turn: str) -> str:
        key = os.environ.get("NGC_ENTERPRISE_KEY") or os.environ.get("NVIDIA_API_KEY", "")
        if not key:
            raise RuntimeError("no NIM key (NGC_ENTERPRISE_KEY / NVIDIA_API_KEY) for the LLM reply")
        r = await self.http.post("https://integrate.api.nvidia.com/v1/chat/completions",
                                 headers={"Authorization": f"Bearer {key}"},
                                 json={"model": "nvidia/nemotron-mini-4b-instruct", "max_tokens": 200,
                                       "messages": [{"role": "system", "content": PERSONA_PROMPT},
                                                    {"role": "user", "content": user_turn}]})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()

    async def render_clip(self, photo: str, audio: str, out: str) -> dict:
        r = await self.http.post(self._url("render", "/render"), json={
            "photo_b64": base64.b64encode(Path(photo).read_bytes()).decode(),
            "audio_b64": base64.b64encode(Path(audio).read_bytes()).decode(), "save_mp4": True}, timeout=600.0)
        r.raise_for_status()
        meta = r.json()
        if "video_url" not in meta:
            raise RuntimeError(f"render returned no video (model={meta.get('model')}, error={meta.get('error')})")
        v = await self.http.get(self._url("render", meta["video_url"]), timeout=120.0)
        v.raise_for_status()
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(v.content)
        return {**meta, "path": out}

    async def health(self) -> dict:
        out = {}
        for node in ("tts", "asr", "listen", "motion", "render", "verify"):
            try:
                r = await self.http.get(self._url(node, "/health"), timeout=3.0)
                body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                out[node] = {"ok": r.status_code < 400, **body}
            except Exception as exc:
                out[node] = {"ok": False, "error": repr(exc)}
        return out
