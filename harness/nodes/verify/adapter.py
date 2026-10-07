"""
VERIFY node — real measurements → sensory gland scorecard.
Measures: lip-sync offset, FPS, first-frame latency, painted-pixel motion, identity drift.
Feeds JEV·VERIFY for typed cross-check. Publishes scorecard to /ws/sensory-gland.
"""
import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

log = logging.getLogger("node.verify")

# Connected sensory-gland clients (Studio panel)
_gland_clients: set[WebSocket] = set()

# Rolling metrics buffer (last N frames)
_metrics_buffer: list[dict] = []
_BUFFER_MAX = 60


def measure_lipsync_offset(audio_timestamp_ms: float, first_video_timestamp_ms: float) -> float:
    """Positive = video late relative to audio."""
    return round(first_video_timestamp_ms - audio_timestamp_ms, 1)


def measure_painted_pixels(prev_frame_data: list[int], curr_frame_data: list[int]) -> dict:
    """
    Compare sequential frames to detect real pixel-level motion.
    Expects raw pixel integer lists (e.g. flat RGBA).
    Returns {changed_pixel_area, frame_diff_mean, occluding_layer}.
    """
    if not prev_frame_data or not curr_frame_data:
        return {"changed_pixel_area": 0, "frame_diff_mean": 0.0, "occluding_layer": None}

    n = min(len(prev_frame_data), len(curr_frame_data))
    diff_sum = sum(abs(curr_frame_data[i] - prev_frame_data[i]) for i in range(n))
    diff_mean = diff_sum / n if n else 0.0
    # Threshold: pixels with diff > 10 count as changed
    changed = sum(1 for i in range(n) if abs(curr_frame_data[i] - prev_frame_data[i]) > 10)
    changed_area = changed // 4  # RGBA -> pixel count

    occluding = None
    if diff_mean < 1.0 and changed_area < 10:
        occluding = "css_transform_only"  # motion claimed but zero pixel change

    return {
        "changed_pixel_area": changed_area,
        "frame_diff_mean": round(diff_mean, 2),
        "occluding_layer": occluding,
    }


async def run_scorecard(telemetry: dict, metrics: dict) -> dict:
    """
    telemetry: {stated_stage, duplug_state, audio_energy_rms, fps_actual,
                lipsync_offset_ms, first_frame_latency_ms}
    metrics: {lipsync_offset_ms, fps, first_frame_latency_ms, identity_drift}
    Returns full scorecard dict.
    """
    from harness.jev import verify as jev_verify

    painted = {
        "changed_pixel_area": metrics.get("painted_pixel_area", 0),
        "occluding_layer": metrics.get("occluding_layer", None),
        "frame_diff_mean": metrics.get("frame_diff_mean", 0.0),
    }
    verify_result = await jev_verify.check(telemetry, painted)
    card = jev_verify.scorecard(verify_result, metrics)
    card["verify_detail"] = verify_result
    return card


async def broadcast_scorecard(card: dict):
    dead = set()
    for ws in list(_gland_clients):
        try:
            await ws.send_json({"type": "scorecard", **card})
        except Exception:
            dead.add(ws)
    _gland_clients.difference_update(dead)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("VERIFY node ready")
    yield


app = FastAPI(title="verify-node", lifespan=lifespan)


@app.websocket("/ws/sensory-gland")
async def sensory_gland_ws(ws: WebSocket):
    """Studio panel connects here to receive live scorecards."""
    await ws.accept()
    _gland_clients.add(ws)
    log.info("Sensory gland client connected (%d total)", len(_gland_clients))
    try:
        while True:
            await asyncio.sleep(30)
    except WebSocketDisconnect:
        _gland_clients.discard(ws)


@app.post("/scorecard")
async def scorecard_endpoint(body: dict):
    """
    body: {telemetry: {...}, metrics: {...}}
    Returns + broadcasts the full scorecard.
    """
    telemetry = body.get("telemetry", {})
    metrics = body.get("metrics", {})
    card = await run_scorecard(telemetry, metrics)
    await broadcast_scorecard(card)
    return card


@app.post("/measure/lipsync")
async def measure_lipsync(body: dict):
    """body: {audio_ts_ms, video_ts_ms}"""
    offset = measure_lipsync_offset(
        body.get("audio_ts_ms", 0),
        body.get("video_ts_ms", 0),
    )
    return {"lipsync_offset_ms": offset, "ok": 40 <= offset <= 133}


@app.post("/measure/pixels")
async def measure_pixels(body: dict):
    """body: {prev_frame: list[int], curr_frame: list[int]}"""
    result = measure_painted_pixels(
        body.get("prev_frame", []),
        body.get("curr_frame", []),
    )
    return result


@app.get("/metrics/latest")
async def latest_metrics():
    if not _metrics_buffer:
        return {"error": "no metrics yet"}
    return _metrics_buffer[-1]


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "node": "verify",
        "gland_clients": len(_gland_clients),
        "buffer_size": len(_metrics_buffer),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8025, log_level="info")
