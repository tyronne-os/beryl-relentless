"""
Offline tests for harness/sensory/ — no real mp4, no Claude API.

T01 standards: thresholds exist and have the right types
T02 triage levels: worst() picks the lowest level
T03 triage_for: maps scalars to levels correctly
T04 triage panel: session_triage = worst instrument
T05 triage JEV lower: JEV can only lower, never raise
T06 triage JEV raise blocked: attempt to raise a red stays red
T07 standards cited: every Standard has a non-empty citation
T08 mouth result: agreement field valid
T09 eye result: triage is in known levels
T10 ear result: triage is in known levels
T11 identity result: triage is in known levels
T12 measure_clip imports: triage.measure_clip importable without ffmpeg crash
T13 enter_keys syntax: deploy/enter_keys.sh parses without error
"""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ── T01 ─────────────────────────────────────────────────────────────────────
def test_t01_standards_types():
    from harness.sensory.standards import (
        BLINK_RATE_MIN, BLINK_RATE_MAX, BLINK_RATE_TARGET,
        LIPSYNC_PASS_MS, LIPSYNC_PEAK_R, IDENTITY_DRIFT_MAX,
        FPS_MIN, FIRST_CHUNK_BUDGET, WPM_MIN, WPM_MAX, WER_MAX,
    )
    for s in (BLINK_RATE_MIN, BLINK_RATE_MAX, BLINK_RATE_TARGET,
              LIPSYNC_PASS_MS, LIPSYNC_PEAK_R, IDENTITY_DRIFT_MAX,
              FPS_MIN, FIRST_CHUNK_BUDGET, WPM_MIN, WPM_MAX, WER_MAX):
        assert isinstance(s.value, (int, float)), f"{s} value not numeric"
        assert s.source in ("published", "project", "engineering"), f"{s} bad source"
    print("T01 PASS")


# ── T02 ─────────────────────────────────────────────────────────────────────
def test_t02_worst():
    from harness.sensory.standards import worst
    assert worst("gold", "red") == "red"
    assert worst("green", "amber") == "amber"
    assert worst("gold", "gold") == "gold"
    assert worst("orange", "red") == "red"
    assert worst("amber", "green", "gold") == "amber"
    print("T02 PASS")


# ── T03 ─────────────────────────────────────────────────────────────────────
def test_t03_triage_for():
    from harness.sensory.standards import triage_for
    # higher_is_better
    assert triage_for(100.0, good=90, warn=70, bad=50) == "gold"
    assert triage_for(80.0,  good=90, warn=70, bad=50) == "green"
    assert triage_for(55.0,  good=90, warn=70, bad=50) == "amber"
    assert triage_for(30.0,  good=90, warn=70, bad=50) == "red"
    assert triage_for(None,  good=90, warn=70, bad=50) == "amber"
    # lower_is_better (e.g. latency)
    assert triage_for(100.0, good=200, warn=400, bad=600, higher_is_better=False) == "gold"
    assert triage_for(350.0, good=200, warn=400, bad=600, higher_is_better=False) == "green"
    assert triage_for(500.0, good=200, warn=400, bad=600, higher_is_better=False) == "amber"
    assert triage_for(800.0, good=200, warn=400, bad=600, higher_is_better=False) == "red"
    print("T03 PASS")


# ── T04 ─────────────────────────────────────────────────────────────────────
def test_t04_session_triage_worst():
    from harness.sensory.triage import Triage, InstrumentLight
    t = Triage()
    t.eye = InstrumentLight("eye", "gold")
    t.ear = InstrumentLight("ear", "green")
    t.mouth = InstrumentLight("mouth", "amber")
    t.identity = InstrumentLight("identity", "red")
    assert t.session_triage() == "red"
    print("T04 PASS")


# ── T05 ─────────────────────────────────────────────────────────────────────
def test_t05_jev_lower():
    from harness.sensory.triage import Triage, InstrumentLight
    t = Triage()
    t.eye = InstrumentLight("eye", "green", "normal")
    t.jev_lower("eye", "amber")
    assert t.eye.light == "amber", f"expected amber, got {t.eye.light}"
    assert "JEV" in t.eye.note
    print("T05 PASS")


# ── T06 ─────────────────────────────────────────────────────────────────────
def test_t06_jev_raise_blocked():
    from harness.sensory.triage import Triage, InstrumentLight
    t = Triage()
    t.eye = InstrumentLight("eye", "red", "failing")
    t.jev_lower("eye", "gold")   # attempt to raise — must be blocked
    assert t.eye.light == "red", f"JEV raised a red! got {t.eye.light}"
    print("T06 PASS")


# ── T07 ─────────────────────────────────────────────────────────────────────
def test_t07_standards_cited():
    import harness.sensory.standards as std
    from harness.sensory.standards import Standard
    for name in dir(std):
        obj = getattr(std, name)
        if isinstance(obj, Standard):
            assert obj.citation.strip(), f"{name} has no citation"
    print("T07 PASS")


# ── T08 ─────────────────────────────────────────────────────────────────────
def test_t08_mouth_result_fields():
    from harness.sensory.mouth import MouthResult
    r = MouthResult()
    d = r.to_dict()
    assert "offset_ms" in d
    assert "agreement" in d
    assert "triage" in d
    from harness.sensory.standards import TRIAGE_LEVELS
    assert r.triage in TRIAGE_LEVELS
    print("T08 PASS")


# ── T09 ─────────────────────────────────────────────────────────────────────
def test_t09_eye_result_fields():
    from harness.sensory.eye import EyeResult
    from harness.sensory.standards import TRIAGE_LEVELS
    r = EyeResult()
    assert r.triage in TRIAGE_LEVELS
    d = r.to_dict()
    assert "blink_count" in d and "blink_rate_per_min" in d
    print("T09 PASS")


# ── T10 ─────────────────────────────────────────────────────────────────────
def test_t10_ear_result_fields():
    from harness.sensory.ear import EarResult
    from harness.sensory.standards import TRIAGE_LEVELS
    r = EarResult()
    assert r.triage in TRIAGE_LEVELS
    d = r.to_dict()
    assert "wpm" in d and "clipping_fraction" in d
    print("T10 PASS")


# ── T11 ─────────────────────────────────────────────────────────────────────
def test_t11_identity_result_fields():
    from harness.sensory.identity import IdentityResult
    from harness.sensory.standards import TRIAGE_LEVELS
    r = IdentityResult()
    assert r.triage in TRIAGE_LEVELS
    d = r.to_dict()
    assert "drift" in d and "drift_max" in d
    print("T11 PASS")


# ── T12 ─────────────────────────────────────────────────────────────────────
def test_t12_measure_clip_importable():
    # Import must succeed without ffmpeg present
    from harness.sensory.triage import measure_clip
    assert callable(measure_clip)
    print("T12 PASS")


# ── T13 ─────────────────────────────────────────────────────────────────────
def test_t13_enter_keys_syntax():
    script = ROOT / "deploy" / "enter_keys.sh"
    assert script.exists(), "deploy/enter_keys.sh not found"
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True)
    assert r.returncode == 0, f"bash -n failed: {r.stderr.decode()}"
    print("T13 PASS")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_t")]
    failures = 0
    for fn in tests:
        try:
            fn()
        except Exception as e:
            print(f"{fn.__name__} FAIL: {e}")
            failures += 1
    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")
