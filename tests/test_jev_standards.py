"""
JEV standards — the contract every JEV placement must meet, tested offline with a fake upstream.
Run:  python3 tests/test_jev_standards.py     (no network, no key, no GPU; ~2 s)
Design + rationale: docs/JEV-DESIGN.md  (S1..S12 below match the standard numbers there)
"""
import asyncio
import contextlib
import json
import logging
import math
import random
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from harness.jev import director, face, persona, verify, voice  # noqa: E402
from harness.jev._contract import SPEC, questions  # noqa: E402

V = SPEC["vocab"]

KEY = "test-key-123"
UNIT = (0.0, 1.0)


# ── fake upstream ────────────────────────────────────────────────────────────
class Resp:
    def __init__(self, body=None, status=200, json_raises=False):
        self._body, self.status_code, self._jr = body, status, json_raises

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        if self._jr:
            raise ValueError("not json")
        return self._body


class Fake:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    async def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        return await self.handler(json)


@contextlib.contextmanager
def configured(mod, handler=None, key=KEY, enabled=True, timeout=0.2):
    saved = {k: getattr(mod, k) for k in ("_KEY", "_ENABLED", "_TIMEOUT", "_client")}
    fake = Fake(handler or (lambda p: None))
    mod._KEY, mod._ENABLED, mod._TIMEOUT, mod._client = key, enabled, timeout, fake
    try:
        yield fake
    finally:
        for k, v in saved.items():
            setattr(mod, k, v)


def good_answers(payload):
    ans = {}
    for k, q in payload["questions"].items():
        if q["type"] == "choice":
            ans[k] = {"choice": next(iter(q["choices"])), "confidence": 0.9}
        elif q["type"] == "score":
            ans[k] = {"score": 1, "confidence": 0.9}
        else:
            ans[k] = {"yes_prob": 0.8, "confidence": 0.9}
    return {"answers": ans}


async def ok_handler(payload):
    return Resp(good_answers(payload))


# ── output schemas (the standard) ────────────────────────────────────────────
def _unit(r, k, bad):
    v = r.get(k)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v) or not UNIT[0] <= v <= UNIT[1]:
        bad.append(f"{k}={v!r} not a number in [0,1]")


def _enum(r, k, allowed, bad):
    v = r.get(k)
    if not (isinstance(v, str) and v in allowed):
        bad.append(f"{k}={r.get(k)!r} not in {sorted(allowed)}")


def _bool(r, k, bad):
    if not isinstance(r.get(k), bool):
        bad.append(f"{k}={r.get(k)!r} not a bool")


def problems_face(r):
    bad = []
    _enum(r, "user_emotion", V["emotions"], bad), _enum(r, "beryl_emotion", V["emotions"], bad)
    _unit(r, "match", bad), _unit(r, "confidence", bad), _enum(r, "source", {"fallback", "jev"}, bad)
    return bad


def problems_voice(r):
    bad = []
    _enum(r, "tone", V["tones"], bad)
    for k in ("contradicts_words", "stress", "confidence"):
        _unit(r, k, bad)
    _enum(r, "source", {"fallback", "jev"}, bad)
    return bad


def problems_persona(r):
    bad = []
    _bool(r, "in_character", bad), _bool(r, "tone_ok", bad), _enum(r, "action", V["actions"], bad)
    _unit(r, "confidence", bad), _enum(r, "source", {"fallback", "jev"}, bad)
    return bad


def problems_director(r):
    bad = []
    _enum(r, "gaze", V["gaze"], bad), _enum(r, "micro_expression", V["micro"], bad)
    for k in ("blink_rate", "nod", "intensity"):
        _unit(r, k, bad)
    _enum(r, "source", {"fallback", "jev"}, bad)
    return bad


def problems_verify(r):
    bad = []
    _enum(r, "telemetry_stage", V["stages"], bad), _enum(r, "stated_stage", V["stages"], bad)
    for k in ("consistent", "is_motion_visible", "agrees"):
        _bool(r, k, bad)
    _enum(r, "source", {"fallback", "jev+deterministic"}, bad)
    return bad


