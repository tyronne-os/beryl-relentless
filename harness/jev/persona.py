"""
JEV·PERSONA — fuse Face+Voice+history+persona card → gates the LLM reply.
Output: {in_character, tone_ok, action: continue|soften|yield}
This is the GATE: if action == "yield", the LLM reply is withheld and
the avatar re-prompts or stays silent. action == "soften" adds a style hint.
"""
import logging
import os

import httpx

log = logging.getLogger("jev.persona")

_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "")
_MODEL = os.environ.get("CRANE_JEV_MODEL", "jev-latest")
_TIMEOUT = float(os.environ.get("JEV_PERSONA_TIMEOUT_S", "2.5"))
_ENABLED = os.environ.get("CRANE_JEV", "true").lower() == "true"

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_ACTIONS = {
    "continue": "Deliver the reply as-is — tone and character are appropriate",
    "soften": "Deliver the reply but with a gentler, more careful tone",
    "yield": "Withhold the reply — the avatar should re-think or stay silent",
}


def _deterministic_fallback(face: dict, voice: dict, history_len: int, persona_card: dict) -> dict:
    action = "continue"
    tone_ok = True
    in_character = True

    # If face and voice both show high stress, soften
    face_match = face.get("match", 1.0)
    voice_stress = voice.get("stress", 0.0)
    if face_match < 0.3 and voice_stress > 0.7:
        action = "soften"
        tone_ok = False

    # If voice contradicts words, yield
    if voice.get("contradicts_words", 0) > 0.8:
        action = "yield"
        in_character = False

    return {
        "in_character": in_character,
        "tone_ok": tone_ok,
        "action": action,
        "confidence": 0.4,
        "source": "fallback",
    }


async def check(face: dict, voice: dict, history: list[dict],
                persona_card: dict, reply: str) -> dict:
    """
    face: output of JEV·FACE
    voice: output of JEV·VOICE
    history: last N turns
    persona_card: {name, role, personality_traits, forbidden_topics}
    reply: the LLM-generated reply text to evaluate
    Returns {in_character, tone_ok, action: continue|soften|yield}
    """
    if not _ENABLED or not _KEY:
        return _deterministic_fallback(face, voice, len(history), persona_card)

    recent_history = history[-6:] if len(history) > 6 else history
    try:
        payload = {
            "model": _MODEL,
            "state": {
                "face_check": face,
                "voice_check": voice,
                "recent_history": recent_history,
                "persona_card": persona_card,
                "proposed_reply": reply[:800],
            },
            "questions": {
                "in_character": {
                    "type": "noul",
                    "question": "Is the proposed_reply consistent with the persona_card's described personality and role?",
                },
                "tone_ok": {
                    "type": "noul",
                    "question": "Given the user's current emotional state (face_check, voice_check), is the proposed_reply's tone appropriate?",
                },
                "action": {
                    "type": "choice",
                    "question": "What should the avatar do with the proposed_reply?",
                    "choices": _ACTIONS,
                },
            },
        }
        resp = await _client.post(_URL, json=payload, headers={"Authorization": f"Bearer {_KEY}"})
        resp.raise_for_status()
        ans = resp.json().get("answers", {})
        in_char = ans.get("in_character", {}).get("yes_prob", 0.8) > 0.5
        tone = ans.get("tone_ok", {}).get("yes_prob", 0.8) > 0.5
        action = ans.get("action", {}).get("choice", "continue")
        confidence = ans.get("action", {}).get("confidence", 0.5)
        return {
            "in_character": in_char,
            "tone_ok": tone,
            "action": action,
            "confidence": round(confidence, 2),
            "source": "jev",
        }
    except Exception as exc:
        log.warning("JEV·PERSONA failed (%s) — using fallback", exc)
        return _deterministic_fallback(face, voice, len(history), persona_card)
