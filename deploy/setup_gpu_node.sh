#!/usr/bin/env bash
# setup_gpu_node.sh — runs ON the GPU node (via sudo -E from gpu_on.sh). Idempotent.
# Installs the real FlashHead-1.3B (Lite) stack: Soul-AILab/SoulX-FlashHead (Apache-2.0).
# Order: clone -> weights (own venv, independent) -> model venv -> import check -> systemd unit.
set -euo pipefail
log() { echo "[setup] $*"; }

BASE=/opt/beryl
WEIGHTS=$BASE/weights
FH=$BASE/flashhead
VENV=$BASE/venv-fh
HFVENV=$BASE/venv-hf
PORT="${RENDER_PORT:-9523}"
FA_WHL="https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.0.post2/flash_attn-2.8.0.post2+cu12torch2.7cxx11abiFALSE-cp310-cp310-linux_x86_64.whl"

export PIP_CACHE_DIR=$BASE/pipcache; mkdir -p "$PIP_CACHE_DIR"
nvidia-smi >/dev/null 2>&1 || { log "ERROR: no NVIDIA driver on node"; exit 1; }
log "GPU: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader)"
python3 --version | grep -q "3.10" || log "WARN: system python is not 3.10 ($(python3 --version)); FlashHead targets 3.10"

mkdir -p "$WEIGHTS" "$BASE/render"

if ! command -v ffmpeg >/dev/null || ! command -v git >/dev/null || ! python3 -c "import ensurepip" 2>/dev/null \
   || ! dpkg -s python3-dev >/dev/null 2>&1; then
    log "installing ffmpeg/git/python3-venv/python3-dev..."
    apt-get update -qq
    apt-get install -y -qq ffmpeg git python3-venv python3-dev
fi

if [[ ! -d "$FH/.git" ]]; then
    log "cloning SoulX-FlashHead..."
    git clone --depth 1 https://github.com/Soul-AILab/SoulX-FlashHead "$FH"
fi

# Weights: separate venv with current huggingface_hub (2.x, `hf` CLI + hf_xet).
# Kept out of the model venv because transformers==4.57.3 requires huggingface_hub<1.0.
if [[ ! -f "$WEIGHTS/.fh_ready" ]]; then
    [[ -n "${HF_TOKEN:-}" ]] || { log "ERROR: HF_TOKEN not set on node"; exit 1; }
    if [[ ! -x "$HFVENV/bin/hf" ]]; then
        python3 -m venv "$HFVENV"
        "$HFVENV/bin/pip" install -q --upgrade pip "huggingface_hub>=2.1" hf_xet
    fi
    log "downloading FlashHead Lite + VAEs (~8.3 GB) and wav2vec2 (~0.4 GB)..."
    export HF_TOKEN HF_XET_HIGH_PERFORMANCE=1
    "$HFVENV/bin/hf" download Soul-AILab/SoulX-FlashHead-1_3B \
        --include "Model_Lite/*" --include "VAE_LTX/*" --include "VAE_Wan/*" --include "*.json" \
        --local-dir "$WEIGHTS/SoulX-FlashHead-1_3B"
    "$HFVENV/bin/hf" download facebook/wav2vec2-base-960h \
        --exclude "*.h5" --exclude "*.msgpack" --exclude "*.ot" \
        --local-dir "$WEIGHTS/wav2vec2-base-960h"
    [[ -s "$WEIGHTS/SoulX-FlashHead-1_3B/Model_Lite/diffusion_pytorch_model.safetensors" ]] \
        || { log "ERROR: Model_Lite weights missing after download"; exit 1; }
    touch "$WEIGHTS/.fh_ready"
    log "weights ok ($(du -sh "$WEIGHTS" | cut -f1))"
fi

# Model venv: .built = pip finished (never rebuilt for a code/import failure), .ready = import check passed.
if [[ ! -f "$VENV/.built" ]]; then
    log "building venv (torch 2.7.1 cu128 + FlashHead requirements) — first run ~10 min..."
    rm -rf "$VENV"; python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip wheel
    "$VENV/bin/pip" install -q torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
    # FlashHead pins nvidia-nccl-cu12==2.27.3 but torch 2.7.1+cu128 needs 2.26.x -> drop it (torch brings its own).
    # gradio/flask are demo UIs. huggingface_hub must stay <1.0 for transformers 4.57.3.
    printf 'torch==2.7.1\ntorchvision==0.22.1\ntransformers==4.57.3\nhuggingface_hub<1.0\n' > "$VENV/constraints.txt"
    grep -viE '^(nvidia-nccl|gradio|flask)' "$FH/requirements.txt" > "$VENV/req.txt"
    "$VENV/bin/pip" install -q -r "$VENV/req.txt" -c "$VENV/constraints.txt" || {
        log "WARN: resolver failed; retrying with --no-deps (import check below catches missing modules)"
        "$VENV/bin/pip" install -q --no-deps -r "$VENV/req.txt"
    }
    "$VENV/bin/pip" install -q "$FA_WHL" || log "WARN: flash_attn wheel failed to install"
    "$VENV/bin/pip" install -q fastapi uvicorn pillow httpx -c "$VENV/constraints.txt"
    "$VENV/bin/pip" check || log "WARN: pip check reports conflicts (above); continuing to import check"
    touch "$VENV/.built"
fi

if [[ ! -f "$VENV/.ready" ]]; then
    # inference.py opens flash_head/configs/infer_params.yaml relative to CWD -> must run from repo root.
    log "import check: flash_head.inference (cwd=$FH)..."
    (cd "$FH" && PYTHONPATH="$FH" "$VENV/bin/python" -c "import torch, librosa, soundfile, PIL, flash_head.inference as m; assert torch.cuda.is_available(), 'torch cannot see the GPU (driver too old for cu128? need >=570)'; print('import ok, cuda = True')") \
        || { log "ERROR: flash_head import failed (see above); venv kept, only this check re-runs next time"; exit 1; }
    touch "$VENV/.ready"
fi

log "writing systemd unit (port $PORT)..."
cat > /etc/systemd/system/beryl-render.service <<UNIT
[Unit]
Description=Beryl GPU Render Service (FlashHead Lite)
After=network.target

[Service]
Type=simple
User=root
# FlashHead loads configs relative to CWD: must be the repo root.
WorkingDirectory=$FH
Environment="PYTHONPATH=$FH"
Environment="FLASHHEAD_CKPT=$WEIGHTS/SoulX-FlashHead-1_3B"
Environment="WAV2VEC_DIR=$WEIGHTS/wav2vec2-base-960h"
Environment="RENDER_PORT=$PORT"
ExecStart=$VENV/bin/python $BASE/render/render_service.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
log "setup complete"
