"""
Ear instrument — audio quality checks from a rendered mp4.

Measures:
- WPM (words per minute from ASR if available, else duration proxy)
- Lead silence (time before first word)
- Clipping fraction (samples at ±1.0)
- Speech / silence ratio
- Energy range (dynamic range of speech segments)

Falls back gracefully when ASR or ffmpeg is unavailable.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass

from harness.sensory.standards import (
    WPM_MIN, WPM_MAX, WER_MAX, LEAD_SILENCE_MAX, CLIPPING_MAX,
    triage_for,
)

log = logging.getLogger("sensory.ear")

_CLIP_THRESHOLD = 0.999   # sample amplitude above this = clipped


@dataclass
class EarResult:
    wpm: float | None = None
    wer: float | None = None
    lead_silence_s: float | None = None
    clipping_fraction: float | None = None
    speech_ratio: float | None = None
    energy_range_db: float | None = None
    duration_s: float | None = None
    triage: str = "amber"
    note: str = ""
    source: str = "not-measured"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _decode_pcm(path: str) -> tuple[list[float], int]:
    """Decode audio track from mp4 to float samples and sample rate."""
    from harness.nodes.verify.lipsync import decode_audio, ffmpeg_exe as _ffe
    samples_i16, sr = decode_audio(path)
    import numpy as np
    arr = np.frombuffer(samples_i16, dtype=np.int16).astype(np.float32) / 32768.0
    return arr.tolist(), sr


def measure_file(path: str, transcript: str | None = None) -> EarResult:
    """
    Measure audio quality from a rendered mp4.
    transcript: if provided, used to compute WPM and is treated as the expected text for WER.
    """
    import numpy as np

    try:
        samples_list, sr = _decode_pcm(str(path))
        samples = np.array(samples_list, dtype=np.float32)
    except Exception as exc:
        log.warning("ear: cannot decode %s: %r", path, exc)
        return EarResult(note=f"decode failed: {exc!r}", source="not-measured")

    if len(samples) == 0:
        return EarResult(note="no audio decoded", source="not-measured")

    duration_s = len(samples) / sr

    # Clipping
    clipped = int(np.sum(np.abs(samples) >= _CLIP_THRESHOLD))
    clip_frac = clipped / len(samples)

    # RMS energy per frame (20 ms windows)
    frame_s = 0.020
    hop = int(frame_s * sr)
    energies = []
    for i in range(0, len(samples) - hop, hop):
        rms = float(np.sqrt(np.mean(samples[i:i + hop] ** 2)))
        energies.append(rms)

    # Lead silence: how long until first active frame (>1% RMS)
    ACTIVE_RMS = 0.01
    lead_frames = 0
    for e in energies:
        if e < ACTIVE_RMS:
            lead_frames += 1
        else:
            break
    lead_silence_s = lead_frames * frame_s

    # Speech / silence ratio
    active_frames = sum(1 for e in energies if e >= ACTIVE_RMS)
    speech_ratio = active_frames / len(energies) if energies else 0.0

    # Energy range (dB) — how dynamic the voice is
    active_rms = [e for e in energies if e >= ACTIVE_RMS]
    if len(active_rms) >= 4:
        lo_db = 20 * np.log10(np.percentile(active_rms, 5) + 1e-9)
        hi_db = 20 * np.log10(np.percentile(active_rms, 95) + 1e-9)
        energy_range_db = float(hi_db - lo_db)
    else:
        energy_range_db = None

    # WPM from transcript and duration
    wpm = None
    if transcript:
        words = len(transcript.split())
        speak_s = duration_s - lead_silence_s
        wpm = words / speak_s * 60.0 if speak_s > 1 else None

    # Triage
    issues = []
    if clip_frac > CLIPPING_MAX.value:
        issues.append(f"clipping {clip_frac:.2%}")
    if lead_silence_s > LEAD_SILENCE_MAX.value:
        issues.append(f"lead silence {lead_silence_s:.2f}s")
    if wpm is not None and not (WPM_MIN.value <= wpm <= WPM_MAX.value):
        issues.append(f"wpm {wpm:.0f} outside {WPM_MIN.value}–{WPM_MAX.value}")

    if issues:
        triage = "orange" if len(issues) >= 2 else "amber"
    elif speech_ratio < 0.3:
        triage = "amber"
    elif energy_range_db is not None and energy_range_db > 15:
        triage = "gold"
    else:
        triage = "green"

    note = ("; ".join(issues) if issues else "ok") + f" | speech_ratio={speech_ratio:.2f}"

    return EarResult(
        wpm=round(wpm, 1) if wpm is not None else None,
        lead_silence_s=round(lead_silence_s, 3),
        clipping_fraction=round(clip_frac, 6),
        speech_ratio=round(speech_ratio, 3),
        energy_range_db=round(energy_range_db, 1) if energy_range_db is not None else None,
        duration_s=round(duration_s, 2),
        triage=triage,
        note=note,
        source="pcm-energy",
    )
