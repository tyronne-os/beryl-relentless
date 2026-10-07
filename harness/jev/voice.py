"""
JEV·VOICE — compare user prosody vs Beryl TTS prosody.
Output: {tone, contradicts_words, stress, confidence}
Vocabulary, thresholds, timeout, limits and question wording live in jev.yaml.
"""
import logging
import os

import httpx

from harness.jev._contract import SPEC, call_jev, choice, conf, guarded, prob, questions, score, timeout_for

log = logging.getLogger("jev.voice")

_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "")
_MODEL = os.environ.get("CRANE_JEV_MODEL", SPEC["model_default"])
_TIMEOUT = timeout_for("voice")
_ENABLED = os.environ.get("CRANE_JEV", "true").lower() == "true"

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_TONES = SPEC["vocab"]["tones"]
_NEUTRAL = SPEC["neutral"]["voice"]
_T = SPEC["fallback"]["voice"]
_REPLY_CHARS = SPEC["limits"]["voice_reply_chars"]


def _deterministic_fallback(user_prosody: dict, beryl_prosody: dict, reply_text: str) -> dict:
    u_energy = user_prosody.get("energy_db", _T["default_energy_db"])
    u_rate = user_prosody.get("rate_wpm", _T["default_rate_wpm"])

    tone = "calm"
    if u_energy > _T["excited_energy_db"] and u_rate > _T["excited_rate_wpm"]:
        tone = "excited"
    elif u_energy < _T["sad_energy_db"]:
        tone = "sad"
    elif u_rate > _T["urgent_rate_wpm"]:
        tone = "urgent"

    contradicts = _T["contradicts_low"]
    b_rate = beryl_prosody.get("rate_wpm", _T["default_rate_wpm"])
    if "!" in reply_text and b_rate < _T["contradicts_beryl_rate_wpm"]:
        contradicts = _T["contradicts_high"]

    floor = _T["stress_floor_db"]
    stress = max(0.0, min(1.0, (u_energy - floor) / -floor))  # dBFS: floor = quiet .. 0 = loud
    return {
        "tone": tone,
        "contradicts_words": round(contradicts, 2),
        "stress": round(stress, 2),
        "confidence": _T["confidence"],
        "source": "fallback",
    }


@guarded(_NEUTRAL)
async def check(user_prosody: dict, beryl_prosody: dict, reply_text: str) -> dict:
    """
    user_prosody: {pitch_hz, energy_db, rate_wpm, pause_count}; beryl_prosody: {pitch_hz, energy_db, rate_wpm}
    Returns {tone, contradicts_words, stress, confidence}
    """
    if not _ENABLED or not _KEY:
        return _deterministic_fallback(user_prosody, beryl_prosody, reply_text)

    try:
        payload = {
            "model": _MODEL,
            "state": {"user_prosody": user_prosody, "beryl_prosody": beryl_prosody,
                      "reply_text": reply_text[:_REPLY_CHARS]},
            "questions": questions("voice"),
        }
        ans = await call_jev(_client, _URL, payload, _KEY, _TIMEOUT)
        tone = choice(ans, "tone", _TONES, "calm")
        contradicts = prob(ans, "contradicts_words", 0.1)
        stress = score(ans, "stress", 0.5)
        confidence = conf(ans, "tone", 0.5)
        return {
            "tone": tone,
            "contradicts_words": round(contradicts, 2),
            "stress": round(stress, 2),
            "confidence": round(confidence, 2),
            "source": "jev",
        }
    except Exception as exc:
        log.warning("JEV·VOICE failed (%r) — using fallback", exc)
        return _deterministic_fallback(user_prosody, beryl_prosody, reply_text)
