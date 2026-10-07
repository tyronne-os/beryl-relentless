"""
bakeoff/scorecard_runner.py — fixed fixture runner for L2 acceptance tests.
Runs the Tilly Norwood acceptance checklist against the live render node.

Usage:
  python3 bakeoff/scorecard_runner.py \
      --render-url http://<node-ip>:9523 \
      --verify-url http://localhost:9525 \
      --output bakeoff/results/run.json

Exit 0 = all_green; exit 1 = failures.
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RESULTS_DIR = Path(__file__).parent / "results"


# ── Acceptance test definitions ───────────────────────────────────────────
TESTS = [
    {
        "id": "no_css_only_motion",
        "name": "No CSS-only motion (VERIFY reads painted pixels)",
        "fixture": "silence_5s.wav",
        "check": lambda r: r.get("motion_real", False) or r.get("stage") == "L0",
        "critical": True,
    },
    {
        "id": "first_frame_latency",
        "name": "First frame under latency budget (< 500 ms)",
        "fixture": "hello_beryl.wav",
        "check": lambda r: r.get("first_frame_ok", False),
        "critical": True,
    },
    {
        "id": "lipsync_range",
        "name": "Lip-sync within 40–133 ms offset",
        "fixture": "hello_beryl.wav",
        "check": lambda r: r.get("lipsync_ok", False),
        "critical": True,
    },
    {
        "id": "fps_realtime",
        "name": "FPS ≥ 24 (real-time threshold)",
        "fixture": "hello_beryl.wav",
        "check": lambda r: r.get("fps_ok", False),
        "critical": True,
    },
    {
        "id": "identity_stable",
        "name": "Identity drift ≤ 0.15 over session",
        "fixture": "long_session_30s.wav",
        "check": lambda r: r.get("identity_ok") is True,
        "critical": False,
    },
    {
        "id": "stage_consistent",
        "name": "Stated stage consistent with telemetry",
        "fixture": "hello_beryl.wav",
        "check": lambda r: r.get("stage_consistent", False),
        "critical": False,
    },
]


# ── Fixture helpers ────────────────────────────────────────────────────────

def load_fixture(name: str) -> bytes:
    path = FIXTURES_DIR / name
    if path.exists():
        return path.read_bytes()
    # Stub: generate silence if fixture file missing
    import struct, wave, io
    frames = 16000 * 5  # 5s silence at 16kHz
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(struct.pack(f"{frames}h", *([0] * frames)))
    return buf.getvalue()


def load_reference_photo() -> str:
    for name in ("reference.jpg", "reference.png"):
        path = FIXTURES_DIR / name
        if path.exists():
            return base64.b64encode(path.read_bytes()).decode()
    raise SystemExit("bakeoff/fixtures/reference.jpg missing: add a front-facing face photo first")
    path = FIXTURES_DIR / "reference.jpg"
    if path.exists():
        return base64.b64encode(path.read_bytes()).decode()
    # 1x1 black JPEG stub
    STUB = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8AJQAB/9k="
    return STUB


# ── Run a single test ─────────────────────────────────────────────────────

async def run_test(client: httpx.AsyncClient, test: dict, render_url: str, verify_url: str) -> dict:
    audio = load_fixture(test["fixture"])
    photo_b64 = load_reference_photo()
    audio_b64 = base64.b64encode(audio).decode()

    t0 = time.monotonic()
    render_result = {}
    scorecard = {}

    try:
        # Call render node
        resp = await client.post(f"{render_url}/render", json={
            "photo_b64": photo_b64,
            "audio_b64": audio_b64,
            "conditioning": {"gaze": "hold", "intensity": 0.6},
        })
        resp.raise_for_status()
        render_result = resp.json()
        first_frame_ms = round((time.monotonic() - t0) * 1000, 1)

        fps = render_result.get("fps", 0)
        painted = render_result.get("painted", {})
        painted_area = painted.get("changed_pixel_area", 0)
        latency_ms = render_result.get("latency_ms", first_frame_ms)

        # Try verify node for typed cross-check; fall back to deterministic scorecard.
        telemetry = {
            "stated_stage": "L2",
            "duplug_state": "speaking",
            "audio_energy_rms": 0.1,
            "fps_actual": fps,
            "first_frame_latency_ms": latency_ms,
        }
        metrics = {
            "fps": fps,
            "first_frame_latency_ms": latency_ms,
            "painted_pixel_area": painted_area,
            "frame_diff_mean": painted.get("frame_diff_mean", 0.0),
        }
        try:
            verify_resp = await client.post(f"{verify_url}/scorecard", json={
                "telemetry": telemetry,
                "metrics": metrics,
            }, timeout=5.0)
            if verify_resp.status_code == 200:
                scorecard = verify_resp.json()
        except Exception:
            pass  # verify node offline — compute deterministic scorecard directly

        if not scorecard:
            # Deterministic fallback: compute the same fields the verify node would return.
            # lipsync_offset_ms and identity_drift are not yet measured -> stay None (red).
            scorecard = {
                "stage": "L2",
                "stage_consistent": render_result.get("model", "").startswith("flashhead"),
                "motion_real": painted_area > 100,
                "lipsync_ok": False,   # not measured yet
                "lipsync_offset_ms": None,
                "fps_ok": fps >= 24,
                "fps": fps,
                "first_frame_ok": latency_ms <= 500,
                "first_frame_ms": latency_ms,
                "identity_ok": None,   # not measured yet
                "identity_drift": None,
                "source": "deterministic_fallback",
            }

    except Exception as exc:
        scorecard = {"error": str(exc)}

    passed = test["check"](scorecard) if scorecard and "error" not in scorecard else False

    return {
        "id": test["id"],
        "name": test["name"],
        "passed": passed,
        "critical": test["critical"],
        "render_model": render_result.get("model", "unknown"),
        "render_device": render_result.get("device", "unknown"),
        "scorecard": scorecard,
        "first_frame_ms": round((time.monotonic() - t0) * 1000, 1),
    }


# ── Main ──────────────────────────────────────────────────────────────────

async def main(render_url: str, verify_url: str, output: str):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  BERYL BAKEOFF — {render_url}")
    print(f"{'='*60}\n")

    # Benchmark the render node
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            health = (await client.get(f"{render_url}/health")).json()
            print(f"  Model:   {health.get('model', '?')}")
            print(f"  Device:  {health.get('device', '?')}")
            gpu = health.get("gpu", {})
            if gpu:
                print(f"  GPU:     {gpu.get('name','?')} ({gpu.get('vram_free_gb','?')} GB free)")
            load_err = health.get("load_error")
            if load_err:
                print(f"  WARN:    load_error = {load_err}")
            print()
        except Exception as exc:
            print(f"  WARN: could not reach render node: {exc}\n")

        results = []
        critical_failures = 0

        for test in TESTS:
            print(f"  [{test['id']}] {test['name']}...")
            result = await run_test(client, test, render_url, verify_url)
            results.append(result)
            status = "PASS" if result["passed"] else ("FAIL (critical)" if test["critical"] else "FAIL")
            print(f"    → {status}")
            if not result["passed"] and test["critical"]:
                critical_failures += 1

    all_green = critical_failures == 0
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "render_url": render_url,
        "all_green": all_green,
        "critical_failures": critical_failures,
        "tests": results,
    }

    Path(output).write_text(json.dumps(report, indent=2))

    print(f"\n{'='*60}")
    print(f"  {'ALL GREEN ✓' if all_green else f'FAILED — {critical_failures} critical'}")
    print(f"  Results: {output}")
    print(f"{'='*60}\n")

    return 0 if all_green else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--render-url", default="http://localhost:9523")
    parser.add_argument("--verify-url", default="http://localhost:9525")
    parser.add_argument("--output", default="bakeoff/results/latest.json")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.render_url, args.verify_url, args.output)))
