"""
JEV·FACE — compare user MediaPipe AUs vs Beryl rendered blendshapes.
Output: {user_emotion, beryl_emotion, match, confidence}

TypeSafe System One endpoint + OpenRouter fallback + deterministic local fallback.
JEV stays OUT of repair decisions — this rubric is advisory only.
"""
import logging
import os
import time
from typing import Any

import httpx

log = logging.getLogger("jev.face")

_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "")
_MODEL = os.environ.get("CRANE_JEV_MODEL", "jev-latest")
_TIMEOUT = float(os.environ.get("JEV_FACE_TIMEOUT_S", "2.5"))
_ENABLED = os.environ.get("CRANE_JEV", "true").lower() == "true"

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_EMOTIONS = ["neutral", "happy", "sad", "angry", "surprised", "disgusted", "fearful", "confused"]


def _deterministic_fallback(user_aus: dict, beryl_blendshapes: dict) -> dict:
    """
    Pure-Python fallback when JEV is down.
    Maps AU intensities to emotions via simple thresholds.
    """
    user_emotion = "neutral"
    beryl_emotion = "neutral"

    # AU4 = brow lowerer (anger/confusion), AU6+12 = smile (happy), AU1+4 = sad
    if user_aus.get("AU12", 0) > 0.5 and user_aus.get("AU6", 0) > 0.4:
        user_emotion = "happy"
    elif user_aus.get("AU4", 0) > 0.6 and user_aus.get("AU7", 0) > 0.5:
        user_emotion = "angry"
    elif user_aus.get("AU1", 0) > 0.5 and user_aus.get("AU4", 0) > 0.4:
        user_emotion = "sad"
    elif user_aus.get("AU1", 0) > 0.5 and user_aus.get("AU2", 0) > 0.5:
        user_emotion = "surprised"

    if beryl_blendshapes.get("mouthSmileLeft", 0) > 0.4:
        beryl_emotion = "happy"
    elif beryl_blendshapes.get("browDownLeft", 0) > 0.5:
        beryl_emotion = "concerned"

    match = 1.0 if user_emotion == beryl_emotion else 0.3
    return {
        "user_emotion": user_emotion,
        "beryl_emotion": beryl_emotion,
        "match": round(match, 2),
        "confidence": 0.4,
        "source": "fallback",
    }


async def check(user_aus: dict[str, float], beryl_blendshapes: dict[str, float]) -> dict:
    """
    Compare user face (MediaPipe Action Units) vs Beryl rendered face (blendshapes).
    Returns {user_emotion, beryl_emotion, match, confidence}.
    Always returns a result — falls back to deterministic heuristic if JEV is down.
    """
    if not _ENABLED or not _KEY:
        return _deterministic_fallback(user_aus, beryl_blendshapes)

    try:
        payload = {
            "model": _MODEL,
            "state": {
                "user_action_units": user_aus,
                "beryl_blendshapes": beryl_blendshapes,
            },
            "questions": {
                "user_emotion": {
                    "type": "choice",
                    "question": "What emotion do the user's facial action units express?",
                    "choices": {e: f"The user appears {e}" for e in _EMOTIONS},
                },
                "beryl_emotion": {
                    "type": "choice",
                    "question": "What emotion do Beryl's current rendered blendshapes express?",
                    "choices": {e: f"Beryl appears {e}" for e in _EMOTIONS},
                },
                "match": {
                    "type": "score",
                    "question": "How well does Beryl's facial expression match what the user's emotion calls for?",
                    "scale": ["Beryl's expression is mismatched or inappropriate",
                              "Partially aligned",
                              "Well matched to the user's emotional state"],
                },
            },
        }
        resp = await _client.post(_URL, json=payload, headers={"Authorization": f"Bearer {_KEY}"})
        resp.raise_for_status()
        data = resp.json()
        ans = data.get("answers", {})

        user_emo = ans.get("user_emotion", {}).get("choice", "neutral")
        beryl_emo = ans.get("beryl_emotion", {}).get("choice", "neutral")
        match_score = ans.get("match", {}).get("score", 2) / 2.0
        confidence = min(
            ans.get("user_emotion", {}).get("confidence", 0.5),
            ans.get("beryl_emotion", {}).get("confidence", 0.5),
        )
        return {
            "user_emotion": user_emo,
            "beryl_emotion": beryl_emo,
            "match": round(match_score, 2),
            "confidence": round(confidence, 2),
            "source": "jev",
        }

    except Exception as exc:
        log.warning("JEV·FACE failed (%s) — using fallback", exc)
        return _deterministic_fallback(user_aus, beryl_blendshapes)
