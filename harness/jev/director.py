"""
JEV·DIRECTOR — translate Persona decision into motion conditioning.
Output: {gaze, blink_rate, nod, micro_expression, intensity}
These values are injected into the MOTION node as conditioning parameters.
"""
import logging
import os

import httpx

log = logging.getLogger("jev.director")

_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "")
_MODEL = os.environ.get("CRANE_JEV_MODEL", "jev-latest")
_TIMEOUT = float(os.environ.get("JEV_DIRECTOR_TIMEOUT_S", "2.0"))
_ENABLED = os.environ.get("CRANE_JEV", "true").lower() == "true"

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_GAZE = {
    "hold": "Steady eye contact — engaged and present",
    "away_think": "Brief glance up/aside — forming a thought",
    "down_sincere": "Brief downward look — sincere or sensitive moment",
    "aside_recall": "Glance aside — recalling a fact or memory",
    "soft_blink": "Slow blink — warmth or acceptance",
}
_MICRO = {
    "none": "No micro-expression",
    "brow_flash": "Quick eyebrow raise — interest or mild surprise",
    "brow_knit": "Brief brow furrow — concentration or concern",
    "smile_flick": "Quick corner-of-mouth smile",
    "lip_press": "Brief lip press — restraint or careful consideration",
    "nostril_flare": "Slight nostril flare — heightened attention",
}


def _deterministic_fallback(persona: dict, performance: dict, reply: str) -> dict:
    action = persona.get("action", "continue")
    emotion = performance.get("emotion", "neutral")

    gaze_map = {
        "curious": "away_think", "excited": "hold",
        "concerned": "down_sincere", "serious": "aside_recall",
        "neutral": "hold", "warm": "soft_blink", "happy": "hold",
    }
    micro_map = {
        "curious": "brow_flash", "concerned": "brow_knit",
        "happy": "smile_flick", "serious": "lip_press",
        "neutral": "none",
    }
    intensity = 0.6 if action == "continue" else 0.3

    return {
        "gaze": gaze_map.get(emotion, "hold"),
        "blink_rate": 0.4 if action == "soften" else 0.25,
        "nod": 0.7 if emotion in ("curious", "warm") else 0.2,
        "micro_expression": micro_map.get(emotion, "none"),
        "intensity": intensity,
        "source": "fallback",
    }


async def direct(persona: dict, performance: dict, reply: str) -> dict:
    """
    persona: output of JEV·PERSONA {in_character, tone_ok, action}
    performance: output of jev.performance {emotion, valence, arousal, gaze, nod...}
    reply: the reply text about to be spoken
    Returns {gaze, blink_rate, nod, micro_expression, intensity}
    """
    if not _ENABLED or not _KEY:
        return _deterministic_fallback(persona, performance, reply)

    try:
        payload = {
            "model": _MODEL,
            "state": {
                "persona_decision": persona,
                "performance_plan": performance,
                "reply_text": reply[:400],
            },
            "questions": {
                "gaze": {
                    "type": "choice",
                    "question": "Where should Beryl's eyes go during this reply to feel most natural and emotionally resonant?",
                    "choices": _GAZE,
                },
                "blink_rate": {
                    "type": "score",
                    "question": "How frequently should Beryl blink during this reply?",
                    "scale": ["Slow, infrequent blinks (calm, focused)", "Normal blink rate", "Slightly elevated blink rate (mild stress or excitement)"],
                },
                "nod": {
                    "type": "noul",
                    "question": "Should Beryl give a small head nod while delivering this reply to emphasise a point?",
                },
                "micro_expression": {
                    "type": "choice",
                    "question": "Which brief involuntary facial flash would feel authentic at the start of this reply?",
                    "choices": _MICRO,
                },
                "intensity": {
                    "type": "score",
                    "question": "How emotionally expressive and physically animated should Beryl be during this reply?",
                    "scale": ["Subdued and still", "Moderately expressive", "Visibly animated and engaged"],
                },
            },
        }
        resp = await _client.post(_URL, json=payload, headers={"Authorization": f"Bearer {_KEY}"})
        resp.raise_for_status()
        ans = resp.json().get("answers", {})
        gaze = ans.get("gaze", {}).get("choice", "hold")
        blink = ans.get("blink_rate", {}).get("score", 1) / 2.0 * 0.4 + 0.2
        nod = ans.get("nod", {}).get("yes_prob", 0.3)
        micro = ans.get("micro_expression", {}).get("choice", "none")
        intensity = ans.get("intensity", {}).get("score", 1) / 2.0
        return {
            "gaze": gaze,
            "blink_rate": round(blink, 2),
            "nod": round(nod, 2),
            "micro_expression": micro,
            "intensity": round(intensity, 2),
            "source": "jev",
        }
    except Exception as exc:
        log.warning("JEV·DIRECTOR failed (%s) — using fallback", exc)
        return _deterministic_fallback(persona, performance, reply)
