"""
Is the judge fit to judge? Before its scores count, it must tell known-good from known-bad evidence.
Margins are measured, recorded in the ledger, and re-required whenever the judge model or the rubric changes.
"""
from pathlib import Path

from harness.nodes.verify import lipsync
from harness.selftest import evidence as ev
from harness.selftest.judge import Judge
from harness.selftest.spec import SPEC
from harness.selftest.stages import face_evidence_text, voice_evidence_text


def _outcome(stage, margins: dict, good_means: list[float]) -> dict:
    need = SPEC["judge"]["calibration_margin"]
    passed = all(m >= need for m in margins.values()) and all(g >= SPEC["pass"]["mean_min"] for g in good_means)
    return {"stage": stage, "passed": passed, "margins": margins, "need_margin": need,
            "note": "" if passed else "judge did not separate good from bad by the required margin, or rated the known-good evidence below the pass bar"}


async def calibrate_voice(judge: Judge) -> dict:
    c = SPEC["calibration"]["voice"]
    mk = lambda text, m: voice_evidence_text([{"id": "cal", "kind": "scripted", "user": None, "reply": text, "metrics": m, "heard": text}])  # noqa: E731
    good = await judge.judge("voice", mk(c["good_text"], c["good_metrics"]), [])
    bad = await judge.judge("voice", mk(c["bad_text"], c["bad_metrics"]), [])
    if good.mean is None or bad.mean is None:
        return {"stage": "voice", "passed": False, "margins": {}, "note": "judge returned no usable scores"}
    return _outcome("voice", {"overall": round(good.mean - bad.mean, 2)}, [good.mean])


async def calibrate_cluster(judge: Judge) -> dict:
    c = SPEC["calibration"]["cluster"]
    good = await judge.judge("cluster", c["good_report"], [])
    bad = await judge.judge("cluster", c["bad_report"], [])
    if good.mean is None or bad.mean is None:
        return {"stage": "cluster", "passed": False, "margins": {}, "note": "judge returned no usable scores"}
    return _outcome("cluster", {"overall": round(good.mean - bad.mean, 2)}, [good.mean])


def _face_bundle(path: str):
    ls = lipsync.measure_file(path)
    text = face_evidence_text({"id": Path(path).stem, "intended_emotion": "warm"}, ls, ev.frozen_run(path), {})
    images = [(n, b) for n, b in (("contact sheet", ev.contact_sheet(path)), ("loudest-syllable strip", ev.loudest_strip(path))) if b]
    return text, images


async def calibrate_face(judge: Judge, good_clip: str, workdir: str) -> dict:
    """Derive known-bad variants of a good clip with ffmpeg; the judge must score each lower on its target criteria."""
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    text, images = _face_bundle(good_clip)
    good = await judge.judge("face", text, images)
    if good.mean is None:
        return {"stage": "face", "passed": False, "margins": {}, "note": "judge returned no usable scores for the good clip"}
    margins = {}
    for kind, d in SPEC["calibration"]["face"]["degradations"].items():
        bad_path = ev.degrade_clip(good_clip, str(work / f"bad_{kind}.mp4"), kind)
        btext, bimages = _face_bundle(bad_path)
        bad = await judge.judge("face", btext, bimages)
        have = [t for t in d["targets"] if t in good.scores and t in bad.scores]
        margins[kind] = round(sum(good.scores[t] - bad.scores[t] for t in have) / len(have), 2) if have else float("-inf")
    return _outcome("face", margins, [good.mean])


async def calibrate(stage: str, judge: Judge, good_clip: str | None = None, workdir: str = "bakeoff/results/selftest_clips") -> dict:
    if stage == "voice":
        return await calibrate_voice(judge)
    if stage == "cluster":
        return await calibrate_cluster(judge)
    if not good_clip:
        return {"stage": "face", "passed": False, "margins": {}, "note": "face calibration needs --good-clip PATH (a clip you consider good)"}
    return await calibrate_face(judge, good_clip, workdir)
