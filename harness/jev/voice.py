"""
JEV·VOICE — compare user prosody vs Beryl TTS prosody.
Output: {tone, contradicts_words, stress, confidence}
"""
import logging
import os

import httpx

log = logging.getLogger("jev.voice")

_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "")
_MODEL = os.environ.get("CRANE_JEV_MODEL", "jev-latest")
_TIMEOUT = float(os.environ.get("JEV_VOICE_TIMEOUT_S", "2.5"))
_ENABLED = os.environ.get("CRANE_JEV", "true").lower() == "true"

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_TONES = ["calm", "warm", "excited", "tense", "sad", "flat", "urgent", "playful"]


def _deterministic_fallback(user_prosody: dict, beryl_prosody: dict, reply_text: str) -> dict:
    u_pitch = user_prosody.get("pitch_hz", 150)
    u_energy = user_prosody.get("energy_db", -20)
    u_rate = user_prosody.get("rate_wpm", 140)

    tone = "calm"
    if u_energy > -10 and u_rate > 160:
        tone = "excited"
    elif u_energy < -30:
        tone = "sad"
    elif u_rate > 180:
        tone = "urgent"

    contradicts = 0.1
    exclamation_in_reply = "!" in reply_text
    b_rate = beryl_prosody.get("rate_wpm", 140)
    if exclamation_in_reply and b_rate < 120:
        contradicts = 0.7

    return {
        "tone": tone,
        "contradicts_words": round(contradicts, 2),
        "stress": round(min(1.0, u_energy / -10 + 0.5), 2),
        "confidence": 0.4,
        "source": "fallback",
    }


async def check(user_prosody: dict, beryl_prosody: dict, reply_text: str) -> dict:
    """
    user_prosody: {pitch_hz, energy_db, rate_wpm, pause_count}
    beryl_prosody: {pitch_hz, energy_db, rate_wpm} — from TTS settings used
    reply_text: the LLM reply text
    Returns {tone, contradicts_words, stress, confidence}
    """
    if not _ENABLED or not _KEY:
        return _deterministic_fallback(user_prosody, beryl_prosody, reply_text)

    try:
        payload = {
            "model": _MODEL,
            "state": {
                "user_prosody": user_prosody,
                "beryl_prosody": beryl_prosody,
                "reply_text": reply_text[:600],
            },
            "questions": {
                "tone": {
                    "type": "choice",
                    "question": "What is the overall vocal tone of the user's speech based on their prosody features?",
                    "choices": {t: f"The user sounds {t}" for t in _TONES},
                },
                "contradicts_words": {
                    "type": "noul",
                    "question": "Does the user's vocal delivery (pitch, energy, rate) contradict or conflict with the literal meaning of their words?",
                },
                "stress": {
                    "type": "score",
                    "question": "How stressed or tense does the user sound based on their prosody?",
                    "scale": ["Relaxed and unstressed", "Mildly stressed", "Clearly stressed or tense"],
                },
            },
        }
        resp = await _client.post(_URL, json=payload, headers={"Authorization": f"Bearer {_KEY}"})
        resp.raise_for_status()
        ans = resp.json().get("answers", {})
        tone = ans.get("tone", {}).get("choice", "calm")
        contradicts = ans.get("contradicts_words", {}).get("yes_prob", 0.1)
        stress = ans.get("stress", {}).get("score", 1) / 2.0
        confidence = ans.get("tone", {}).get("confidence", 0.5)
        return {
            "tone": tone,
            "contradicts_words": round(contradicts, 2),
            "stress": round(stress, 2),
            "confidence": round(confidence, 2),
            "source": "jev",
        }
    except Exception as exc:
        log.warning("JEV·VOICE failed (%s) — using fallback", exc)
        return _deterministic_fallback(user_prosody, beryl_prosody, reply_text)