CALLS = {
    "face": (face, problems_face, lambda: face.check({"AU12": .8, "AU6": .7}, {"mouthSmileLeft": .6})),
    "voice": (voice, problems_voice, lambda: voice.check(
        {"pitch_hz": 180, "energy_db": -18, "rate_wpm": 150}, {"pitch_hz": 170, "energy_db": -18, "rate_wpm": 145}, "Nice to meet you!")),
    "persona": (persona, problems_persona, lambda: persona.check(
        {"match": .9}, {"stress": .2, "contradicts_words": .1}, [{"role": "user", "text": "hi"}], {"name": "Beryl"}, "Hello there")),
    "director": (director, problems_director, lambda: director.direct({"action": "continue"}, {"emotion": "warm"}, "Hello there")),
    "verify": (verify, problems_verify, lambda: verify.check(
        {"stated_stage": "L2", "duplug_state": "speaking", "audio_energy_rms": .2}, {"changed_pixel_area": 1500, "frame_diff_mean": 3.0})),
}


# ── hostile upstreams ────────────────────────────────────────────────────────
def _all_slots(payload, **fields):
    return {k: dict(fields) for k in payload["questions"]}


async def h_500(p):
    return Resp({"answers": {}}, status=500)


async def h_conn(p):
    raise httpx.ConnectError("down")


async def h_hang(p):
    await asyncio.sleep(30)


async def h_notjson(p):
    return Resp(json_raises=True)


async def h_list(p):
    return Resp([1, 2, 3])


async def h_no_answers(p):
    return Resp({"nope": 1})


async def h_answers_list(p):
    return Resp({"answers": [1, 2]})


async def h_slots_not_dicts(p):
    return Resp({"answers": {k: "calm" for k in p["questions"]}})


async def h_out_of_range(p):
    return Resp({"answers": _all_slots(p, choice="banana", score=99, yes_prob=7.0, confidence=9)})


async def h_negative(p):
    return Resp({"answers": _all_slots(p, score=-3, yes_prob=-1.0, confidence=-2)})


async def h_wrong_types(p):
    return Resp({"answers": _all_slots(p, choice=5, score="high", yes_prob=None, confidence="x")})


async def h_nan(p):
    return Resp({"answers": _all_slots(p, score=float("nan"), yes_prob=float("nan"), confidence=float("nan"))})


HOSTILE = [h_500, h_conn, h_hang, h_notjson, h_list, h_no_answers, h_answers_list,
           h_slots_not_dicts, h_out_of_range, h_negative, h_wrong_types, h_nan]


# ── the standards ────────────────────────────────────────────────────────────
async def s1_valid_jev_response():
    """S1 a well-formed JEV answer yields a schema-valid result tagged as JEV."""
    out = []
    for name, (mod, probs, call) in CALLS.items():
        with configured(mod, ok_handler):
            r = await call()
        out += [f"{name}: {p}" for p in probs(r)]
        if not r["source"].startswith("jev"):
            out.append(f"{name}: source={r['source']!r}, expected jev")
    return out


async def s2_offline_makes_no_network_call():
    """S2 no key, or CRANE_JEV=false: deterministic result, zero network calls."""
    out = []
    for name, (mod, probs, call) in CALLS.items():
        for label, kw in (("no key", {"key": ""}), ("disabled", {"enabled": False})):
            with configured(mod, ok_handler, **kw) as fake:
                r = await call()
            if fake.calls:
                out.append(f"{name} ({label}): made {len(fake.calls)} network call(s)")
            out += [f"{name} ({label}): {p}" for p in probs(r)]
            if r.get("source") != "fallback":
                out.append(f"{name} ({label}): source={r.get('source')!r}")
    return out


async def s3_hostile_upstream_never_breaks_contract():
    """S3 5xx, refused, hung, non-JSON, wrong shape, out-of-enum/range/type, NaN: valid output, never raises."""
    out = []
    for name, (mod, probs, call) in CALLS.items():
        for h in HOSTILE:
            with configured(mod, h):
                try:
                    r = await asyncio.wait_for(call(), 5)
                except Exception as exc:
                    out.append(f"{name} / {h.__name__}: raised {exc!r}")
                    continue
            out += [f"{name} / {h.__name__}: {p}" for p in probs(r)]
    return out


