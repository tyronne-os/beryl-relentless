"""
Deterministic evidence for the self-test (numpy + ffmpeg + PIL). Everything here is measured, never judged.
"""
import io
import math
import re
import subprocess
import wave

import numpy as np

from harness.nodes.verify import lipsync

_HOP = 0.010
_WIN = 0.025


# ── audio ────────────────────────────────────────────────────────────────────
def read_wav(data: bytes):
    """WAV bytes -> (float32 mono in [-1,1], sample_rate). Raw bytes are treated as 24 kHz PCM16 (Kokoro pcm)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            sr, ch, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
        if width != 2:
            raise ValueError(f"only 16-bit wav supported, got {8 * width}-bit")
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        return (x.reshape(-1, ch).mean(axis=1) if ch > 1 else x), sr
    except wave.Error:
        usable = len(data) // 2 * 2
        return np.frombuffer(data[:usable], dtype="<i2").astype(np.float32) / 32768.0, 24000


def to_pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def frame_db(x: np.ndarray, sr: int):
    n_hop, n_win = max(1, int(sr * _HOP)), max(2, int(sr * _WIN))
    if len(x) < n_win:
        return np.zeros(0), np.zeros(0)
    k = 1 + (len(x) - n_win) // n_hop
    idx = np.arange(n_win)[None, :] + n_hop * np.arange(k)[:, None]
    rms = np.sqrt(np.mean(x[idx] ** 2, axis=1))
    return (np.arange(k) * n_hop + n_win / 2) / sr, 20 * np.log10(rms + 1e-6)


def _f0_std_semitones(x: np.ndarray, sr: int, voiced_t: np.ndarray):
    """Crude autocorrelation pitch spread over voiced frames. None when there is too little voiced audio."""
    n = int(sr * 0.04)
    lo, hi = int(sr / 400), int(sr / 70)
    f0 = []
    for t in voiced_t[:: max(1, len(voiced_t) // 200)]:
        i = int(t * sr) - n // 2
        if i < 0 or i + n > len(x):
            continue
        seg = x[i:i + n] - x[i:i + n].mean()
        if np.max(np.abs(seg)) < 1e-4:
            continue
        ac = np.correlate(seg, seg, mode="full")[n - 1:]
        if ac[0] <= 0:
            continue
        lag = lo + int(np.argmax(ac[lo:hi]))
        if ac[lag] / ac[0] > 0.3:
            f0.append(sr / lag)
    if len(f0) < 10:
        return None
    f0 = np.array(f0)
    return float(np.std(12 * np.log2(f0 / np.median(f0))))


def audio_metrics(x: np.ndarray, sr: int, text: str = "") -> dict:
    t, db = frame_db(x, sr)
    dur = len(x) / sr if sr else 0.0
    out = {"duration_s": round(dur, 2), "clipping_fraction": round(float(np.mean(np.abs(x) >= 0.999)), 5) if len(x) else 0.0}
    if len(db) == 0:
        return {**out, "voiced_s": 0.0, "wpm": None, "lead_silence_s": None, "trail_silence_s": None,
                "energy_range_db": None, "f0_std_semitones": None, "pause_count": 0, "longest_pause_s": 0.0}
    thresh = max(db.max() - 35, -55)
    voiced = db > thresh
    if not voiced.any():
        return {**out, "voiced_s": 0.0, "wpm": None, "lead_silence_s": round(dur, 2), "trail_silence_s": round(dur, 2),
                "energy_range_db": None, "f0_std_semitones": None, "pause_count": 0, "longest_pause_s": 0.0}
    first, last = int(np.argmax(voiced)), len(voiced) - 1 - int(np.argmax(voiced[::-1]))
    lead, trail = t[first] - _WIN / 2, dur - (t[last] + _WIN / 2)
    speech_s = max(1e-3, dur - lead - trail)
    words = len(re.findall(r"[A-Za-z0-9']+", text))
    pauses, run = [], 0
    for v in voiced[first:last + 1]:
        if not v:
            run += 1
        else:
            if run * _HOP >= 0.15:
                pauses.append(run * _HOP)
            run = 0
    vdb = db[voiced]
    return {**out, "voiced_s": round(float(voiced.sum() * _HOP), 2),
            "wpm": round(words / speech_s * 60, 1) if words else None,
            "lead_silence_s": round(float(max(lead, 0)), 2), "trail_silence_s": round(float(max(trail, 0)), 2),
            "energy_range_db": round(float(np.percentile(vdb, 90) - np.percentile(vdb, 10)), 1),
            "f0_std_semitones": (lambda v: None if v is None else round(v, 2))(_f0_std_semitones(x, sr, t[voiced])),
            "pause_count": len(pauses), "longest_pause_s": round(max(pauses), 2) if pauses else 0.0}


def wer(reference: str, hypothesis: str) -> float:
    norm = lambda s: re.findall(r"[a-z0-9']+", s.lower())  # noqa: E731
    r, h = norm(reference), norm(hypothesis)
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
            prev = cur
    return round(d[len(h)] / len(r), 3)


# ── video ────────────────────────────────────────────────────────────────────
def frozen_run(path: str) -> int:
    """Longest run of identical consecutive frames (a frozen or stuttering render)."""
    frames, _ = lipsync.decode_video_gray(path)
    if len(frames) < 2:
        return len(frames)
    same = np.abs(np.diff(frames, axis=0)).max(axis=(1, 2)) < 0.5
    best = run = 0
    for s in same:
        run = run + 1 if s else 0
        best = max(best, run)
    return best + 1 if best else 1


def _grab(path: str, t: float, height: int):
    from PIL import Image
    r = subprocess.run([lipsync.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(t, 0):.3f}", "-i", path,
                        "-frames:v", "1", "-vf", f"scale=-2:{height}", "-f", "image2pipe", "-vcodec", "png", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        return None
    return Image.open(io.BytesIO(r.stdout)).convert("RGB")


def pick_moments(path: str):
    """Loud, quiet, start and end moments chosen from the clip's own audio. Returns [(t, kind, db)]."""
    audio, a0 = lipsync.decode_audio(path)
    if audio is None or len(audio) == 0:
        return []
    t, db = frame_db(audio, 16000)
    t = t + a0
    dur = float(t[-1]) if len(t) else 0.0

    def choose(order, n, gap):
        got = []
        for i in order:
            if all(abs(t[i] - t[j]) >= gap for j in got):
                got.append(i)
            if len(got) == n:
                break
        return got

    loud = choose(np.argsort(-db), 3, 0.6)
    margin = (t > 0.3) & (t < dur - 0.3)
    quiet = choose([i for i in np.argsort(db) if margin[i]], 3, 0.6)
    m = [(float(t[i]), "loud", float(db[i])) for i in sorted(loud)]
    m += [(float(t[i]), "quiet", float(db[i])) for i in sorted(quiet)]
    for tt, kind in ((0.15, "start"), (max(dur - 0.25, 0.2), "end")):
        j = int(np.argmin(np.abs(t - tt)))
        m.append((tt, kind, float(db[j])))
    return m


