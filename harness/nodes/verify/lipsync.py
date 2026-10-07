"""
Lip-sync offset measured from the rendered mp4 itself (what the viewer sees and hears).

Method (deterministic, no model weights, numpy + ffmpeg only):
  video  m_i = motion energy of the mouth/jaw region between frames i-1 and i
  audio  a(t) = |d/dt| of the speech envelope (dB), averaged over the same frame interval
  offset = lag (ms) that maximises Pearson r between m_i and a shifted by that lag.
Positive offset = video is LATE relative to audio.

Timestamps come from ffmpeg showinfo (real pts), so encoder/mux delay is included.
This is a motion proxy for mouth aperture; landmark-based aperture is the accuracy upgrade.
"""
import re
import shutil
import subprocess
import sys

LIPSYNC_MAX_MS = 133.0   # acceptance: |offset| within this
MIN_PEAK_R = 0.45        # below this the correlation is not trusted -> offset reported as None
MIN_SPEECH_S = 0.8
LAG_RANGE_S = 0.80
LAG_STEP_S = 0.005
_SIZE = 192
_SR = 16000
_HOP = 0.005
_WIN = 0.020


class LipsyncUnavailable(RuntimeError):
    pass


def lipsync_ok(offset_ms) -> bool:
    return offset_ms is not None and abs(offset_ms) <= LIPSYNC_MAX_MS


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise LipsyncUnavailable(
            "ffmpeg not found: run `pip install imageio-ffmpeg` (no sudo) or `sudo apt install ffmpeg`")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True)


