"""
Validates harness/nodes/verify/lipsync.py against synthetic clips with a KNOWN offset.
Run:  python3 tests/test_lipsync.py        (needs numpy + ffmpeg; no GPU, no network)
"""
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.nodes.verify import lipsync  # noqa: E402

SR, FPS, SIZE = 16000, 25, 256
TOL_MS = 20.0


def _syllables(seed: int, dur: float):
    rng = np.random.default_rng(seed)
    t, out = 0.4, []
    while t < dur - 0.6:
        for _ in range(int(rng.integers(2, 6))):
            w = rng.uniform(0.07, 0.13)
            out.append((t + w, w, rng.uniform(0.6, 1.0)))
            t += rng.uniform(0.16, 0.30)
        t += rng.uniform(0.25, 0.6)
    return out


def _env(sylls, t):
    e = np.zeros_like(t)
    for c, w, a in sylls:
        x = np.clip((t - c) / w, -1, 1)
        e += a * 0.5 * (1 + np.cos(np.pi * x)) * (np.abs(t - c) < w)
    return np.clip(e, 0, 1)


def _write_wav(path, x):
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1), wf.setsampwidth(2), wf.setframerate(SR)
        wf.writeframes((np.clip(x, -1, 1) * 32000).astype(np.int16).tobytes())


def make_clip(out: Path, dur=8.0, audio_seed=1, video_seed=1, offset_ms=0.0, audio_delay_s=0.0, silent=False):
    """Video mouth opening follows the speech envelope delayed by offset_ms (positive = video late)."""
    rng = np.random.default_rng(99)
    ta = np.arange(int(SR * dur)) / SR
    carrier = sum(np.sin(2 * np.pi * f * ta + rng.uniform(0, 6)) / k for k, f in enumerate((140, 280, 420, 700, 1100), 1))
    carrier = carrier / np.abs(carrier).max() + 0.15 * rng.standard_normal(len(ta))
    audio = np.zeros_like(ta) if silent else 0.6 * carrier * _env(_syllables(audio_seed, dur), ta)

    tf = np.arange(int(FPS * dur)) / FPS
    o = _env(_syllables(video_seed, dur), tf - offset_ms / 1000.0)
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    bg = 128 + 20 * np.sin(xx / 17) * np.cos(yy / 23) + rng.normal(0, 4, (SIZE, SIZE)).astype(np.float32)
    wavp = out.with_suffix(".wav")
    _write_wav(wavp, audio)

    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{SIZE}x{SIZE}",
           "-r", str(FPS), "-i", "-"]
    if audio_delay_s:
        cmd += ["-itsoffset", str(audio_delay_s)]
    cmd += ["-i", str(wavp), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-c:a", "aac", "-shortest", str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i, oi in enumerate(o):
        b = 3 + 22 * oi
        inside = 1 - ((xx - 128) / 40) ** 2 - ((yy - 190) / b) ** 2
        frame = bg - 70 / (1 + np.exp(np.clip(-inside * 12, -50, 50))) + rng.normal(0, 1.5, (SIZE, SIZE))
        p.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
    p.stdin.close()
    assert p.wait() == 0, "ffmpeg encode failed"


def main() -> int:
    fails = 0

    def check(name, cond, detail):
        nonlocal fails
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}: {detail}")
        fails += 0 if cond else 1

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        print("known offsets (positive = video late):")
        for off in (0, 80, -80, 120, -120, 40, 450, -450):
            clip = d / f"o{off}.mp4"
            make_clip(clip, offset_ms=off)
            r = lipsync.measure_file(str(clip))
            got = r["offset_ms"]
            check(f"offset {off:+d} ms", got is not None and abs(got - off) <= TOL_MS,
                  f"measured {got} (r={r['peak_r']}, reason={r['reason']})")

        print("beyond the search window (+-0.8 s) it must refuse, never give a confident wrong number:")
        for far in (900, -900, 1200):
            clip = d / f"far{far}.mp4"
            make_clip(clip, offset_ms=far)
            r = lipsync.measure_file(str(clip))
            check(f"offset {far:+d} ms", r["offset_ms"] is None and not r["ok"],
                  f"offset={r['offset_ms']} r={r['peak_r']} ({r['reason']})")

        print("container timestamps are honoured (audio muxed 100 ms later => video 100 ms early):")
        clip = d / "mux.mp4"
        make_clip(clip, offset_ms=0, audio_delay_s=0.1)
        r = lipsync.measure_file(str(clip))
        check("itsoffset audio +100 ms", r["offset_ms"] is not None and abs(r["offset_ms"] + 100) <= TOL_MS,
              f"measured {r['offset_ms']} (r={r['peak_r']}, reason={r['reason']})")

        print("must refuse to guess:")
        clip = d / "unrelated.mp4"
        make_clip(clip, audio_seed=1, video_seed=7, offset_ms=0)
        r = lipsync.measure_file(str(clip))
        check("unrelated mouth motion", r["offset_ms"] is None, f"offset={r['offset_ms']} r={r['peak_r']} ({r['reason']})")
        clip = d / "silent.mp4"
        make_clip(clip, silent=True)
        r = lipsync.measure_file(str(clip))
        check("silent audio", r["offset_ms"] is None, f"offset={r['offset_ms']} ({r['reason']})")

    print(f"\n{'ALL PASS' if not fails else f'{fails} FAILED'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
