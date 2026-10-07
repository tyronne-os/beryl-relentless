#!/usr/bin/env bash
# setup_gpu_node.sh — runs ON the GPU node after first deploy
# Installs NVIDIA drivers, cuda, torch, weights, systemd service.
# Called by gpu_on.sh via gcloud compute ssh.

set -euo pipefail

log() { echo "[setup] $*"; }

WEIGHTS_DIR="/opt/beryl/weights"
SERVICE_DIR="/opt/beryl/render"
VENV="/opt/beryl/venv"
HF_TOKEN="${HF_TOKEN:-}"

# ── NVIDIA driver ─────────────────────────────────────────────────────────
if ! nvidia-smi &>/dev/null; then
    log "Installing NVIDIA driver + CUDA..."
    apt-get update -qq
    apt-get install -y -qq linux-headers-$(uname -r) build-essential
    # GCP Deep Learning VMs ship drivers; on plain ubuntu use DKMS path
    apt-get install -y -qq nvidia-driver-535 nvidia-cuda-toolkit || \
        log "WARN: driver install failed — may already be present or need reboot"
fi
log "NVIDIA: $(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || echo 'driver not ready yet')"

# ── Python venv + torch ────────────────────────────────────────────────────
if [[ ! -f "$VENV/bin/python" ]]; then
    log "Creating venv + installing torch..."
    apt-get install -y -qq python3.11 python3.11-venv python3-pip
    python3.11 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet \
        torch torchvision --index-url https://download.pytorch.org/whl/cu118
    "$VENV/bin/pip" install --quiet \
        fastapi uvicorn httpx huggingface_hub diffusers accelerate
fi

# ── Weights download ───────────────────────────────────────────────────────
mkdir -p "$WEIGHTS_DIR"

# FlashHead-1.3B (primary bake-off candidate)
if [[ ! -d "$WEIGHTS_DIR/flashhead" && -n "$HF_TOKEN" ]]; then
    log "Downloading FlashHead-1.3B weights..."
    "$VENV/bin/python" -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='SoulX-AI/FlashHead',
    local_dir='$WEIGHTS_DIR/flashhead',
    token='$HF_TOKEN',
    ignore_patterns=['*.safetensors.index.json'],
)
print('FlashHead downloaded')
" 2>&1 | tail -5 || log "WARN: FlashHead download failed — check HF_TOKEN and repo access"
fi

# AvatarForcing (fallback bake-off candidate)
if [[ ! -d "$WEIGHTS_DIR/avatarforcing" && -n "$HF_TOKEN" ]]; then
    log "Downloading AvatarForcing weights..."
    "$VENV/bin/python" -c "
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id='lycui/AvatarForcing',
    local_dir='$WEIGHTS_DIR/avatarforcing',
    token='$HF_TOKEN',
)
print('AvatarForcing downloaded')
" 2>&1 | tail -5 || log "WARN: AvatarForcing download failed"
fi

# ── systemd service ────────────────────────────────────────────────────────
log "Writing systemd unit beryl-render.service..."
cat > /etc/systemd/system/beryl-render.service <<'UNIT'
[Unit]
Description=Beryl GPU Render Service
After=network.target nvidia-persistenced.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/beryl/render
Environment="WEIGHTS_DIR=/opt/beryl/weights"
Environment="RENDER_PORT=8023"
ExecStart=/opt/beryl/venv/bin/python render_service.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
log "Setup complete — ready for systemctl start beryl-render"
