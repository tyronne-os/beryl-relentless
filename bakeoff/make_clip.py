"""
make_clip.py — photo in -> speaking avatar mp4 out, via the L2 render node.

Usage (tunnel open, i.e. after ./deploy/gpu_on.sh):
  .venv/bin/python bakeoff/make_clip.py [--photo P] [--audio A] [--out OUT.mp4]
Defaults: bakeoff/fixtures/reference.jpg + bakeoff/fixtures/hello_beryl.wav -> bakeoff/results/clip_<ts>.mp4
"""
import argparse
import base64
import sys
import time
from pathlib import Path

import httpx

FIXTURES = Path(__file__).parent / "fixtures"
RESULTS = Path(__file__).parent / "results"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-url", default="http://localhost:9523")
    ap.add_argument("--photo", default=str(FIXTURES / "reference.jpg"))
    ap.add_argument("--audio", default=str(FIXTURES / "hello_beryl.wav"))
    ap.add_argument("--out", default=str(RESULTS / f"clip_{time.strftime('%Y%m%d_%H%M%S')}.mp4"))
    a = ap.parse_args()

    for f in (a.photo, a.audio):
        if not Path(f).exists():
            sys.exit(f"missing input: {f}")

    t0 = time.monotonic()
    with httpx.Client(timeout=600.0) as c:
        r = c.post(f"{a.render_url}/render", json={
            "photo_b64": base64.b64encode(Path(a.photo).read_bytes()).decode(),
            "audio_b64": base64.b64encode(Path(a.audio).read_bytes()).decode(),
            "save_mp4": True,
        })
        r.raise_for_status()
        meta = r.json()
        if "video_url" not in meta:
            sys.exit(f"render returned no video (model={meta.get('model')}, error={meta.get('error')})")
        v = c.get(f"{a.render_url}{meta['video_url']}")
        v.raise_for_status()

    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_bytes(v.content)
    print(f"model={meta['model']} frames={meta['frames']} video_s={meta['video_s']} "
          f"fps={meta['fps']} first_chunk_ms={meta['latency_ms']} encode_ms={meta['encode_ms']} "
          f"wall_s={time.monotonic() - t0:.1f}")
    print(f"wrote {a.out} ({len(v.content) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
