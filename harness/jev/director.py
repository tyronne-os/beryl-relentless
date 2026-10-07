"""
JEV·DIRECTOR — translate Persona decision into motion conditioning.
Output: {gaze, blink_rate, nod, micro_expression, intensity}, injected into the MOTION node.
Vocabulary, fallback tables, timeout, limits and question wording live in jev.yaml.
"""
import logging
import os

import httpx

from harness.jev._contract import SPEC, call_jev, choice, guarded, prob, questions, score, timeout_for

log = logging.getLogger("jev.director")

_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "")
_MODEL = os.environ.get("CRANE_JEV_MODEL", SPEC["model_default"])
_TIMEOUT = timeout_for("director")
_ENABLED = os.environ.get("CRANE_JEV", "true").lower() == "true"

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_GAZE = SPEC["vocab"]["gaze"]
_MICRO = SPEC["vocab"]["micro"]
_NEUTRAL = SPEC["neutral"]["director"]
_T = SPEC["fallback"]["director"]
_REPLY_CHARS = SPEC["limits"]["director_reply_chars"]


def _deterministic_fallback(persona: dict, performance: dict, reply: str) -> dict:
    action = persona.get("action", "continue")
    emotion = performance.get("emotion", "neutral")
    if not isinstance(emotion, str):
        emotion = "neutral"

    return {
        "gaze": _T["gaze_by_emotion"].get(emotion, "hold"),
        "blink_rate": _T["blink_soften"] if action == "soften" else _T["blink_default"],
        "nod": _T["nod_high"] if emotion in _T["nod_emotions"] else _T["nod_low"],
        "micro_expression": _T["micro_by_emotion"].get(emotion, "none"),
        "intensity": _T["intensity_continue"] if action == "continue" else _T["intensity_other"],
        "source": "fallback",
    }


@guarded(_NEUTRAL)
async def direct(persona: dict, performance: dict, reply: str) -> dict:
    """persona: JEV·PERSONA output; performance: jev.performance {emotion, valence, arousal, ...}; reply: text to be spoken."""
    if not _ENABLED or not _KEY:
        return _deterministic_fallback(persona, performance, reply)

    try:
        payload = {
            "model": _MODEL,
            "state": {"persona_decision": persona, "performance_plan": performance,
                      "reply_text": reply[:_REPLY_CHARS]},
            "questions": questions("director"),
        }
        ans = await call_jev(_client, _URL, payload, _KEY, _TIMEOUT)
        gaze = choice(ans, "gaze", _GAZE, "hold")
        blink = score(ans, "blink_rate", 0.5) * 0.4 + 0.2
        nod = prob(ans, "nod", 0.3)
        micro = choice(ans, "micro_expression", _MICRO, "none")
        intensity = score(ans, "intensity", 0.5)
        return {
            "gaze": gaze,
            "blink_rate": round(blink, 2),
            "nod": round(nod, 2),
            "micro_expression": micro,
            "intensity": round(intensity, 2),
            "source": "jev",
        }
    except Exception as exc:
        log.warning("JEV·DIRECTOR failed (%r) — using fallback", exc)
        return _deterministic_fallback(persona, performance, reply)