def _decode_video(path: str):
    import numpy as np
    base = [_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "info", "-i", str(path), "-map", "0:v:0",
            "-vf", f"scale={_SIZE}:{_SIZE},format=gray,showinfo"]
    tail = ["-f", "rawvideo", "-pix_fmt", "gray", "-"]
    r = _run(base + ["-fps_mode", "passthrough"] + tail)
    if r.returncode != 0:
        r = _run(base + ["-vsync", "0"] + tail)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg video decode failed: {r.stderr.decode(errors='replace')[-300:]}")
    pts = [float(x) for x in re.findall(r"pts_time:\s*(-?[\d.]+)", r.stderr.decode(errors="replace"))]
    frames = np.frombuffer(r.stdout, dtype=np.uint8)
    frames = frames[: len(frames) // (_SIZE * _SIZE) * _SIZE * _SIZE].reshape(-1, _SIZE, _SIZE)
    n = min(len(frames), len(pts))
    return frames[:n].astype(np.float32), np.array(pts[:n], dtype=np.float64)


def _decode_audio(path: str):
    import numpy as np
    r = _run([_ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "info", "-i", str(path), "-map", "0:a:0",
              "-af", "ashowinfo", "-ac", "1", "-ar", str(_SR), "-f", "f32le", "-"])
    if r.returncode != 0:
        return None, 0.0
    m = re.search(r"pts_time:\s*(-?[\d.]+)", r.stderr.decode(errors="replace"))
    start = float(m.group(1)) if m else 0.0
    return np.frombuffer(r.stdout, dtype=np.float32).copy(), start


def _audio_activity(x):
    """Return (times_s relative to first sample, |d envelope/dt| per hop, speech_seconds)."""
    import numpy as np
    n_hop, n_win = int(_SR * _HOP), int(_SR * _WIN)
    if len(x) < n_win * 4:
        return np.zeros(0), np.zeros(0), 0.0
    k = 1 + (len(x) - n_win) // n_hop
    idx = np.arange(n_win)[None, :] + n_hop * np.arange(k)[:, None]
    rms = np.sqrt(np.mean(x[idx] ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-6)
    top = np.percentile(db, 99)
    env = np.clip(db, top - 35, top)
    env = np.convolve(env, np.ones(5) / 5, mode="same")
    act = np.abs(np.gradient(env, _HOP))
    t = (np.arange(k) * n_hop + n_win / 2) / _SR
    speech_s = float(np.sum(db > max(top - 25, -55)) * _HOP)
    return t, act, speech_s


def _mouth_signal(frames):
    """Motion energy per frame interval over the most active pixels of the lower-centre face box."""
    import numpy as np
    d = np.abs(np.diff(frames, axis=0))
    h, w = frames.shape[1:]
    r0, r1, c0, c1 = int(.55 * h), int(.95 * h), int(.25 * w), int(.75 * w)
    box = d[:, r0:r1, c0:c1]
    activity = box.mean(axis=0)
    mask = activity >= np.percentile(activity, 85)
    return box[:, mask].mean(axis=1)


def measure_file(path: str) -> dict:
    out = {"offset_ms": None, "ok": False, "peak_r": None, "raw_offset_ms": None, "out_of_range": False,
           "n_frames": 0, "speech_s": 0.0, "reason": None,
           "method": "speech-envelope-rate vs mouth-motion-energy cross-correlation"}
    try:
        try:
            import numpy as np
        except ImportError:
            raise LipsyncUnavailable("numpy missing: run `.venv/bin/pip install numpy`")
        frames, pts = _decode_video(path)
        out["n_frames"] = int(len(frames))
        if len(frames) < 25:
            out["reason"] = f"too few video frames ({len(frames)})"
            return out
        audio, a0 = _decode_audio(path)
        if audio is None or len(audio) == 0:
            out["reason"] = "no audio stream in clip"
            return out
        ta, act, speech_s = _audio_activity(audio)
        out["speech_s"] = round(speech_s, 2)
        if speech_s < MIN_SPEECH_S:
            out["reason"] = f"not enough speech to measure ({speech_s:.2f} s)"
            return out

        m = _mouth_signal(frames)
        t_hi, t_lo = pts[1:], pts[:-1]
        ta = ta + a0
        cum = np.concatenate([[0.0], np.cumsum((act[:-1] + act[1:]) / 2 * np.diff(ta))])
        lo, hi = ta[0] + LAG_RANGE_S, ta[-1] - LAG_RANGE_S
        keep = (t_lo >= lo) & (t_hi <= hi)
        if keep.sum() < 20:
            out["reason"] = "clip too short for the lag search window"
            return out
        m, t_lo, t_hi = m[keep], t_lo[keep], t_hi[keep]
        dur = t_hi - t_lo

        lags = np.arange(-LAG_RANGE_S, LAG_RANGE_S + 1e-9, LAG_STEP_S)
        mz = (m - m.mean()) / (m.std() + 1e-9)
        r = np.empty(len(lags))
        for j, tau in enumerate(lags):
            a = (np.interp(t_hi - tau, ta, cum) - np.interp(t_lo - tau, ta, cum)) / dur
            az = (a - a.mean()) / (a.std() + 1e-9)
            r[j] = float(np.mean(mz * az))

        j = int(np.argmax(r))
        delta = 0.0
        if 0 < j < len(r) - 1:
            den = r[j - 1] - 2 * r[j] + r[j + 1]
            if den < 0:
                delta = 0.5 * (r[j - 1] - r[j + 1]) / den
        offset_ms = round(float((lags[j] + delta * LAG_STEP_S) * 1000), 1)
        out["peak_r"] = round(float(r[j]), 3)
        out["raw_offset_ms"] = offset_ms
        if r[j] < MIN_PEAK_R:
            out["reason"] = f"weak audio/mouth correlation (peak r={r[j]:.2f} < {MIN_PEAK_R}); offset not trusted"
            return out
        if j in (0, len(r) - 1):
            out["out_of_range"] = True
            out["reason"] = (f"audio and video are at least {LAG_RANGE_S * 1000:.0f} ms apart "
                             f"(best lag at the edge of the search window); exact offset not measurable")
            return out
        out["offset_ms"] = offset_ms
        out["ok"] = lipsync_ok(offset_ms)
        return out
    except LipsyncUnavailable as exc:
        out["reason"] = str(exc)
        return out
    except Exception as exc:
        out["reason"] = f"measurement error: {exc!r}"
        return out


# Public names for sibling packages (selftest) so they do not import private helpers.
ffmpeg_exe = _ffmpeg
decode_audio = _decode_audio
decode_video_gray = _decode_video


if __name__ == "__main__":
    import json
    if len(sys.argv) != 2:
        sys.exit("usage: python -m harness.nodes.verify.lipsync CLIP.mp4")
    res = measure_file(sys.argv[1])
    print(json.dumps(res, indent=2))
    sys.exit(0 if res["offset_ms"] is not None else 1)
