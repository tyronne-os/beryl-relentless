"""
Identity instrument — face embedding drift over a rendered clip.

Method: decode the clip to frames, sample one frame per second,
compute a simple perceptual hash (phash) of the face region,
measure the fraction of frames whose hash differs from the reference
(first frame) by more than a threshold.

This is a lightweight proxy for cosine distance between face embeddings.
It does not require InsightFace or any model weights.
Upgrade: swap _phash_distance for an actual embedding distance when
InsightFace license is cleared (docs/LESSONS-LEARNED — open item).
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

from harness.sensory.standards import IDENTITY_DRIFT_MAX, triage_for

log = logging.getLogger("sensory.identity")

# Face region: top-centre of frame
_FACE_ROW_LO = 0.05
_FACE_ROW_HI = 0.65
_FACE_COL_LO = 0.20
_FACE_COL_HI = 0.80

_PHASH_BITS = 64      # 8x8 perceptual hash
_DRIFT_GATE = 0.15    # fraction of bits differing = identity drifted (no model: conservative)
_SAMPLE_INTERVAL_S = 1.0


@dataclass
class IdentityResult:
    drift: float | None = None          # mean bit-distance vs reference frame
    drift_max: float | None = None      # worst-case frame
    sample_count: int = 0
    triage: str = "amber"
    note: str = ""
    source: str = "not-measured"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _phash(crop) -> int:
    """8x8 average hash — returns 64-bit int."""
    import numpy as np
    import struct
    h, w = 8, 8
    thumb = np.array([
        crop[int(r * crop.shape[0] / h):int((r + 1) * crop.shape[0] / h),
             int(c * crop.shape[1] / w):int((c + 1) * crop.shape[1] / w)].mean()
        for r in range(h) for c in range(w)
    ], dtype=np.float32)
    avg = thumb.mean()
    bits = int(sum(1 << i for i, v in enumerate(thumb) if v > avg))
    return bits


def _hamming(a: int, b: int) -> float:
    """Normalised Hamming distance between two 64-bit ints."""
    xor = (a ^ b) & 0xFFFFFFFFFFFFFFFF
    count = bin(xor).count('1')
    return count / 64.0


def measure_file(path: str) -> IdentityResult:
    """Measure identity drift from a rendered mp4."""
    import numpy as np

    try:
        from harness.nodes.verify.lipsync import decode_video_gray, _SIZE as SIZE
        frames_raw, pts_list, fps = decode_video_gray(str(path))
    except Exception as exc:
        log.warning("identity: cannot decode %s: %r", path, exc)
        return IdentityResult(note=f"decode failed: {exc!r}", source="not-measured")

    if not frames_raw or fps <= 0:
        return IdentityResult(note="no frames", source="not-measured")

    # Sample one frame per second
    sample_step = max(1, int(fps * _SAMPLE_INTERVAL_S))
    sampled = frames_raw[::sample_step]

    r0 = int(_FACE_ROW_LO * SIZE)
    r1 = int(_FACE_ROW_HI * SIZE)
    c0 = int(_FACE_COL_LO * SIZE)
    c1 = int(_FACE_COL_HI * SIZE)

    hashes = []
    for raw in sampled:
        try:
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(SIZE, SIZE)
            crop = frame[r0:r1, c0:c1]
            hashes.append(_phash(crop))
        except Exception:
            pass

    if len(hashes) < 2:
        return IdentityResult(note="too few samples", source="not-measured")

    ref = hashes[0]
    dists = [_hamming(ref, h) for h in hashes[1:]]
    drift_mean = float(np.mean(dists))
    drift_max = float(np.max(dists))

    # Triage
    gate = IDENTITY_DRIFT_MAX.value   # 0.15
    if drift_max <= gate * 0.5:
        triage = "gold"
    elif drift_max <= gate:
        triage = "green"
    elif drift_max <= gate * 1.5:
        triage = "amber"
    else:
        triage = "red"

    note = (f"drift_mean={drift_mean:.3f} drift_max={drift_max:.3f} "
            f"n={len(hashes)} gate={gate} [phash proxy — no InsightFace]")

    return IdentityResult(
        drift=round(drift_mean, 4),
        drift_max=round(drift_max, 4),
        sample_count=len(hashes),
        triage=triage,
        note=note,
        source="phash-proxy",
    )
