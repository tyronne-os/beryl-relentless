#!/usr/bin/env bash
# deploy/gpu_ollama.sh — install Ollama on berylize-node and open a tunnel to it.
#
# Run AFTER gpu_on.sh (render service must be up, VM running, SA key loaded).
# The L4 has ~18 GB VRAM free alongside FlashHead; qwen2.5-coder:14b uses ~8 GB.
#
# After this script:
#   OLLAMA_HOST=http://localhost:11434 ollama run qwen2.5-coder:14b
#   (Ollama CLI on your laptop connects via the tunnel — no local GPU needed)
#
# Tunnel PID: /tmp/beryl_ollama_tunnel.pid
# Logs:       /tmp/beryl_ollama_tunnel.log
#
# Usage:
#   source .env && bash deploy/gpu_ollama.sh
#   bash deploy/gpu_ollama.sh --stop     # close tunnel only
set -uo pipefail
SCRIPT_TAG="gpu_ollama"
source "$(dirname "$0")/lib.sh"

OLLAMA_PORT="${OLLAMA_PORT:-11434}"
MODEL="${OLLAMA_MODEL:-qwen2.5-coder:14b}"
OLLAMA_TUNNEL_PID="/tmp/beryl_ollama_tunnel.pid"

# ── --stop: close the Ollama tunnel ──────────────────────────────────────────
if [[ "${1:-}" == "--stop" ]]; then
    if [[ -f "$OLLAMA_TUNNEL_PID" ]] && kill -0 "$(cat "$OLLAMA_TUNNEL_PID")" 2>/dev/null; then
        kill "$(cat "$OLLAMA_TUNNEL_PID")"
        log "Ollama tunnel closed."
    else
        log "No Ollama tunnel running."
    fi
    pkill -f "ssh.*-L ${OLLAMA_PORT}:localhost:${OLLAMA_PORT}" 2>/dev/null || true
    rm -f "$OLLAMA_TUNNEL_PID"
    exit 0
fi

# ── 1. Verify the VM is reachable ────────────────────────────────────────────
log "checking node is up..."
gc compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
    --format="value(status)" | grep -q RUNNING || {
    echo "[gpu_ollama] ERROR: $INSTANCE is not RUNNING. Run ./deploy/gpu_on.sh first." >&2
    exit 1
}

# ── 2. Install Ollama on the node (idempotent) ───────────────────────────────
log "installing Ollama on node (idempotent)..."
node_ssh "
set -e
if command -v ollama >/dev/null 2>&1; then
    echo '[node] ollama already installed: '$(ollama --version)
else
    curl -fsSL https://ollama.com/install.sh | sudo sh
    echo '[node] ollama installed'
fi

# start ollama service if not running
if ! systemctl is-active --quiet ollama 2>/dev/null; then
    sudo systemctl enable ollama --now 2>/dev/null || \
        (nohup ollama serve >/tmp/ollama.log 2>&1 & sleep 3)
fi
echo '[node] ollama service up'
"

# ── 3. Pull the model on the node (idempotent — skip if already present) ─────
log "pulling $MODEL on node (first pull ~8 GB, subsequent runs instant)..."
node_ssh "
set -e
if ollama list | grep -q '${MODEL%%:*}'; then
    echo '[node] ${MODEL} already present'
else
    echo '[node] pulling ${MODEL}...'
    ollama pull '${MODEL}'
    echo '[node] pull complete'
fi
"

# ── 4. Open SSH tunnel for Ollama port ──────────────────────────────────────
log "opening Ollama tunnel on localhost:${OLLAMA_PORT}..."

# kill any stale tunnel on this port
pkill -f "ssh.*-L ${OLLAMA_PORT}:localhost:${OLLAMA_PORT}" 2>/dev/null && sleep 1

if (echo >"/dev/tcp/127.0.0.1/${OLLAMA_PORT}") 2>/dev/null; then
    echo "[gpu_ollama] ERROR: local port ${OLLAMA_PORT} already in use by something else." >&2
    exit 1
fi

gc compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --quiet -- \
    -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
    -L "${OLLAMA_PORT}:localhost:${OLLAMA_PORT}" >/tmp/beryl_ollama_tunnel.log 2>&1 &
echo $! > "$OLLAMA_TUNNEL_PID"

# wait up to 15 s for tunnel
for _ in $(seq 1 15); do
    curl -s -m 2 -o /dev/null "http://localhost:${OLLAMA_PORT}/api/tags" && break
    sleep 1
done

if ! curl -s -m 2 "http://localhost:${OLLAMA_PORT}/api/tags" >/dev/null 2>&1; then
    echo "[gpu_ollama] WARN: tunnel may still be starting — check /tmp/beryl_ollama_tunnel.log"
else
    log "tunnel OK — Ollama API at http://localhost:${OLLAMA_PORT}"
fi

# ── 5. Done — print usage ────────────────────────────────────────────────────
echo
echo "======================================================"
echo "  Ollama running on berylize-node L4 GPU"
echo "  Model: $MODEL"
echo "  Tunnel: localhost:${OLLAMA_PORT}"
echo "======================================================"
echo
echo "  From your laptop terminal:"
echo "    OLLAMA_HOST=http://localhost:${OLLAMA_PORT} ollama run ${MODEL}"
echo
echo "  Or OpenAI-compatible (for any editor/tool):"
echo "    Base URL:  http://localhost:${OLLAMA_PORT}/v1"
echo "    Model:     ${MODEL}"
echo "    API Key:   ollama"
echo
echo "  To stop tunnel: bash deploy/gpu_ollama.sh --stop"
echo "======================================================"
