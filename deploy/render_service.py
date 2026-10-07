"""
render_service.py — GPU render service on berylize-node (L2).
Real model: SoulX-FlashHead-1.3B Lite via the repo's flash_head.inference API
(get_pipeline / get_base_data / get_infer_params / get_audio_embedding / run_pipeline),
using the same "stream" chunking as the repo's generate_video.py.

POST /render {photo_b64, audio_b64, max_chunks?, save_mp4?} -> first frame + real timing + painted-pixel stats
     save_mp4=true renders the whole audio (max_chunks ignored) and writes an H.264+AAC mp4;
     the response carries video_id / video_url.
GET  /video/{video_id} -> the mp4 (photo in -> speaking avatar out)
GET  /health, GET /benchmark (uses the last photo sent)
If the model fails to load, /health says why and /render returns the photo with model="passthrough".
"""
import asyncio
import base64
import hashlib
import io
import logging
import os
import re
import subprocess
import tempfile
import uuid
import threading
import time
from collections import deque
from contextlib import asynccontextmanager

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

log = logging.getLogger("render_service")
logging.basicConfig(level=logging.INFO)

CKPT = os.environ.get("FLASHHEAD_CKPT", "/opt/beryl/weights/SoulX-FlashHead-1_3B")
W2V = os.environ.get("WAV2VEC_DIR", "/opt/beryl/weights/wav2vec2-base-960h")
MODEL_TYPE = "lite"
PORT = int(os.environ.get("RENDER_PORT", "9523"))
VIDEO_DIR = os.environ.get("RENDER_VIDEO_DIR", "/opt/beryl/renders")
VIDEO_KEEP = int(os.environ.get("RENDER_VIDEO_KEEP", "50"))  # oldest clips pruned past this

_pipe = None
_load_error = None
_photo_hash = None
_last_photo = None
_lock = threading.Lock()


def _load():
    global _pipe, _load_error
    try:
        import torch
        from flash_head.inference import get_pipeline
        _pipe = get_pipeline(world_size=1, ckpt_dir=CKPT, wav2vec_dir=W2V, model_type=MODEL_TYPE)
        log.info("FlashHead %s loaded (cuda=%s)", MODEL_TYPE, torch.cuda.is_available())
    except Exception as exc:
        _load_error = f"{type(exc).__name__}: {exc}"[:400]
        log.error("FlashHead load failed: %s", _load_error)


def _prepare_photo(photo_bytes: bytes):
    global _photo_hash
    from flash_head.inference import get_base_data
    h = hashlib.sha256(photo_bytes).hexdigest()
    if h == _photo_hash:
        return
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        from PIL import Image
        Image.open(io.BytesIO(photo_bytes)).convert("RGB").save(f.name)
        path = f.name
    try:
        get_base_data(_pipe, cond_image_path_or_dir=path, base_seed=42, use_face_crop=False)
    finally:
        os.unlink(path)
    _photo_hash = h


