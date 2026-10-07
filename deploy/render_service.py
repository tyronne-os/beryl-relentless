"""
render_service.py — GPU render service running ON berylize-node (L4).
Serves POST /render {photo_b64, audio_b64, conditioning} → {frame_b64, fps, latency_ms}
Also: POST /generate_latents (motion only, no full render)

Bake-off: tries FlashHead-1.3B first, falls back to AvatarForcing.
Both target ~34 ms/frame on L4. L4 has 24 GB VRAM.
"""
import asyncio
import base64
import io
import logging
import os
import time
from contextlib import asynccontextmanager

import torch
import uvicorn
from fastapi import FastAPI

log = logging.getLogger("render_service")
logging.basicConfig(level=logging.INFO)

WEIGHTS_DIR = os.environ.get("WEIGHTS_DIR", "/opt/beryl/weights")
RENDER_PORT = int(os.environ.get("RENDER_PORT", "9523"))
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Global model handles
_model = None
_model_name = "none"


def _load_flashhead():
    """Load FlashHead-1.3B — single-step distilled video diffusion."""
    import sys
    sys.path.insert(0, f"{WEIGHTS_DIR}/flashhead")
    try:
        from flashhead import FlashHeadPipeline  # type: ignore
        pipe = FlashHeadPipeline.from_pretrained(
            f"{WEIGHTS_DIR}/flashhead",
            torch_dtype=torch.float16,
        ).to(DEVICE)
        pipe.enable_xformers_memory_efficient_attention()
        return pipe, "flashhead-1.3b"
    except Exception as exc:
        log.warning("FlashHead load failed: %s", exc)
        return None, None


def _load_avatarforcing():
    """Load AvatarForcing — 34 ms/frame dual-anchor distilled."""
    import sys
    sys.path.insert(0, f"{WEIGHTS_DIR}/avatarforcing")
    try:
        from avatar_forcing import AvatarForcingPipeline  # type: ignore
        pipe = AvatarForcingPipeline.from_pretrained(
            f"{WEIGHTS_DIR}/avatarforcing",
            torch_dtype=torch.float16,
        ).to(DEVICE)
        return pipe, "avatarforcing"
    except Exception as exc:
        log.warning("AvatarForcing load failed: %s", exc)
        return None, None


def _passthrough_render(photo_b64: str, audio_b64: str, conditioning: dict) -> dict:
    """CPU passthrough: return the reference photo as a static frame."""
    return {
        "frame_b64": photo_b64,
        "fps": 1,
        "latency_ms": 0,
        "model": "passthrough",
        "device": "cpu",
        "warning": "no GPU model loaded — returning reference photo",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _model_name
    log.info("Loading render model on %s...", DEVICE)
    if DEVICE == "cuda":
        _model, _model_name = _load_flashhead()
        if _model is None:
            _model, _model_name = _load_avatarforcing()
    if _model is None:
        log.warning("No GPU model loaded — passthrough mode")
        _model_name = "passthrough"
    log.info("Render service ready: model=%s device=%s", _model_name, DEVICE)
    yield
    if _model is not None and hasattr(_model, "to"):
        del _model
        if DEVICE == "cuda":
            torch.cuda.empty_cache()


app = FastAPI(title="beryl-render-gpu", lifespan=lifespan)


def _render_frame(photo_b64: str, audio_b64: str, conditioning: dict) -> dict:
    """Synchronous render call — runs in executor to avoid blocking event loop."""
    t0 = time.monotonic()

    if _model is None or _model_name == "passthrough":
        return _passthrough_render(photo_b64, audio_b64, conditioning)

    try:
        photo_bytes = base64.b64decode(photo_b64)
        audio_bytes = base64.b64decode(audio_b64)

        from PIL import Image  # type: ignore
        photo_img = Image.open(io.BytesIO(photo_bytes)).convert("RGB").resize((512, 512))

        result = _model(
            reference_image=photo_img,
            audio=audio_bytes,
            gaze=conditioning.get("gaze", "hold"),
            intensity=float(conditioning.get("intensity", 0.5)),
            num_inference_steps=1,  # single-step distilled
        )
        frames = result.frames if hasattr(result, "frames") else [result]
        frame = frames[0] if frames else photo_img

        buf = io.BytesIO()
        frame.save(buf, format="JPEG", quality=85)
        frame_b64 = base64.b64encode(buf.getvalue()).decode()

        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return {
            "frame_b64": frame_b64,
            "fps": round(1000 / max(latency_ms, 1), 1),
            "latency_ms": latency_ms,
            "model": _model_name,
            "device": DEVICE,
        }
    except Exception as exc:
        log.error("Render failed: %s", exc)
        return _passthrough_render(photo_b64, audio_b64, conditioning)


@app.post("/render")
async def render_endpoint(body: dict):
    """
    body: {photo_b64, audio_b64, conditioning: {gaze, intensity, ...}}
    Returns: {frame_b64, fps, latency_ms, model, device}
    """
    result = await asyncio.get_event_loop().run_in_executor(
        None, _render_frame,
        body.get("photo_b64", ""),
        body.get("audio_b64", ""),
        body.get("conditioning", {}),
    )
    return result


@app.get("/health")
async def health():
    gpu_info = {}
    if DEVICE == "cuda":
        try:
            gpu_info = {
                "name": torch.cuda.get_device_name(0),
                "vram_free_gb": round(torch.cuda.mem_get_info(0)[0] / 1e9, 1),
                "vram_total_gb": round(torch.cuda.mem_get_info(0)[1] / 1e9, 1),
            }
        except Exception:
            pass
    return {
        "status": "ok",
        "model": _model_name,
        "device": DEVICE,
        "gpu": gpu_info,
    }


@app.get("/benchmark")
async def benchmark():
    """Quick FPS benchmark using a blank frame."""
    import numpy as np
    from PIL import Image
    blank = Image.fromarray(np.zeros((512, 512, 3), dtype=np.uint8))
    buf = io.BytesIO()
    blank.save(buf, format="JPEG")
    blank_b64 = base64.b64encode(buf.getvalue()).decode()

    times = []
    for _ in range(5):
        t0 = time.monotonic()
        _render_frame(blank_b64, "", {})
        times.append((time.monotonic() - t0) * 1000)
    avg = sum(times) / len(times)
    return {
        "avg_latency_ms": round(avg, 1),
        "avg_fps": round(1000 / avg, 1),
        "model": _model_name,
        "device": DEVICE,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=RENDER_PORT, log_level="info")
