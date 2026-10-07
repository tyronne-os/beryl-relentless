"""
JEV·PERSONA — fuse Face+Voice+history+persona card → gates the LLM reply.
Output: {in_character, tone_ok, action: continue|soften|yield}
This is the GATE: "yield" withholds the LLM reply (re-prompt or stay silent); "soften" adds a style hint.
Fails OPEN: if anything breaks the result is `continue`, so a JEV problem never mutes the avatar.
Vocabulary, thresholds, timeout, limits and question wording live in jev.yaml.
"""
import logging
import os

import httpx

from harness.jev._contract import SPEC, call_jev, choice, conf, guarded, prob, questions, timeout_for

log = logging.getLogger("jev.persona")

_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "")
_MODEL = os.environ.get("CRANE_JEV_MODEL", SPEC["model_default"])
_TIMEOUT = timeout_for("persona")
_ENABLED = os.environ.get("CRANE_JEV", "true").lower() == "true"

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_ACTIONS = SPEC["vocab"]["actions"]
_NEUTRAL = SPEC["neutral"]["persona"]
_T = SPEC["fallback"]["persona"]
_REPLY_CHARS = SPEC["limits"]["persona_reply_chars"]
_HISTORY_TURNS = SPEC["limits"]["persona_history_turns"]


def _deterministic_fallback(face: dict, voice: dict, history_len: int, persona_card: dict) -> dict:
    action, tone_ok, in_character = "continue", True, True

    if face.get("match", 1.0) < _T["soften_match_below"] and voice.get("stress", 0.0) > _T["soften_stress_above"]:
        action, tone_ok = "soften", False
    if voice.get("contradicts_words", 0) > _T["yield_contradicts_above"]:
        action, in_character = "yield", False

    return {"in_character": in_character, "tone_ok": tone_ok, "action": action,
            "confidence": _T["confidence"], "source": "fallback"}


@guarded(_NEUTRAL)
async def check(face: dict, voice: dict, history: list[dict], persona_card: dict, reply: str) -> dict:
    """
    face/voice: outputs of JEV·FACE / JEV·VOICE; history: last N turns;
    persona_card: {name, role, personality_traits, forbidden_topics}; reply: the LLM reply to evaluate.
    """
    if not _ENABLED or not _KEY:
        return _deterministic_fallback(face, voice, len(history), persona_card)

    try:
        payload = {
            "model": _MODEL,
            "state": {
                "face_check": face,
                "voice_check": voice,
                "recent_history": history[-_HISTORY_TURNS:],
                "persona_card": persona_card,
                "proposed_reply": reply[:_REPLY_CHARS],
            },
            "questions": questions("persona"),
        }
        ans = await call_jev(_client, _URL, payload, _KEY, _TIMEOUT)
        in_char = prob(ans, "in_character", 0.8) > 0.5
        tone = prob(ans, "tone_ok", 0.8) > 0.5
        action = choice(ans, "action", _ACTIONS, "continue")
        confidence = conf(ans, "action", 0.5)
        return {"in_character": in_char, "tone_ok": tone, "action": action,
                "confidence": round(confidence, 2), "source": "jev"}
    except Exception as exc:
        log.warning("JEV·PERSONA failed (%r) — using fallback", exc)
        return _deterministic_fallback(face, voice, len(history), persona_card)
