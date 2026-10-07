#!/usr/bin/env bash
# deploy/open_ui.sh — one command to open LibreChat + Qwen on the GPU node.
#
# Usage:
#   source .env && bash deploy/open_ui.sh
#
# What it does (all idempotent — safe to run multiple times):
#   1. Installs Ollama on the node if missing
#   2. Pulls qwen2.5-coder:14b if missing
#   3. Installs LibreChat via Podman if missing
#   4. Starts Ollama + LibreChat on the node if not running
#   5. Opens SSH tunnels: localhost:3080 (UI) + localhost:11434 (Ollama API)
#   6. Waits for both services to respond
#   7. Prints the URL — open it in your browser
#
# Stop tunnels:  bash deploy/open_ui.sh --stop
# Logs:          /tmp/beryl_ui_tunnel.log
set -uo pipefail
SCRIPT_TAG="open_ui"
source "$(dirname "$0")/lib.sh"

OLLAMA_PORT="${OLLAMA_PORT:-11434}"
UI_PORT="${UI_PORT:-3080}"
UI_TUNNEL_PID="/tmp/beryl_ui_tunnel.pid"
MODEL="${OLLAMA_MODEL:-qwen2.5-coder:14b}"

# ── --stop ────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--stop" ]]; then
    [[ -f "$UI_TUNNEL_PID" ]] && kill "$(cat "$UI_TUNNEL_PID")" 2>/dev/null || true
    pkill -f "ssh.*-L ${UI_PORT}:localhost:${UI_PORT}" 2>/dev/null || true
    pkill -f "ssh.*-L ${OLLAMA_PORT}:localhost:${OLLAMA_PORT}" 2>/dev/null || true
    rm -f "$UI_TUNNEL_PID"
    log "tunnels closed."
    exit 0
fi

# ── 1. Verify VM is running ───────────────────────────────────────────────────
log "checking node..."
gc compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
    --format="value(status)" 2>/dev/null | grep -q RUNNING || {
    log "ERROR: berylize-node is not RUNNING. Start it first with: ./deploy/gpu_on.sh"
    exit 1
}
log "node is RUNNING."

# ── 2. Install Ollama + pull model (idempotent) ───────────────────────────────
log "ensuring Ollama + ${MODEL} on node..."
node_ssh "
set -e
# install ollama
if ! command -v ollama >/dev/null 2>&1; then
    curl -fsSL https://ollama.com/install.sh | sudo sh
fi
# start ollama if not running
if ! pgrep -x ollama >/dev/null 2>&1; then
    nohup ollama serve >/tmp/ollama.log 2>&1 &
    sleep 3
fi
# pull model if missing
if ! ollama list 2>/dev/null | grep -q '${MODEL%%:*}'; then
    ollama pull '${MODEL}'
fi
echo '[node] ollama ok'
"

# ── 3. Install + start LibreChat via Podman (idempotent) ─────────────────────
log "ensuring LibreChat on node..."
node_ssh "
set -e
# install podman + podman-compose
if ! command -v podman >/dev/null 2>&1; then
    sudo apt-get install -y -q podman podman-compose
fi
if ! command -v podman-compose >/dev/null 2>&1; then
    sudo apt-get install -y -q podman-compose
fi

# clone LibreChat if missing
if [[ ! -d /opt/LibreChat ]]; then
    sudo git clone --depth=1 https://github.com/danny-avila/LibreChat.git /opt/LibreChat
    sudo chown -R \$USER:\$USER /opt/LibreChat
fi

cd /opt/LibreChat

# copy .env if missing
[[ -f .env ]] || cp .env.example .env

# write Ollama config
cat > librechat.yaml << 'YAML'
version: 1.1.5
endpoints:
  custom:
    - name: "Qwen on GPU"
      apiKey: "ollama"
      baseURL: "http://localhost:11434/v1/"
      models:
        default: ["qwen2.5-coder:14b"]
        fetch: true
      titleConvo: true
      titleModel: "qwen2.5-coder:14b"
      modelDisplayLabel: "Qwen 14B — Miranda"
YAML

# apply Miranda branding via .env overrides
grep -q 'APP_TITLE' .env || echo 'APP_TITLE=Miranda' >> .env
grep -q 'CUSTOM_FOOTER' .env || echo 'CUSTOM_FOOTER=Miranda — Berylize Labs' >> .env
grep -q 'HELP_AND_FAQ_URL' .env || echo 'HELP_AND_FAQ_URL=' >> .env

# start if not already running
if ! podman ps 2>/dev/null | grep -q librechat; then
    podman-compose up -d
    sleep 10
fi
echo '[node] librechat ok'
"

# ── 4. Open SSH tunnels ───────────────────────────────────────────────────────
log "opening tunnels (UI :${UI_PORT}, Ollama :${OLLAMA_PORT})..."

# kill stale tunnels
[[ -f "$UI_TUNNEL_PID" ]] && kill "$(cat "$UI_TUNNEL_PID")" 2>/dev/null || true
pkill -f "ssh.*-L ${UI_PORT}:localhost:${UI_PORT}" 2>/dev/null && sleep 1 || true

gc compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --quiet -- \
    -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
    -L "${UI_PORT}:localhost:${UI_PORT}" \
    -L "${OLLAMA_PORT}:localhost:${OLLAMA_PORT}" \
    >/tmp/beryl_ui_tunnel.log 2>&1 &
echo $! > "$UI_TUNNEL_PID"

# ── 5. Wait for LibreChat to respond (up to 60 s) ────────────────────────────
log "waiting for LibreChat to come up..."
for i in $(seq 1 60); do
    if curl -s -m 2 -o /dev/null -w "%{http_code}" "http://localhost:${UI_PORT}" \
        2>/dev/null | grep -qE "^[23]"; then
        break
    fi
    sleep 1
done

# ── 6. Final check + print URL ───────────────────────────────────────────────
if curl -s -m 3 -o /dev/null "http://localhost:${UI_PORT}"; then
    echo
    echo "======================================================"
    echo "  LibreChat + Qwen 14B — READY"
    echo "======================================================"
    echo
    echo "  Open this in your browser:"
    echo "  → http://localhost:${UI_PORT}"
    echo
    echo "  First time: click 'Sign up', create a local account"
    echo "  (stored only on the GPU node — nothing leaves it)"
    echo
    echo "  Select 'Qwen on GPU' from the model dropdown."
    echo
    echo "  Ollama API also at: http://localhost:${OLLAMA_PORT}/v1"
    echo
    echo "  To close:  bash deploy/open_ui.sh --stop"
    echo "  Logs:      /tmp/beryl_ui_tunnel.log"
    echo "======================================================"
else
    echo
    log "WARN: tunnel is up but LibreChat not responding yet."
    log "Wait 30 s then try: http://localhost:${UI_PORT}"
    log "Node logs: ssh berylize-node then: cd /opt/LibreChat && podman-compose logs --tail=30"
fi
