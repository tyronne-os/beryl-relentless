"""
The three stages. Each: collect measured evidence -> deterministic gates (primary) -> Claude judges what
numbers cannot -> one verdict plus a ranked fix list. Gates can never be overridden by the judge.
"""
import io
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from harness.nodes.verify import lipsync
from harness.selftest import evidence as ev
from harness.selftest.judge import Judge, JudgeResult, JudgeUnavailable, merge
from harness.selftest.spec import SPEC

log = logging.getLogger("selftest.stages")
ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class Gate:
    id: str
    passed: bool | None          # None = could not be measured (never counts as a pass)
    value: object = None
    limit: object = None
    note: str = ""


@dataclass
class StageResult:
    stage: str
    status: str = "inconclusive"   # pass | fail | inconclusive | provisional (gates only, no judge)
    gates: list = field(default_factory=list)
    judge: JudgeResult | None = None
    blocking: list = field(default_factory=list)
    fixes: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {**{k: v for k, v in asdict(self).items() if k != "judge"}, "judge": self.judge.to_dict() if self.judge else None}


def _le(gid, value, limit, note=""):
    return Gate(gid, None if value is None else value <= limit, value, f"<= {limit}", note)


def _ge(gid, value, limit, note=""):
    return Gate(gid, None if value is None else value >= limit, value, f">= {limit}", note)


def decide(gates: list[Gate], judge: JudgeResult | None) -> tuple[str, list]:
    failed = [g.id for g in gates if g.passed is False]
    if failed:
        return "fail", failed
    unknown = [g.id for g in gates if g.passed is None]
    if unknown:
        return "inconclusive", [f"not measured: {u}" for u in unknown]
    if judge is None:
        return "provisional", []
    if judge.status != "scored":
        return "inconclusive", [judge.reason]
    p = SPEC["pass"]
    low = [c for c, s in judge.scores.items() if s < p["criterion_min"]]
    if judge.mean < p["mean_min"] or low:
        return "fail", [f"judge mean {judge.mean} (need {p['mean_min']})"] + [f"{c}={judge.scores[c]}" for c in low]
    return "pass", []


def _note(services, method, *args):
    fn = getattr(services, method, None)
    if callable(fn):
        fn(*args)


async def _judge(judge, stage, text, images, res: StageResult):
    if judge is None:
        return None
    try:
        return await judge.judge(stage, text, images)
    except JudgeUnavailable as exc:
        res.errors.append(f"judge unavailable: {exc}")
        return None


def _finish(res: StageResult, judge_res: JudgeResult | None) -> StageResult:
    res.judge = judge_res
    res.status, res.blocking = decide(res.gates, judge_res)
    res.fixes = list(judge_res.fixes) if judge_res else []
    return res


# ── stage 1: vernacular and voice delivery ───────────────────────────────────
def voice_evidence_text(items: list[dict]) -> str:
    out = []
    for it in items:
        head = f"ITEM {it['id']} ({'scripted line, TTS only' if it['kind'] == 'scripted' else 'LLM reply then TTS'})"
        lines = [head]
        if it.get("user"):
            lines.append(f'  user said: "{it["user"]}"')
        lines.append(f'  text spoken: "{it["reply"]}"')
        lines.append(f"  measured delivery: {json.dumps(it.get('metrics'))}")
        if it.get("heard") is not None:
            lines.append(f'  ASR heard (round trip): "{it["heard"]}"')
        out.append("\n".join(lines))
    return "\n\n".join(out)


