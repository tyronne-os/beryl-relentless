"""
Shared JEV contract (standards S3, S4, S6): bounded upstream call, strict answer parsing,
and a never-raise guard. A JEV answer is accepted whole or not at all: any slot that is
present but malformed (wrong type, out of range, outside the question's choices) raises
BadAnswer and the caller falls back to its deterministic result.
"""
import asyncio
import functools
import logging
import math
import os
from pathlib import Path

import yaml

log = logging.getLogger("jev")


class BadAnswer(ValueError):
    pass


_PLACEMENTS = ("face", "voice", "persona", "director", "verify")


def _load_spec() -> dict:
    spec = yaml.safe_load((Path(__file__).with_name("jev.yaml")).read_text())
    for sect in ("vocab", "neutral", "fallback", "questions", "timeout_s"):
        missing = [p for p in _PLACEMENTS if sect != "vocab" and p not in spec.get(sect, {})]
        if missing:
            raise ValueError(f"jev.yaml: section {sect!r} missing placements {missing}")
    for place, qs in spec["questions"].items():
        for key, q in qs.items():
            if q["type"] not in spec["question_types"]:
                raise ValueError(f"jev.yaml: {place}.{key} has unknown type {q['type']!r}")
            if q["type"] == "choice" and q["vocab"] not in spec["vocab"]:
                raise ValueError(f"jev.yaml: {place}.{key} names unknown vocab {q['vocab']!r}")
            if q["type"] == "score" and len(q["scale"]) != 3:
                raise ValueError(f"jev.yaml: {place}.{key} scale must have 3 anchors (scores 0..2)")
    return spec


SPEC = _load_spec()


def timeout_for(place: str) -> float:
    return float(os.environ.get(f"JEV_{place.upper()}_TIMEOUT_S", SPEC["timeout_s"][place]))


def questions(place: str) -> dict:
    """Expand a placement's questions from the spec into the System One wire format."""
    out = {}
    for key, q in SPEC["questions"][place].items():
        d = {"type": q["type"], "question": q["question"]}
        if q["type"] == "choice":
            v = SPEC["vocab"][q["vocab"]]
            d["choices"] = dict(v) if isinstance(v, dict) else {x: f"{q['choice_prefix']} {x}" for x in v}
        elif q["type"] == "score":
            d["scale"] = list(q["scale"])
        out[key] = d
    return out


async def call_jev(client, url: str, payload: dict, key: str, timeout: float) -> dict:
    """POST one System One request under a hard deadline; return the validated `answers` dict."""
    resp = await asyncio.wait_for(
        client.post(url, json=payload, headers={"Authorization": f"Bearer {key}"}), timeout + 0.5)
    resp.raise_for_status()
    body = resp.json()
    ans = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(ans, dict):
        raise BadAnswer("response has no 'answers' object")
    return ans


def _slot(ans: dict, key: str) -> dict:
    s = ans.get(key, {})
    if not isinstance(s, dict):
        raise BadAnswer(f"{key}: slot is not an object")
    return s


def _number(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v):
        raise BadAnswer(f"{what}={v!r} is not a number")
    return float(v)


def _ranged(s: dict, field: str, key: str, default: float, top: float) -> float:
    if field not in s:
        return default
    v = _number(s[field], f"{key}.{field}")
    if not 0.0 <= v <= top:
        raise BadAnswer(f"{key}.{field}={v} outside 0..{top:g}")
    return v


def choice(ans: dict, key: str, allowed, default: str) -> str:
    s = _slot(ans, key)
    if "choice" not in s:
        return default
    v = s["choice"]
    if not isinstance(v, str) or v not in allowed:
        raise BadAnswer(f"{key}.choice={v!r} not one of the question's choices")
    return v


def prob(ans: dict, key: str, default: float) -> float:
    """yes_prob in [0,1]."""
    return _ranged(_slot(ans, key), "yes_prob", key, default, 1.0)


def conf(ans: dict, key: str, default: float) -> float:
    """confidence in [0,1]."""
    return _ranged(_slot(ans, key), "confidence", key, default, 1.0)


def score(ans: dict, key: str, default_unit: float, top: int = 2) -> float:
    """Discrete 0..top rubric score, returned normalised to [0,1]."""
    s = _slot(ans, key)
    if "score" not in s:
        return default_unit
    return _ranged(s, "score", key, 0.0, float(top)) / top


def guarded(neutral: dict):
    """Never raise: any failure (including garbage inputs from the browser) yields `neutral`.
    Neutral values differ by role: experience gates fail OPEN, VERIFY fails CLOSED."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **k):
            try:
                return await fn(*a, **k)
            except Exception as exc:
                log.error("%s failed (%r) — neutral result", fn.__qualname__, exc)
                return {**neutral, "source": "fallback"}
        return wrapper
    return deco
