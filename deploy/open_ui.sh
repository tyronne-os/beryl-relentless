#!/usr/bin/env bash
# deploy/open_ui.sh — one command to open Miranda (LibreChat + Qwen + female Kokoro voice) on the GPU node.
#
# Usage:   bash deploy/open_ui.sh        (or: bash miranda.sh)
# Stop:    bash deploy/open_ui.sh --stop
#
# Idempotent. On the node it makes sure: Ollama (Qwen) listens on all interfaces, Kokoro TTS runs on
# :8012, LibreChat is configured (Qwen + female voices only + voice playback) and running.
# On your laptop it opens ONE tunnel for :3080 (site), :11434 (Qwen API), :8012 (Kokoro).
# Then open http://localhost:3080
set -uo pipefail
SCRIPT_TAG="open_ui"
source "$(dirname "$0")/lib.sh"

OLLAMA_PORT="${OLLAMA_PORT:-11434}"
UI_PORT="${UI_PORT:-3080}"
KOKORO_PORT="${KOKORO_PORT:-8012}"
UI_TUNNEL_PID="/tmp/beryl_ui_tunnel.pid"
MODEL="${OLLAMA_MODEL:-qwen2.5-coder:14b}"

if [[ "${1:-}" == "--stop" ]]; then
    [[ -f "$UI_TUNNEL_PID" ]] && kill "$(cat "$UI_TUNNEL_PID")" 2>/dev/null || true
    pkill -f "ssh.*-L ${UI_PORT}:localhost:${UI_PORT}" 2>/dev/null || true
    rm -f "$UI_TUNNEL_PID"
    log "tunnels closed."
    exit 0
fi

log "checking node..."
gc compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" \
    --format="value(status)" 2>/dev/null | grep -q RUNNING || {
    log "ERROR: berylize-node is not RUNNING. Start it first with: ./deploy/gpu_on.sh"
    exit 1
}
log "node is RUNNING."

# The remote script is sent base64-encoded so no quote character in it can break the ssh command line.
REMOTE_B64=$(base64 <<'REMOTE' | tr -d '\n'
set -e
MODEL="${MODEL:-qwen2.5-coder:14b}"

# ---- Ollama (Qwen). Listen on all interfaces so the LibreChat container can reach it.
command -v ollama >/dev/null 2>&1 || curl -fsSL https://ollama.com/install.sh | sudo sh
if systemctl list-unit-files 2>/dev/null | grep -q '^ollama.service'; then
    if [ ! -f /etc/systemd/system/ollama.service.d/override.conf ]; then
        sudo mkdir -p /etc/systemd/system/ollama.service.d
        printf '[Service]\nEnvironment=OLLAMA_HOST=0.0.0.0\n' | sudo tee /etc/systemd/system/ollama.service.d/override.conf >/dev/null
        sudo systemctl daemon-reload
        sudo systemctl restart ollama
    fi
elif ! curl -sf -m 2 localhost:11434/api/tags >/dev/null 2>&1; then
    OLLAMA_HOST=0.0.0.0 nohup ollama serve >/tmp/ollama.log 2>&1 &
fi
for i in $(seq 1 30); do curl -sf -m 2 localhost:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done
ollama list 2>/dev/null | grep -q "${MODEL%%:*}" || ollama pull "$MODEL"
echo "[node] ollama ok"

# ---- Podman, Kokoro TTS on :8012
command -v podman >/dev/null 2>&1 || sudo apt-get install -y -q podman
command -v podman-compose >/dev/null 2>&1 || sudo apt-get install -y -q podman-compose
if ! curl -sf -m 3 localhost:8012/health >/dev/null 2>&1; then
    podman rm -f kokoro >/dev/null 2>&1 || true
    podman run -d --name kokoro --restart=always -p 8012:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest
    for i in $(seq 1 150); do curl -sf -m 2 localhost:8012/health >/dev/null 2>&1 && break; sleep 1; done
fi
if curl -sf -m 3 localhost:8012/health >/dev/null 2>&1; then echo "[node] kokoro ok"; else echo "[node] WARN: kokoro not answering on :8012 (podman logs kokoro)"; fi

# ---- LibreChat
NODE_IP=$(hostname -I | awk '{print $1}')
if [ ! -d /opt/LibreChat ]; then
    sudo git clone --depth=1 https://github.com/danny-avila/LibreChat.git /opt/LibreChat
    sudo chown -R "$USER:$USER" /opt/LibreChat
fi
cd /opt/LibreChat
[ -f .env ] || cp .env.example .env

cat > librechat.yaml <<YAML
version: 1.2.1
cache: true
endpoints:
  custom:
    - name: "Qwen on GPU"
      apiKey: "ollama"
      baseURL: "http://${NODE_IP}:11434/v1/"
      models:
        default: ["${MODEL}"]
        fetch: true
      titleConvo: true
      titleModel: "${MODEL}"
      modelDisplayLabel: "Miranda"
speech:
  tts:
    openai:
      url: "http://${NODE_IP}:8012/v1/audio/speech"
      apiKey: "kokoro"
      model: "kokoro"
      voices: ["af_heart", "af_bella", "af_sarah", "af_nicole", "af_sky", "bf_emma", "bf_isabella"]
  speechTab:
    conversationMode: true
    advancedMode: false
    speechToText:
      engineSTT: "browser"
      languageSTT: "English (US)"
      autoTranscribeAudio: true
      decibelValue: -45
      autoSendText: 0
    textToSpeech:
      engineTTS: "external"
      voice: "af_heart"
      languageTTS: "en"
      automaticPlayback: true
      playbackRate: 1.0
      cacheTTS: true
YAML

cat > docker-compose.override.yml <<'YAML'
services:
  api:
    volumes:
      - type: bind
        source: ./librechat.yaml
        target: /app/librechat.yaml
YAML

sed -i '/^APP_TITLE=/d;/^CUSTOM_FOOTER=/d;/^HELP_AND_FAQ_URL=/d' .env
printf 'APP_TITLE=Miranda\nCUSTOM_FOOTER=Miranda - Berylize Labs\nHELP_AND_FAQ_URL=\n' >> .env

podman-compose down >/dev/null 2>&1 || true
podman-compose up -d
sleep 10
echo "[node] librechat ok"
REMOTE
)

