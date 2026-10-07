#!/usr/bin/env bash
# gpu_on.sh — activate L2 on berylize-node.
# Usage: ./deploy/gpu_on.sh [--no-bakeoff]
#
# Flow: preflight -> ensure VM running -> sync code (idempotent) -> restart service
#       -> health-wait ON THE NODE (over SSH, no firewall needed) -> open SSH tunnel
#       -> flip controller L1->L2 -> bakeoff.
# Every step is safe to re-run. There is no skip flag: setup_gpu_node.sh is idempotent.

set -euo pipefail
SCRIPT_TAG=gpu_on
cd "$(dirname "$0")/.."
source deploy/lib.sh

HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-300}"   # service listens only after the model loads
RUN_BAKEOFF=1; [[ "${1:-}" == "--no-bakeoff" ]] && RUN_BAKEOFF=0

./deploy/predeploy.sh || { log "predeploy gate failed — nothing was changed"; exit 1; }
./deploy/preflight.sh || { log "preflight failed — fix the FAILs above, nothing was changed"; exit 1; }

gc auth activate-service-account --key-file="$SA_KEY_PATH" --quiet >/dev/null 2>&1

STATUS=$(gc compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --format='value(status)')
if [[ "$STATUS" != "RUNNING" ]]; then
    log "starting $INSTANCE (was $STATUS)..."
    gc compute instances start "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --quiet
    for _ in $(seq 1 30); do node_ssh "true" >/dev/null 2>&1 && break; sleep 5; done
fi

log "syncing code to node..."
gc compute scp deploy/render_service.py deploy/setup_gpu_node.sh \
    "${INSTANCE}:/tmp/" --zone="$ZONE" --project="$PROJECT" --quiet
node_ssh "set -e
    export HF_TOKEN='${HF_TOKEN}' RENDER_PORT='${RENDER_PORT}'
    sudo mkdir -p /opt/beryl/render /opt/beryl/weights
    sudo cp /tmp/render_service.py /opt/beryl/render/render_service.py
    sudo -E bash /tmp/setup_gpu_node.sh
    sudo systemctl daemon-reload
    sudo systemctl enable beryl-render >/dev/null 2>&1 || true
    sudo systemctl restart beryl-render"

log "waiting for service on the node (up to ${HEALTH_TIMEOUT}s)..."
if ! node_ssh "for i in \$(seq 1 $((HEALTH_TIMEOUT/2))); do
        curl -sf -m 2 localhost:${RENDER_PORT}/health && exit 0
        systemctl is-active --quiet beryl-render || { echo SERVICE_DIED; exit 2; }
        sleep 2
    done; exit 1"; then
    log "service did not become healthy. Last logs:"
    node_ssh "sudo journalctl -u beryl-render -n 30 --no-pager" || true
    exit 1
fi
echo >&2

log "opening SSH tunnel localhost:${RENDER_PORT} -> node..."
tunnel_start
curl -sf -m 5 "http://localhost:${RENDER_PORT}/health" >/dev/null \
    && log "tunnel OK" || log "WARN: tunnel not answering yet (see /tmp/beryl_tunnel.log)"

HEALTH=$(node_ssh "curl -s -m 5 localhost:${RENDER_PORT}/health" 2>/dev/null || echo '{}')
MODEL=$(echo "$HEALTH" | python3 -c "import sys,json;print(json.load(sys.stdin).get('model','?'))" 2>/dev/null || echo "?")
if [[ "$MODEL" != flashhead-* ]]; then
    log "FAIL: render model = '$MODEL' (not a real model). load_error:"
    echo "$HEALTH" | python3 -c "import sys,json;print('   ', json.load(sys.stdin).get('load_error'))" 2>/dev/null || echo "    $HEALTH"
    log "last service logs:"
    node_ssh "sudo journalctl -u beryl-render -n 30 --no-pager" || true
    log "L2 is NOT live. Tunnel left open for debugging; ./deploy/gpu_off.sh --keep-vm closes it."
    exit 1
fi
log "model loaded: $MODEL"

log "flipping controller to L2..."
curl -s -m 5 -X POST "${CONTROLLER_URL}/stage/upgrade" -H "Content-Type: application/json" \
    -d "{\"target\":\"L2\",\"gpu_url\":\"http://localhost:${RENDER_PORT}\"}" >/dev/null \
    && log "controller flipped" || log "controller not running locally (ok for now) — skipping flip"

if (( RUN_BAKEOFF )); then
    mkdir -p bakeoff/results
    BPY=python3; [[ -x .venv/bin/python ]] && BPY=.venv/bin/python
    "$BPY" bakeoff/scorecard_runner.py \
        --render-url "http://localhost:${RENDER_PORT}" \
        --verify-url "http://localhost:9525" \
        --output "bakeoff/results/$(date +%Y%m%d_%H%M%S).json" || true
fi
log "=== done. tunnel stays open; stop it with ./deploy/gpu_off.sh ==="