def _jpeg_b64(frame: np.ndarray) -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(frame).save(buf, format="JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


def _painted_stats(chunks: list) -> dict:
    """Real pixel motion: mean abs diff between consecutive frames, and changed-pixel area."""
    frames = np.concatenate(chunks, axis=0)[:, ::4, ::4, :].astype(np.int16)  # downsample 4x
    if len(frames) < 2:
        return {"changed_pixel_area": 0, "frame_diff_mean": 0.0}
    d = np.abs(np.diff(frames, axis=0)).max(axis=-1)
    return {"changed_pixel_area": int((d > 10).sum(axis=(1, 2)).mean() * 16),
            "frame_diff_mean": round(float(d.mean()), 3)}


def _write_mp4(chunks: list, audio: np.ndarray, sr: int, fps: int) -> str:
    """Mux rendered frames + the driving audio into an mp4. Returns the video id."""
    import soundfile as sf
    os.makedirs(VIDEO_DIR, exist_ok=True)
    vid = uuid.uuid4().hex[:12]
    out = os.path.join(VIDEO_DIR, f"{vid}.mp4")
    frames = np.concatenate(chunks, axis=0)
    n, h, w, _ = frames.shape
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = f.name
    try:
        sf.write(wav, audio[: int(n * sr / fps)], sr)  # trim padding to the frame count
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
               "-i", wav, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
               "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out]
        r = subprocess.run(cmd, input=frames.tobytes(), capture_output=True, timeout=300)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {r.stderr.decode()[-400:]}")
    finally:
        os.unlink(wav)
    clips = sorted((os.path.join(VIDEO_DIR, x) for x in os.listdir(VIDEO_DIR) if x.endswith(".mp4")),
                   key=os.path.getmtime)
    for old in clips[:-VIDEO_KEEP]:
        os.unlink(old)
    return vid


def _render(photo_b64: str, audio_b64: str, max_chunks: int, save_mp4: bool = False) -> dict:
    global _last_photo
    photo_bytes = base64.b64decode(photo_b64) if photo_b64 else b""
    if _pipe is None or not photo_bytes:
        return {"frame_b64": photo_b64, "model": "passthrough", "device": "cpu",
                "fps": 0, "latency_ms": 0, "error": _load_error or "no photo"}
    import librosa
    import torch
    from flash_head.inference import get_audio_embedding, get_infer_params, run_pipeline

    with _lock:
        _last_photo = photo_b64
        _prepare_photo(photo_bytes)
        p = get_infer_params()
        sr, fps, cad, fn, mfn = (p["sample_rate"], p["tgt_fps"], p["cached_audio_duration"],
                                 p["frame_num"], p["motion_frames_num"])
        slice_samples = (fn - mfn) * sr // fps
        if audio_b64:
            audio, _ = librosa.load(io.BytesIO(base64.b64decode(audio_b64)), sr=sr, mono=True)
        else:
            audio = np.zeros(sr, dtype=np.float32)
        if len(audio) % slice_samples:
            audio = np.concatenate([audio, np.zeros(slice_samples - len(audio) % slice_samples, dtype=audio.dtype)])
        slices = audio.reshape(-1, slice_samples)
        if not save_mp4:
            slices = slices[:max_chunks]

        cached = sr * cad
        end_idx = cad * fps
        start_idx = end_idx - fn
        dq = deque([0.0] * cached, maxlen=cached)
        chunk_ms, chunks = [], []
        for s in slices:
            t0 = time.monotonic()
            dq.extend(s.tolist())
            emb = get_audio_embedding(_pipe, np.array(dq), start_idx, end_idx)
            video = run_pipeline(_pipe, emb)[mfn:]
            torch.cuda.synchronize()
            chunk_ms.append(round((time.monotonic() - t0) * 1000, 1))
            chunks.append(video.cpu().numpy().astype(np.uint8))

    total_frames = sum(len(c) for c in chunks)
    total_s = sum(chunk_ms) / 1000
    clip = {}
    if save_mp4:
        t0 = time.monotonic()
        vid = _write_mp4(chunks, audio, sr, fps)
        clip = {"video_id": vid, "video_url": f"/video/{vid}",
                 "video_s": round(total_frames / fps, 2),
                 "encode_ms": round((time.monotonic() - t0) * 1000, 1)}
    return {
        **clip,
        "frame_b64": _jpeg_b64(chunks[0][0]),
        "last_frame_b64": _jpeg_b64(chunks[-1][-1]),
        "model": f"flashhead-{MODEL_TYPE}",
        "device": "cuda",
        "latency_ms": chunk_ms[0],
        "chunk_ms": chunk_ms,
        "frames": total_frames,
        "fps": round(total_frames / total_s, 1) if total_s else 0,
        "target_fps": fps,
        "painted": _painted_stats(chunks),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.get_event_loop().run_in_executor(None, _load)
    yield


app = FastAPI(title="beryl-render-gpu", lifespan=lifespan)


@app.post("/render")
async def render_endpoint(body: dict):
    return await asyncio.get_event_loop().run_in_executor(
        None, _render, body.get("photo_b64", ""), body.get("audio_b64", ""), int(body.get("max_chunks", 4)),
        bool(body.get("save_mp4", False)))


@app.get("/video/{video_id}")
async def video(video_id: str):
    if not re.fullmatch(r"[0-9a-f]{12}", video_id):
        raise HTTPException(400, "bad video id")
    path = os.path.join(VIDEO_DIR, f"{video_id}.mp4")
    if not os.path.exists(path):
        raise HTTPException(404, "no such video")
    return FileResponse(path, media_type="video/mp4", filename=f"beryl_{video_id}.mp4")


@app.get("/benchmark")
async def benchmark():
    if not _last_photo:
        return {"error": "send one /render request with a photo first"}
    r = await asyncio.get_event_loop().run_in_executor(None, _render, _last_photo, "", 6)
    return {k: r.get(k) for k in ("model", "fps", "latency_ms", "chunk_ms", "target_fps")}


@app.get("/health")
async def health():
    info = {"status": "ok", "model": f"flashhead-{MODEL_TYPE}" if _pipe else "passthrough",
            "load_error": _load_error}
    try:
        import torch
        info["device"] = "cuda" if torch.cuda.is_available() else "cpu"
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info(0)
            info["gpu"] = {"name": torch.cuda.get_device_name(0),
                           "vram_free_gb": round(free / 1e9, 1), "vram_total_gb": round(total / 1e9, 1)}
    except Exception:
        pass
    return info


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")