def _label(img, text):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, img.width, 14], fill=(0, 0, 0))
    d.text((3, 2), text, fill=(255, 255, 255))
    return img


def contact_sheet(path: str, size: int = 256) -> bytes | None:
    from PIL import Image
    moments = pick_moments(path)
    tiles = []
    for tt, kind, db in moments:
        im = _grab(path, tt, size)
        if im is not None:
            tiles.append(_label(im, f"t={tt:.2f}s {kind} audio={db:.0f}dB"))
    if not tiles:
        return None
    cols = 4
    w, h = max(i.width for i in tiles), max(i.height for i in tiles)
    sheet = Image.new("RGB", (cols * w, math.ceil(len(tiles) / cols) * h), (20, 20, 20))
    for n, im in enumerate(tiles):
        sheet.paste(im, ((n % cols) * w, (n // cols) * h))
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()


def loudest_strip(path: str, n: int = 6, fps: float = 25.0, size: int = 192) -> bytes | None:
    from PIL import Image
    loud = [m for m in pick_moments(path) if m[1] == "loud"]
    if not loud:
        return None
    t0 = max(loud[0][0] - 2 / fps, 0)
    tiles = [im for im in (_grab(path, t0 + k / fps, size) for k in range(n)) if im is not None]
    if not tiles:
        return None
    strip = Image.new("RGB", (sum(i.width for i in tiles), max(i.height for i in tiles)))
    x = 0
    for k, im in enumerate(tiles):
        strip.paste(_label(im, f"+{k * 1000 / fps:.0f}ms"), (x, 0))
        x += im.width
    buf = io.BytesIO()
    strip.save(buf, "PNG")
    return buf.getvalue()


def degrade_clip(src: str, dst: str, kind: str) -> str:
    """Make a known-bad variant of a good clip so the judge can be calibrated on it."""
    ff = lipsync.ffmpeg_exe()
    base = [ff, "-y", "-hide_banner", "-loglevel", "error"]
    if kind == "av_shift":
        cmd = base + ["-i", src, "-itsoffset", "0.4", "-i", src, "-map", "0:v:0", "-map", "1:a:0",
                      "-c:v", "copy", "-c:a", "aac", dst]
    elif kind == "freeze":
        cmd = base + ["-i", src, "-vf", "fps=3,fps=25", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", dst]
    elif kind == "blur":
        cmd = base + ["-i", src, "-vf", "gblur=sigma=10", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", dst]
    else:
        raise ValueError(f"unknown degradation {kind!r}")
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg {kind} failed: {r.stderr.decode(errors='replace')[-300:]}")
    return dst