async def s4_hard_latency_deadline():
    """S4 a hung upstream cannot hold a turn: result within timeout + 1 s, marked fallback."""
    out = []
    for name, (mod, probs, call) in CALLS.items():
        with configured(mod, h_hang, timeout=0.2):
            t0 = time.monotonic()
            r = await asyncio.wait_for(call(), 10)
            dt = time.monotonic() - t0
        if dt > 1.2:
            out.append(f"{name}: took {dt:.1f}s with a 0.2 s timeout")
        if not r.get("source", "").startswith("fallback") and r.get("source") != "fallback":
            out.append(f"{name}: source={r.get('source')!r} after a hang")
    return out


async def s5_fallbacks_are_deterministic():
    """S5 same input, same output; no randomness or clock in the fallback."""
    out = []
    for name, (mod, probs, call) in CALLS.items():
        with configured(mod, key=""):
            a, b = await call(), await call()
        if a != b:
            out.append(f"{name}: two identical calls differ: {a} vs {b}")
    return out


async def s6_inputs_from_clients_cannot_crash():
    """S6 null/garbage values from the browser (AUs, prosody, telemetry) still give a valid result."""
    rng = random.Random(7)
    junk = [None, "x", -5, 0, 0.3, 1, 1e9, float("nan"), True, [], {}, {"a": 1}]

    def d(keys):
        return rng.choice([None, {}, {k: rng.choice(junk) for k in keys if rng.random() < .8}])

    out = []
    for _ in range(150):
        calls = {
            "face": (face, problems_face, lambda: face.check(d(["AU1", "AU2", "AU4", "AU6", "AU7", "AU12"]), d(["mouthSmileLeft", "browDownLeft"]))),
            "voice": (voice, problems_voice, lambda: voice.check(d(["pitch_hz", "energy_db", "rate_wpm"]), d(["rate_wpm"]), rng.choice([None, "", "Wow!", 5]))),
            "persona": (persona, problems_persona, lambda: persona.check(d(["match"]), d(["stress", "contradicts_words"]), rng.choice([None, [], [1, 2]]), d(["name"]), rng.choice([None, "", "hi"]))),
            "director": (director, problems_director, lambda: director.direct(d(["action"]), d(["emotion"]), rng.choice([None, "", "hi"]))),
            "verify": (verify, problems_verify, lambda: verify.check(d(["stated_stage", "duplug_state", "audio_energy_rms"]), d(["changed_pixel_area", "occluding_layer"]))),
        }
        for name, (mod, probs, call) in calls.items():
            with configured(mod, key=""):
                try:
                    r = await call()
                except Exception as exc:
                    out.append(f"{name}: raised {exc!r}")
                    continue
            out += [f"{name}: {p}" for p in probs(r)]
        if len(out) > 12:
            break
    return sorted(set(out))[:12]


async def s7_persona_gate_semantics():
    """S7 persona gate: yield on contradiction, soften on stress+mismatch, continue otherwise; JEV can't invent actions."""
    out = []
    with configured(persona, key=""):
        cases = [
            ({"match": .9}, {"stress": .1, "contradicts_words": .9}, "yield"),
            ({"match": .2}, {"stress": .9, "contradicts_words": .1}, "soften"),
            ({"match": .9}, {"stress": .2, "contradicts_words": .1}, "continue"),
        ]
        for f, v, want in cases:
            r = await persona.check(f, v, [], {}, "x")
            if r["action"] != want:
                out.append(f"fallback {f}/{v}: action={r['action']!r}, expected {want!r}")

    async def invented(p):
        return Resp({"answers": {"in_character": {"yes_prob": .9}, "tone_ok": {"yes_prob": .9},
                                 "action": {"choice": "explode", "confidence": .99}}})
    with configured(persona, invented):
        r = await persona.check({"match": .9}, {"stress": .2}, [], {}, "x")
    if r["action"] not in V["actions"]:
        out.append(f"JEV invented action {r['action']!r} passed through the gate")
    return out