log "configuring Qwen + Kokoro (female voices) + LibreChat on the node (first run takes a few minutes)..."
node_ssh "export MODEL='${MODEL}'; echo '${REMOTE_B64}' | base64 -d | bash" || {
    log "ERROR: node setup failed — the lines above say which step."
    exit 1
}

log "opening tunnels (site :${UI_PORT}, Qwen :${OLLAMA_PORT}, Kokoro :${KOKORO_PORT})..."
[[ -f "$UI_TUNNEL_PID" ]] && kill "$(cat "$UI_TUNNEL_PID")" 2>/dev/null || true
pkill -f "ssh.*-L ${UI_PORT}:localhost:${UI_PORT}" 2>/dev/null && sleep 1 || true

gc compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --quiet -- \
    -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
    -L "${UI_PORT}:localhost:${UI_PORT}" \
    -L "${OLLAMA_PORT}:localhost:${OLLAMA_PORT}" \
    -L "${KOKORO_PORT}:localhost:${KOKORO_PORT}" \
    >/tmp/beryl_ui_tunnel.log 2>&1 &
echo $! > "$UI_TUNNEL_PID"

log "waiting for the site..."
for _ in $(seq 1 90); do
    curl -s -m 2 -o /dev/null -w "%{http_code}" "http://localhost:${UI_PORT}" 2>/dev/null | grep -qE "^[23]" && break
    sleep 1
done

if curl -s -m 3 -o /dev/null "http://localhost:${UI_PORT}"; then
    echo
    echo "======================================================"
    echo "  MIRANDA — READY"
    echo "======================================================"
    echo "  Open in your browser (Chrome works best for the mic):"
    echo "      http://localhost:${UI_PORT}"
    echo
    echo "  First time: click 'Sign up', make a local account."
    echo "  Pick 'Qwen on GPU' (Miranda) in the model menu."
    echo "  Voice: speaker icon plays replies in a female Kokoro voice (Heart default);"
    echo "         headphones icon = hands-free conversation mode; mic = dictate."
    echo "  Change voice: Settings > Speech > Voice (only the 7 female voices are listed)."
    echo
    echo "  Close: bash deploy/open_ui.sh --stop     Tunnel log: /tmp/beryl_ui_tunnel.log"
    echo "======================================================"
else
    echo
    log "WARN: tunnel is up but the site is not answering yet. Wait 30 s, then open http://localhost:${UI_PORT}"
    log "If it never comes up: gcloud compute ssh ${INSTANCE} then: cd /opt/LibreChat && podman-compose logs --tail=40"
fi