async def run_voice(services, judge: Judge | None, voice: str | None = None) -> StageResult:
    res = StageResult("voice")
    fx = SPEC["stages"]["voice"]["fixtures"]
    items = [{"id": s["id"], "kind": "scripted", "user": None, "reply": s["text"], "llm_ms": 0.0} for s in fx["scripted_lines"]]
    for turn in fx["llm_turns"]:
        t0 = time.monotonic()
        try:
            reply = await services.llm_reply(turn["user"])
        except Exception as exc:
            res.errors.append(f"llm {turn['id']}: {exc!r}")
            continue
        items.append({"id": turn["id"], "kind": "llm", "user": turn["user"], "reply": reply,
                      "llm_ms": (time.monotonic() - t0) * 1000})

    for it in items:
        t0 = time.monotonic()
        try:
            wav = await services.tts(it["reply"], voice)
        except Exception as exc:
            res.errors.append(f"tts {it['id']}: {exc!r}")
            it["metrics"] = None
            continue
        x, sr = ev.read_wav(wav)
        it["metrics"] = ev.audio_metrics(x, sr, it["reply"])
        if it["kind"] == "llm":
            _note(services, "note_turn", it["llm_ms"] + (time.monotonic() - t0) * 1000)
        if len(x) == 0 or not it["metrics"]["voiced_s"]:
            _note(services, "note_silent_failure", "tts", f"{it['id']}: returned no speech")
        try:
            it["heard"] = await services.asr(wav)
            it["metrics"]["wer"] = ev.wer(it["reply"], it["heard"])
            if not it["heard"].strip() and it["metrics"]["voiced_s"]:
                _note(services, "note_silent_failure", "asr", f"{it['id']}: empty transcript for voiced audio")
        except Exception as exc:
            it["heard"], it["metrics"]["wer"] = None, None
            res.errors.append(f"asr {it['id']}: {exc!r}")

    ms = [it["metrics"] for it in items if it.get("metrics")]

    def col(k, fn):
        v = [m[k] for m in ms if m.get(k) is not None]
        return fn(v) if v else None

    g = SPEC["stages"]["voice"]["gates"]
    res.gates = [
        _le("asr_wer_max", col("wer", max), g["asr_wer_max"], f"{sum(1 for m in ms if m.get('wer') is not None)}/{len(items)} items measured"),
        _le("clipping_fraction_max", col("clipping_fraction", max), g["clipping_fraction_max"]),
        _ge("wpm_min", col("wpm", min), g["wpm_min"]),
        _le("wpm_max", col("wpm", max), g["wpm_max"]),
        _le("lead_silence_s_max", col("lead_silence_s", max), g["lead_silence_s_max"]),
        _ge("min_duration_s", col("duration_s", min), g["min_duration_s"]),
    ]
    if len(ms) < len(items):
        res.gates.append(Gate("all_items_synthesised", False, f"{len(ms)}/{len(items)}", "all", "a TTS or LLM call failed"))
    res.metrics = {"items": len(items), "voice": voice}
    judge_res = await _judge(judge, "voice", voice_evidence_text([i for i in items if i.get("metrics")]), [], res) if ms else None
    return _finish(res, judge_res)


# ── stage 2: lip-sync and facial expression ──────────────────────────────────
def face_evidence_text(clip: dict, ls: dict, frozen: int, meta: dict) -> str:
    emo = SPEC["stages"]["face"]["fixtures"]["emotion_on_demand"]
    return "\n".join([
        f"CLIP {clip['id']}  intended_emotion: {clip['intended_emotion']}",
        "images, in order: (1) reference photo, (2) contact sheet of 8 frames (4 columns; each labelled with its time, "
        "the kind of moment - loud / quiet / start / end - and the audio level at that time), (3) strip of 6 consecutive "
        "frames starting just before the loudest syllable (labelled with milliseconds from the first frame)",
        f"measured lip-sync: {json.dumps({k: ls.get(k) for k in ('offset_ms', 'peak_r', 'reason', 'speech_s')})}  "
        "(positive offset = video late relative to audio)",
        f"measured render: fps={meta.get('fps')} first_chunk_ms={meta.get('latency_ms')} model={meta.get('model')} "
        f"painted={json.dumps(meta.get('painted'))} longest_frozen_run_frames={frozen}",
        f"emotion on demand: {'enabled' if emo['enabled'] else 'not available (' + emo['reason'] + ')'}",
    ])


def _abs_offset(row):
    """|offset| in ms; a measurement pinned at the edge of the search window counts as that large (a fail, not unknown)."""
    ls = row[1]
    if ls["offset_ms"] is not None:
        return abs(ls["offset_ms"])
    return abs(ls["raw_offset_ms"]) if ls.get("out_of_range") and ls["raw_offset_ms"] is not None else None


def _png(path: str, max_side: int = 512) -> bytes:
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


