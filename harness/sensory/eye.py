"""
Eye instrument — blink rate and blink duration from a rendered mp4.

Method: decode every frame to grayscale, crop the eye region (top-centre strip),
compute the mean pixel value of the eye band. A blink is a local minimum in the
brightness time series that drops below 80% of the surrounding baseline.

Requires: numpy, ffmpeg (or imageio-ffmpeg).
Falls back gracefully when ffmpeg is unavailable: returns not-measured result.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from pathlib import Path

from harness.sensory.standards import (
    BLINK_RATE_MIN, BLINK_RATE_MAX, BLINK_RATE_TARGET,
    triage_for,
)

log = logging.getLogger("sensory.eye")

# Eye region: rows 20–45% of frame height, cols 25–75% of frame width.
_EYE_ROW_LO = 0.20
_EYE_ROW_HI = 0.45
_EYE_COL_LO = 0.25
_EYE_COL_HI = 0.75

_BLINK_DIP   = 0.80   # brightness must drop to < 80% of local max to count
_MIN_DUR_FR  = 2      # blink must be at least 2 frames (≈66 ms at 30 fps)
_MAX_DUR_FR  = 12     # blink must resolve within 12 frames (≈400 ms)


@dataclass
class EyeResult:
    blink_count: int | None = None
    blink_rate_per_min: float | None = None
    blink_durations_ms: list[float] = field(default_factory=list)
    duration_s: float | None = None
    fps: float | None = None
    triage: str = "amber"       # gold | green | amber | orange | red
    note: str = ""
    source: str = "not-measured"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _decode_gray(path: str, max_frames: int = 9000) -> tuple[list, float]:
    """Return (list_of_2d_arrays, fps). Raises on ffmpeg failure."""
    import numpy as np
    from harness.nodes.verify.lipsync import decode_video_gray, ffmpeg_exe
    frames_raw, pts_list, fps = decode_video_gray(path)
    # decode_video_gray returns flat bytes; reshape to 2D (size x size)
    from harness.nodes.verify import lipsync as ls
    size = ls._SIZE
    arrays = [np.frombuffer(f, dtype=np.uint8).reshape(size, size)
              for f in frames_raw[:max_frames]]
    return arrays, fps


def _eye_band(frame, size: int) -> float:
    """Mean brightness of the eye region."""
    import numpy as np
    r0 = int(_EYE_ROW_LO * size)
    r1 = int(_EYE_ROW_HI * size)
    c0 = int(_EYE_COL_LO * size)
    c1 = int(_EYE_COL_HI * size)
    return float(np.mean(frame[r0:r1, c0:c1]))


def _find_blinks(brightness: list[float], fps: float) -> list[int]:
    """Return list of frame indices where a blink starts (local minima)."""
    if len(brightness) < 10:
        return []
    import numpy as np
    arr = np.array(brightness)
    # Smooth with a small window to remove single-frame flicker
    kernel = np.ones(3) / 3
    smooth = np.convolve(arr, kernel, mode='same')

    # Local baseline: rolling max over ±30 frames
    win = 30
    baseline = np.array([smooth[max(0, i - win):i + win + 1].max()
                         for i in range(len(smooth))])

    blinks: list[int] = []
    in_blink = False
    start = 0
    for i in range(1, len(smooth)):
        ratio = smooth[i] / (baseline[i] + 1e-6)
        if not in_blink and ratio < _BLINK_DIP:
            in_blink = True
            start = i
        elif in_blink and ratio >= _BLINK_DIP:
            dur = i - start
            if _MIN_DUR_FR <= dur <= _MAX_DUR_FR:
                blinks.append(start)
            in_blink = False
    return blinks


def measure_file(path: str | Path) -> EyeResult:
    """
    Measure blink rate from a rendered mp4.
    Returns EyeResult with triage light and note.
    """
    path = str(path)
    try:
        frames, fps = _decode_gray(path)
    except Exception as exc:
        log.warning("eye: cannot decode %s: %r", path, exc)
        return EyeResult(note=f"decode failed: {exc!r}", source="not-measured")

    if not frames or fps <= 0:
        return EyeResult(note="no frames decoded", source="not-measured")

    size = frames[0].shape[0]
    brightness = [_eye_band(f, size) for f in frames]
    blink_frames = _find_blinks(brightness, fps)
    duration_s = len(frames) / fps
    count = len(blink_frames)
    rate = count / duration_s * 60.0 if duration_s > 0 else 0.0

    # Blink durations (frames to ms)
    durations_ms = []
    for i in range(len(brightness)):
        if i in blink_frames:
            j = i
            while j < len(brightness) and brightness[j] < 0.80 * (max(brightness[max(0,j-30):j+30]+[1])):
                j += 1
            durations_ms.append((j - i) / fps * 1000)

    # Triage
    target = BLINK_RATE_TARGET.value  # 17
    lo = BLINK_RATE_MIN.value         # 12
    hi = BLINK_RATE_MAX.value         # 21
    if rate < lo or rate > hi:
        triage = "orange"
    elif abs(rate - target) <= 2:
        triage = "gold"
    elif abs(rate - target) <= 4:
        triage = "green"
    else:
        triage = "amber"

    note = f"{count} blinks in {duration_s:.1f}s = {rate:.1f}/min (target {target})"
    return EyeResult(
        blink_count=count,
        blink_rate_per_min=round(rate, 1),
        blink_durations_ms=[round(d, 1) for d in durations_ms],
        duration_s=round(duration_s, 2),
        fps=round(fps, 1),
        triage=triage,
        note=note,
        source="pixel-brightness",
    )