async def s8_verify_is_deterministic_first_and_fail_closed():
    """S8 JEV may only make VERIFY stricter, never turn a CSS-only/no-motion result green."""
    out = []
    css_only = ({"stated_stage": "L2", "duplug_state": "speaking", "audio_energy_rms": .2},
                {"changed_pixel_area": 0, "frame_diff_mean": 0.0})

    async def cheerleader(p):
        return Resp({"answers": {k: {"yes_prob": .99, "confidence": .99} for k in p["questions"]}})
    for label, kw in (("no JEV", {"key": ""}), ("JEV says yes @0.99", {"handler": cheerleader})):
        with configured(verify, **kw):
            r = await verify.check(*css_only)
        if r["consistent"] or r["is_motion_visible"] or r["telemetry_stage"] != "L0":
            out.append(f"CSS-only motion passed VERIFY ({label}): {r}")

    real = ({"stated_stage": "L2", "duplug_state": "speaking", "audio_energy_rms": .2},
            {"changed_pixel_area": 1500, "frame_diff_mean": 3.0})

    async def skeptic(p):
        return Resp({"answers": {k: {"yes_prob": .01, "confidence": .95} for k in p["questions"]}})
    with configured(verify, skeptic):
        r = await verify.check(*real)
    if r["is_motion_visible"]:
        out.append("JEV downgrade ignored: skeptical high-confidence JEV did not make VERIFY stricter")

    with configured(verify, key=""):
        orig = verify._deterministic_fallback

        def boom(*a, **k):
            raise RuntimeError("x")
        verify._deterministic_fallback = boom
        try:
            r = await verify.check(*real)
        finally:
            verify._deterministic_fallback = orig
    if r.get("consistent") or r.get("is_motion_visible"):
        out.append(f"VERIFY failed open on an internal error: {r}")
    return out


async def s9_fallback_never_scalar_saturated():
    """S9 voice stress fallback: monotonic with loudness and not saturated for normal speech."""
    out = []
    with configured(voice, key=""):
        s = {}
        for e in (-45, -30, -20, -10, -3):
            s[e] = (await voice.check({"energy_db": e, "rate_wpm": 140}, {}, "ok"))["stress"]
    vals = [s[e] for e in (-45, -30, -20, -10, -3)]
    if vals != sorted(vals) or len(set(vals)) < 3:
        out.append(f"stress not increasing with loudness: {s}")
    if s[-20] >= 0.9:
        out.append(f"normal speech (-20 dB) is already 'maximally stressed': {s[-20]}")
    return out


async def s10_enums_shared_by_fallback_and_jev():
    """S10 fallback may only emit values the JEV question set can also emit (one vocabulary)."""
    out = []
    with configured(face, key=""):
        r = await face.check({}, {"browDownLeft": .9})
    if r["beryl_emotion"] not in V["emotions"]:
        out.append(f"face fallback emits {r['beryl_emotion']!r}, outside the JEV vocabulary")
    for emo in ("curious", "excited", "concerned", "serious", "neutral", "warm", "happy"):
        with configured(director, key=""):
            r = await director.direct({"action": "continue"}, {"emotion": emo}, "x")
        out += [f"director fallback for {emo!r}: {p}" for p in problems_director(r)]
    return out


async def s11_data_minimisation_and_secrets():
    """S11 bounded text/history leave the box; the key is only in the Authorization header, never body or logs."""
    out = []
    big = "x" * 5000
    caps = {
        "voice": (voice, lambda: voice.check({}, {}, big), lambda s: s["reply_text"], 600),
        "persona": (persona, lambda: persona.check({}, {}, [{"i": i} for i in range(20)], {}, big), lambda s: s["proposed_reply"], 800),
        "director": (director, lambda: director.direct({}, {}, big), lambda s: s["reply_text"], 400),
    }
    log_buf = []

    class H(logging.Handler):
        def emit(self, rec):
            log_buf.append(rec.getMessage())
    h = H()
    logging.getLogger().addHandler(h)
    try:
        for name, (mod, call, getter, cap) in caps.items():
            with configured(mod, ok_handler) as fake:
                await call()
            _, payload, headers = fake.calls[0]
            if len(getter(payload["state"])) > cap:
                out.append(f"{name}: reply text sent upstream exceeds {cap} chars")
            if KEY in json.dumps(payload):
                out.append(f"{name}: API key appears in the request body")
            if headers.get("Authorization") != f"Bearer {KEY}":
                out.append(f"{name}: Authorization header wrong: {headers}")
            if name == "persona" and len(payload["state"]["recent_history"]) > 6:
                out.append("persona: more than 6 history turns sent upstream")
        for name, (mod, probs, call) in CALLS.items():
            with configured(mod, h_500):
                await call()
    finally:
        logging.getLogger().removeHandler(h)
    if any(KEY in m for m in log_buf):
        out.append("API key found in log output")
    return out