async def run_face(services, judge: Judge | None, workdir: str, clips: list[dict] | None = None) -> StageResult:
    res = StageResult("face")
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    rows, judgements = [], []
    for clip in clips or SPEC["stages"]["face"]["fixtures"]["clips"]:
        photo, audio = ROOT / clip["photo"], ROOT / clip["audio"]
        if not photo.exists() or not audio.exists():
            res.errors.append(f"clip {clip['id']}: fixture missing ({photo.name if not photo.exists() else audio.name})")
            continue
        out = work / f"{clip['id']}.mp4"
        t0 = time.monotonic()
        try:
            meta = await services.render_clip(str(photo), str(audio), str(out))
        except Exception as exc:
            res.errors.append(f"render {clip['id']}: {exc!r}")
            continue
        _note(services, "note_turn", (time.monotonic() - t0) * 1000)
        ls = lipsync.measure_file(str(out))
        frozen = ev.frozen_run(str(out))
        rows.append((clip, ls, frozen, meta))
        sheet, strip = ev.contact_sheet(str(out)), ev.loudest_strip(str(out))
        images = [("reference photo", _png(str(photo)))] + [(n, b) for n, b in (("contact sheet", sheet), ("loudest-syllable strip", strip)) if b]
        if judge is not None:
            j = await _judge(judge, "face", face_evidence_text(clip, ls, frozen, meta), images, res)
            if j is not None:
                judgements.append(j)

    def col(fn, f):
        v = [f(r) for r in rows if f(r) is not None]
        return fn(v) if v and len(v) == len(rows) else None

    g = SPEC["stages"]["face"]["gates"]
    painted_ok = None if not rows else all((r[3].get("painted") or {}).get("changed_pixel_area", 0) > 0 for r in rows)
    res.gates = [
        _le("lipsync_abs_offset_ms_max", col(max, _abs_offset), g["lipsync_abs_offset_ms_max"]),
        _ge("lipsync_peak_r_min", col(min, lambda r: r[1]["peak_r"]), g["lipsync_peak_r_min"]),
        _ge("fps_min", col(min, lambda r: r[3].get("fps")), g["fps_min"]),
        _le("frozen_frame_run_max", col(max, lambda r: r[2]), g["frozen_frame_run_max"]),
        Gate("painted_motion_required", painted_ok, painted_ok, True, "painted-pixel change, never CSS"),
        _le("first_frame_ms_max", col(max, lambda r: r[3].get("latency_ms")), g["first_frame_ms_max"], "tracked; known red today"),
    ]
    if not rows:
        res.errors.append("no clip could be rendered")
    res.metrics = {"clips": [{"id": r[0]["id"], "lipsync": r[1], "frozen_run": r[2], "fps": r[3].get("fps"),
                              "first_chunk_ms": r[3].get("latency_ms"), "model": r[3].get("model")} for r in rows]}
    return _finish(res, merge("face", judgements) if judgements else None)


# ── stage 3: cluster and back-end reaction ───────────────────────────────────
def cluster_report(health: dict, snap: dict, gates: list[Gate], injected: list | None) -> str:
    lines = ["NODE HEALTH", json.dumps(health, indent=1, default=str), "",
             "TIMINGS RECORDED DURING STAGES 1 AND 2", json.dumps(snap, indent=1, default=str), "",
             "GATE RESULTS (measured, authoritative)"]
    lines += [f"  {g.id}: {'PASS' if g.passed else 'FAIL' if g.passed is False else 'NOT MEASURED'} value={g.value} limit={g.limit} {g.note}" for g in gates]
    inj = SPEC["stages"]["cluster"]["inject"]
    lines += ["", "FAULT INJECTION: " + ("not run (disabled; no fault was injected, so recovery cannot be assessed)"
                                         if not inj["enabled"] or not injected else json.dumps(injected))]
    return "\n".join(lines)


async def run_cluster(services, judge: Judge | None, injected: list | None = None) -> StageResult:
    res = StageResult("cluster")
    try:
        health = await services.health()
    except Exception as exc:
        health = {}
        res.errors.append(f"health: {exc!r}")
    snap = services.snapshot() if hasattr(services, "snapshot") else {}
    g = SPEC["stages"]["cluster"]["gates"]
    render = health.get("render", {})
    model = render.get("model")
    unannounced = len(snap.get("silent_failures", [])) + (1 if model == "passthrough" and not render.get("load_error") else 0)
    vram = (render.get("gpu") or {}).get("vram_free_gb")
    res.gates = [
        Gate("required_nodes_healthy", all(health.get(n, {}).get("ok") for n in g["required_nodes_healthy"]) if health else None,
             {n: health.get(n, {}).get("ok") for n in g["required_nodes_healthy"]}, g["required_nodes_healthy"]),
        Gate("render_model_not_passthrough", None if model is None else model != "passthrough", model, "!= passthrough"),
        Gate("render_load_error_absent", None if not render else not render.get("load_error"), render.get("load_error")),
        _le("turn_total_p95_ms_max", (snap.get("turn_ms") or {}).get("p95"), g["turn_total_p95_ms_max"]),
        _le("unannounced_fallbacks_max", unannounced, g["unannounced_fallbacks_max"]),
        _ge("gpu_vram_free_gb_min", vram, g["gpu_vram_free_gb_min"]),
    ]
    res.metrics = {"health": health, "timings": snap}
    judge_res = await _judge(judge, "cluster", cluster_report(health, snap, res.gates, injected), [], res)
    return _finish(res, judge_res)
