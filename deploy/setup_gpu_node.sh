#!/usr/bin/env bash
# setup_gpu_node.sh — runs ON the GPU node (via sudo -E from gpu_on.sh). Idempotent.
# Installs the real FlashHead-1.3B (Lite) stack: Soul-AILab/SoulX-FlashHead (Apache-2.0).
set -euo pipefail
log() { echo "[setup] $*"; }

BASE=/opt/beryl
WEIGHTS=$BASE/weights
FH=$BASE/flashhead
VENV=$BASE/venv-fh
PORT="${RENDER_PORT:-9523}"
FA_WHL="https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.0.post2/flash_attn-2.8.0.post2+cu12torch2.7cxx11abiFALSE-cp310-cp310-linux_x86_64.whl"

nvidia-smi >/dev/null 2>&1 || { log "ERROR: no NVIDIA driver on node"; exit 1; }
log "GPU: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader)"
python3 --version | grep -q "3.10" || log "WARN: system python is not 3.10 ($(python3 --version)); FlashHead targets 3.10"

mkdir -p "$WEIGHTS" "$BASE/render"

if ! command -v ffmpeg >/dev/null || ! command -v git >/dev/null; then
    log "installing ffmpeg/git..."
    apt-get update -qq && apt-get install -y -qq ffmpeg git python3-venv python3-dev
fi

if [[ ! -d "$FH/.git" ]]; then
    log "cloning SoulX-FlashHead..."
    git clone --depth 1 https://github.com/Soul-AILab/SoulX-FlashHead "$FH"
fi

if [[ ! -f "$VENV/.ready" ]]; then
    log "building venv (torch 2.7.1 cu128 + FlashHead requirements) — first run ~10 min..."
    rm -rf "$VENV"; python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip wheel
    "$VENV/bin/pip" install -q torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
    "$VENV/bin/pip" install -q -r "$FH/requirements.txt"
    "$VENV/bin/pip" install -q "$FA_WHL" || log "WARN: flash_attn wheel failed to install"
    "$VENV/bin/pip" install -q fastapi uvicorn pillow httpx "huggingface_hub[cli]"
    touch "$VENV/.ready"
fi

if [[ ! -f "$WEIGHTS/.fh_ready" ]]; then
    [[ -n "${HF_TOKEN:-}" ]] || { log "ERROR: HF_TOKEN not set on node"; exit 1; }
    log "downloading FlashHead Lite + LTX VAE (~8 GB) and wav2vec2..."
    HF_TOKEN="$HF_TOKEN" WEIGHTS="$WEIGHTS" "$VENV/bin/python" - <<'PY'
import os
from huggingface_hub import snapshot_download
t, w = os.environ["HF_TOKEN"], os.environ["WEIGHTS"]
snapshot_download("Soul-AILab/SoulX-FlashHead-1_3B", local_dir=f"{w}/SoulX-FlashHead-1_3B",
                  allow_patterns=["Model_Lite/*", "VAE_LTX/*", "*.json"], token=t)
snapshot_download("facebook/wav2vec2-base-960h", local_dir=f"{w}/wav2vec2-base-960h",
                  ignore_patterns=["*.h5", "*.msgpack", "*.ot"], token=t)
print("weights ok")
PY
    touch "$WEIGHTS/.fh_ready"
fi

log "writing systemd unit (port $PORT)..."
cat > /etc/systemd/system/beryl-render.service <<UNIT
[Unit]
Description=Beryl GPU Render Service (FlashHead Lite)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$BASE/render
Environment="PYTHONPATH=$FH"
Environment="FLASHHEAD_CKPT=$WEIGHTS/SoulX-FlashHead-1_3B"
Environment="WAV2VEC_DIR=$WEIGHTS/wav2vec2-base-960h"
Environment="RENDER_PORT=$PORT"
ExecStart=$VENV/bin/python render_service.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
log "setup complete"