async def s12_jev_is_outside_repair_paths():
    """S12 nothing under deploy/ or harness/controller/ imports JEV or calls TypeSafe (repairs never depend on it)."""
    out = []
    pat = re.compile(r"harness\.jev|from harness import jev|api\.typesafe|TYPESAFE_API|JEV_API_KEY|CRANE_JEV")
    for base in ("deploy", "harness/controller"):
        for p in (ROOT / base).rglob("*"):
            if p.is_file() and p.suffix in (".py", ".sh", ".yaml", ".yml") and p.name != "predeploy.sh":
                for n, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
                    if pat.search(line) and not line.lstrip().startswith(("#", '"""', "JEV is excluded")):
                        out.append(f"{p.relative_to(ROOT)}:{n}: {line.strip()[:90]}")
    return out


async def s13_yaml_is_the_single_source():
    """S13 jev.yaml drives the code: payload questions == spec, neutral results are schema-valid, no vocab literals in modules."""
    out = []
    for name, (mod, probs, call) in CALLS.items():
        with configured(mod, ok_handler) as fake:
            await call()
        sent = fake.calls[0][1]["questions"]
        if sent != questions(name):
            out.append(f"{name}: request questions differ from jev.yaml")
        if set(sent) != set(SPEC["questions"][name]):
            out.append(f"{name}: question keys {sorted(sent)} != spec {sorted(SPEC['questions'][name])}")
        neutral = {**SPEC["neutral"][name], "source": "fallback"}
        out += [f"{name}: neutral result in jev.yaml violates the schema: {p}" for p in probs(neutral)]
    literals = re.compile(r'"(away_think|brow_flash|disgusted|playful|aside_recall|nostril_flare|down_sincere)"')
    for p in sorted((ROOT / "harness/jev").glob("*.py")):
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if literals.search(line):
                out.append(f"{p.name}:{n}: vocabulary literal in code, belongs in jev.yaml: {line.strip()[:70]}")
    return out


STANDARDS = [s1_valid_jev_response, s2_offline_makes_no_network_call, s3_hostile_upstream_never_breaks_contract,
             s4_hard_latency_deadline, s5_fallbacks_are_deterministic, s6_inputs_from_clients_cannot_crash,
             s7_persona_gate_semantics, s8_verify_is_deterministic_first_and_fail_closed,
             s9_fallback_never_scalar_saturated, s10_enums_shared_by_fallback_and_jev,
             s11_data_minimisation_and_secrets, s12_jev_is_outside_repair_paths,
             s13_yaml_is_the_single_source]


async def main() -> int:
    logging.disable(logging.CRITICAL)
    logging.disable(logging.NOTSET)
    logging.getLogger("jev").setLevel(logging.CRITICAL)
    for n in ("face", "voice", "persona", "director", "verify"):
        logging.getLogger(f"jev.{n}").setLevel(logging.CRITICAL)
    failed = 0
    for fn in STANDARDS:
        tag = fn.__name__.split("_")[0].upper()
        try:
            probs = await fn()
        except Exception as exc:
            probs = [f"test crashed: {exc!r}"]
        print(f"  [{'PASS' if not probs else 'FAIL'}] {tag}  {fn.__doc__.split(' ', 1)[1]}")
        for p in probs[:6]:
            print(f"           - {p}")
        if len(probs) > 6:
            print(f"           ... and {len(probs) - 6} more")
        failed += bool(probs)
    print(f"\n{'ALL STANDARDS MET' if not failed else f'{failed} of {len(STANDARDS)} standards NOT met'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
