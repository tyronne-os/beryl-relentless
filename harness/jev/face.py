"""
JEV·FACE — compare user MediaPipe AUs vs Beryl rendered blendshapes.
Output: {user_emotion, beryl_emotion, match, confidence}

Vocabulary, thresholds, timeout and question wording live in jev.yaml.
TypeSafe System One + deterministic local fallback. JEV stays OUT of repair decisions — advisory only.
"""
import logging
import os

import httpx

from harness.jev._contract import SPEC, call_jev, choice, conf, guarded, questions, score, timeout_for

log = logging.getLogger("jev.face")

_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "")
_MODEL = os.environ.get("CRANE_JEV_MODEL", SPEC["model_default"])
_TIMEOUT = timeout_for("face")
_ENABLED = os.environ.get("CRANE_JEV", "true").lower() == "true"

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_EMOTIONS = SPEC["vocab"]["emotions"]
_NEUTRAL = SPEC["neutral"]["face"]
_T = SPEC["fallback"]["face"]


def _deterministic_fallback(user_aus: dict, beryl_blendshapes: dict) -> dict:
    """Pure-Python fallback: AU intensities -> emotion by threshold (AU4 brow lowerer, AU6+12 smile, ...)."""
    au = user_aus.get
    user_emotion = "neutral"
    if au("AU12", 0) > _T["happy_au12"] and au("AU6", 0) > _T["happy_au6"]:
        user_emotion = "happy"
    elif au("AU4", 0) > _T["angry_au4"] and au("AU7", 0) > _T["angry_au7"]:
        user_emotion = "angry"
    elif au("AU1", 0) > _T["sad_au1"] and au("AU4", 0) > _T["sad_au4"]:
        user_emotion = "sad"
    elif au("AU1", 0) > _T["surprised_au1"] and au("AU2", 0) > _T["surprised_au2"]:
        user_emotion = "surprised"

    beryl_emotion = "neutral"
    if beryl_blendshapes.get("mouthSmileLeft", 0) > _T["beryl_smile"]:
        beryl_emotion = "happy"
    elif beryl_blendshapes.get("browDownLeft", 0) > _T["beryl_brow"]:
        beryl_emotion = "concerned"

    return {
        "user_emotion": user_emotion,
        "beryl_emotion": beryl_emotion,
        "match": _T["match_same"] if user_emotion == beryl_emotion else _T["match_diff"],
        "confidence": _T["confidence"],
        "source": "fallback",
    }


@guarded(_NEUTRAL)
async def check(user_aus: dict[str, float], beryl_blendshapes: dict[str, float]) -> dict:
    """Always returns a schema-valid result; falls back to the deterministic heuristic if JEV is unusable."""
    if not _ENABLED or not _KEY:
        return _deterministic_fallback(user_aus, beryl_blendshapes)

    try:
        payload = {
            "model": _MODEL,
            "state": {"user_action_units": user_aus, "beryl_blendshapes": beryl_blendshapes},
            "questions": questions("face"),
        }
        ans = await call_jev(_client, _URL, payload, _KEY, _TIMEOUT)
        user_emo = choice(ans, "user_emotion", _EMOTIONS, "neutral")
        beryl_emo = choice(ans, "beryl_emotion", _EMOTIONS, "neutral")
        match_score = score(ans, "match", 1.0)
        confidence = min(conf(ans, "user_emotion", 0.5), conf(ans, "beryl_emotion", 0.5))
        return {
            "user_emotion": user_emo,
            "beryl_emotion": beryl_emo,
            "match": round(match_score, 2),
            "confidence": round(confidence, 2),
            "source": "jev",
        }
    except Exception as exc:
        log.warning("JEV·FACE failed (%r) — using fallback", exc)
        return _deterministic_fallback(user_aus, beryl_blendshapes)
