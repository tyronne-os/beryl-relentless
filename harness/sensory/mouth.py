"""
Mouth instrument — lip-sync offset from a rendered mp4.

Two approaches, in order of preference:
  1. Motion-energy cross-correlation (lipsync.py) — fast, no model weights,
     measures pixel change in the mouth region vs speech envelope.
     Reliable for most content; refuses when r < MIN_PEAK_R or clip is out of range.

  2. Aperture proxy (this file) — measures mean brightness of the inner-mouth
     strip (very centre of the lower face) as a coarse aperture signal, then
     cross-correlates with the speech envelope.
     Less precise than landmarks but works without MediaPipe / onnx.
     Used ONLY when approach 1 refuses; kept amber until both approaches agree.

Both use the same ±0.8 s window and 0.45 r threshold from lipsync.py.
Both approaches agreeing = green or gold. Only one agreeing = amber.
Neither agreeing = orange/red.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

from harness.nodes.verify import lipsync as ls
from harness.sensory.standards import LIPSYNC_PASS_MS, LIPSYNC_PEAK_R, triage_for

log = logging.getLogger("sensory.mouth")

_INNER_ROW_LO = 0.62   # inner-mouth strip: lower 60% of face, tight band
_INNER_ROW_HI = 0.72
_INNER_COL_LO = 0.35
_INNER_COL_HI = 0.65


@dataclass
class MouthResult:
    offset_ms: float | None = None
    peak_r: float | None = None
    ok: bool = False
    out_of_range: bool = False
    approach: str = "none"
    agreement: str = "none"   # both | primary | aperture | none
    triage: str = "amber"
    note: str = ""

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _aperture_correlation(path: str):
    """
    Coarse aperture signal from pixel brightness in the inner-mouth strip.
    Returns (offset_ms, peak_r) or (None, None).
    """
    import numpy as np

    try:
        frames_raw, pts_list, fps = ls.decode_video_gray(path)
    except Exception as exc:
        log.debug("aperture: decode failed: %r", exc)
        return None, None

    if not frames_raw or fps <= 0:
        return None, None

    size = ls._SIZE
    r0 = int(_INNER_ROW_LO * size)
    r1 = int(_INNER_ROW_HI * size)
    c0 = int(_INNER_COL_LO * size)
    c1 = int(_INNER_COL_HI * size)

    brightness = []
    for raw in frames_raw:
        try:
            import numpy as _np
            frame = _np.frombuffer(raw, dtype=np.uint8).reshape(size, size)
            brightness.append(float(frame[r0:r1, c0:c1].mean()))
        except Exception:
            brightness.append(0.0)

    # Treat brightness as an "aperture" proxy — more light = more open mouth.
    # Differentiate it to get a rate-of-change signal comparable to the audio envelope.
    arr = np.array(brightness, dtype=np.float32)
    motion = np.abs(np.diff(arr, prepend=arr[0]))

    # Get speech envelope from same file
    try:
        samples_i16, sr = ls.decode_audio(path)
        import numpy as _np2
        pcm = _np2.frombuffer(samples_i16, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as exc:
        log.debug("aperture: audio decode failed: %r", exc)
        return None, None

    # Build speech envelope at the same frame rate as the video
    hop = max(1, int(sr / fps))
    envelope = np.array([
        float(np.sqrt(np.mean(pcm[max(0, i - hop):i + 1] ** 2)))
        for i in range(hop, len(pcm), hop)
    ], dtype=np.float32)

    # Trim / align lengths
    n = min(len(motion), len(envelope))
    if n < 20:
        return None, None
    motion = motion[:n]
    envelope = envelope[:n]

    # Cross-correlation in ±LAG_RANGE_S window
    lag_frames = int(ls.LAG_RANGE_S * fps)
    best_r, best_lag = 0.0, 0
    m_std = motion.std()
    e_std = envelope.std()
    if m_std < 1e-6 or e_std < 1e-6:
        return None, None

    for lag in range(-lag_frames, lag_frames + 1):
        if lag < 0:
            m_ = motion[-lag:]
            e_ = envelope[:lag] if lag else envelope
        elif lag > 0:
            m_ = motion[:-lag]
            e_ = envelope[lag:]
        else:
            m_, e_ = motion, envelope
        if len(m_) < 10:
            continue
        r = float(np.corrcoef(m_, e_)[0, 1])
        if abs(r) > abs(best_r):
            best_r, best_lag = r, lag

    if abs(best_r) < ls.MIN_PEAK_R:
        return None, None

    offset_ms = best_lag / fps * 1000.0
    return offset_ms, best_r


def measure_file(path: str) -> MouthResult:
    """
    Run both lip-sync approaches and return a MouthResult with agreement triage.
    """
    # Approach 1: motion-energy (primary)
    primary = ls.measure_file(str(path))
    p_offset = primary.get("offset_ms")
    p_r = primary.get("peak_r")
    p_ok = primary.get("ok", False)
    p_range = not primary.get("out_of_range", False)

    # Approach 2: aperture proxy (secondary, used when primary refuses)
    a_offset, a_r = None, None
    a_ok = False
    if not p_ok:
        log.debug("mouth: primary refused (r=%.3f, out_of_range=%s) — trying aperture",
                  p_r or 0, primary.get("out_of_range", False))
        a_offset, a_r = _aperture_correlation(str(path))
        a_ok = (a_offset is not None
                and a_r is not None
                and abs(a_offset) <= LIPSYNC_PASS_MS.value)

    # Agreement and triage
    if p_ok and a_ok:
        # Both agree — check they're within 30 ms of each other
        if abs((p_offset or 0) - (a_offset or 0)) <= 30:
            agreement = "both"
            triage = "gold" if abs(p_offset) <= LIPSYNC_PASS_MS.value else "red"
        else:
            agreement = "both-disagree"
            triage = "amber"
        offset_ms = p_offset  # trust primary
        peak_r = p_r
        approach = "motion-energy+aperture"
    elif p_ok:
        agreement = "primary"
        triage = "green" if abs(p_offset) <= LIPSYNC_PASS_MS.value else "orange"
        offset_ms, peak_r = p_offset, p_r
        approach = "motion-energy"
    elif a_ok:
        agreement = "aperture"
        triage = "amber"  # single instrument = amber max
        offset_ms, peak_r = a_offset, a_r
        approach = "aperture"
    else:
        agreement = "none"
        triage = "amber"
        offset_ms, peak_r = None, None
        approach = "none"

    out_of_range = primary.get("out_of_range", False) and not a_ok
    note = (f"offset={offset_ms:.0f}ms r={peak_r:.3f} [{approach}]"
            if offset_ms is not None else
            f"not measured [{approach}] primary_r={p_r:.3f}" if p_r is not None else "not measured")

    return MouthResult(
        offset_ms=round(offset_ms, 1) if offset_ms is not None else None,
        peak_r=round(peak_r, 3) if peak_r is not None else None,
        ok=p_ok or a_ok,
        out_of_range=out_of_range,
        approach=approach,
        agreement=agreement,
        triage=triage,
        note=note,
    )
