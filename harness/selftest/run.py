"""
python -m harness.selftest.run --stage voice|face|cluster|all [options]

Fix the lowest stage first: with --stage all the run stops at the first stage that does not pass (--keep-going to
override). One change per run: pass --change-note so the ledger can show what that change did.
"""
import argparse
import asyncio
import sys
from pathlib import Path

from harness.selftest import calibration, ledger
from harness.selftest.judge import Judge, JudgeUnavailable
from harness.selftest.services import LiveServices, Recorder
from harness.selftest.spec import SPEC, STAGES
from harness.selftest.stages import run_cluster, run_face, run_voice

ICON = {"pass": "PASS", "fail": "FAIL", "inconclusive": "INCONCLUSIVE", "provisional": "PROVISIONAL (gates only, no judge)"}


def show(res, entry, prev_delta, regressions, calibrated):
    print(f"\n=== {res.stage.upper()}: {ICON[res.status]} ===")
    for g in res.gates:
        mark = "ok  " if g.passed else "FAIL" if g.passed is False else "n/a "
        print(f"  [{mark}] {g.id}: {g.value} (limit {g.limit}) {g.note}")
    j = res.judge
    if j:
        tag = "" if calibrated else "  ** UNCALIBRATED judge: advisory only **"
        print(f"  judge {j.model}: status={j.status} mean={j.mean} min={j.min_score} agreement={j.agreement}"
              f" tokens={j.usage['input_tokens']}/{j.usage['output_tokens']}{' (cached)' if j.cached else ''}{tag}")
        for c, s in j.scores.items():
            print(f"    {c}: {s}  {('- ' + j.issues[c]) if j.issues.get(c) else ''}")
        for c, ok in j.assessable.items():
            if not ok:
                print(f"    {c}: not assessable from this evidence")
        if j.reason:
            print(f"  note: {j.reason}")
    if res.blocking:
        print(f"  blocking: {'; '.join(map(str, res.blocking))}")
    for e in res.errors:
        print(f"  error: {e}")
    if res.fixes:
        print("  fix next (highest priority first, change ONE thing per run):")
        for f in res.fixes[:3]:
            print(f"    {f['priority']:>4}  {f['criterion']} -> {f['node']}: {f['knob']}")
    if prev_delta:
        print(f"  since {prev_delta['from_git']} ({prev_delta['from_note'] or 'no note'}): "
              f"scores {prev_delta['scores']} gate flips {prev_delta['gate_flips']}")
    for r in regressions:
        print(f"  REGRESSION vs baseline: {r}")


async def amain(a) -> int:
    services = Recorder(LiveServices(host=a.host, tts_voice=a.voice))
    judge = None if a.no_judge else Judge(use_cache=not a.no_cache)
    stages = list(STAGES) if a.stage == "all" else [a.stage]

    if a.calibrate:
        if judge is None:
            print("calibration needs the judge (drop --no-judge)")
            return 2
        for st in stages:
            try:
                out = await calibration.calibrate(st, judge, a.good_clip)
            except JudgeUnavailable as exc:
                print(f"judge unavailable: {exc}")
                return 2
            ledger.record_calibration(st, out) if out["margins"] else None
            print(f"calibration {st}: {'PASS' if out['passed'] else 'FAIL'} margins={out['margins']} need>={out.get('need_margin')} {out.get('note', '')}")
            if not out["passed"]:
                return 1
        return 0

    code, entries, baseline = 0, ledger.load(), ledger.load_baseline()
    for st in stages:
        if st == "voice":
            res = await run_voice(services, judge, a.voice)
        elif st == "face":
            res = await run_face(services, judge, a.workdir)
        else:
            res = await run_cluster(services, judge)
        calibrated = ledger.is_calibrated(st, entries)
        prev = ledger.previous(st, entries)
        entry = ledger.record(res, a.change_note, calibrated)
        show(res, entry, ledger.deltas(prev, entry), ledger.ratchet(entry, baseline), calibrated)
        if a.update_baseline:
            try:
                ledger.update_baseline(entry, a.i_reviewed)
                print(f"  baseline for {st} updated")
            except (PermissionError, ValueError) as exc:
                print(f"  baseline NOT updated: {exc}")
        if res.status != "pass":
            code = 1
            if not a.keep_going and len(stages) > 1:
                print(f"\nstopping: fix {st} before moving up the stack (--keep-going to continue)")
                break
    return code


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stage", choices=[*STAGES, "all"], required=True)
    p.add_argument("--host", default="localhost", help="where the tunnelled services answer (default localhost)")
    p.add_argument("--voice", default="af_heart")
    p.add_argument("--change-note", default="", help="the ONE change since the last run (recorded in the ledger)")
    p.add_argument("--calibrate", action="store_true", help="check the judge on known-good vs known-bad evidence")
    p.add_argument("--good-clip", help="face calibration: a clip you consider good; bad variants are derived from it")
    p.add_argument("--no-judge", action="store_true", help="deterministic gates only (no API calls, no cost)")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--keep-going", action="store_true")
    p.add_argument("--workdir", default=str(Path("bakeoff/results/selftest_clips")))
    p.add_argument("--update-baseline", action="store_true")
    p.add_argument("--i-reviewed", action="store_true", help="required with --update-baseline: a human looked at this run")
    return asyncio.run(amain(p.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
