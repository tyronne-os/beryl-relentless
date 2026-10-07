#!/usr/bin/env bash
# gpu_on.sh — activate L2 cinematic render on berylize-node
# Usage: ./deploy/gpu_on.sh [--skip-deploy] [--bakeoff-only]
#
# What it does:
#   1. Verify berylize-node is RUNNING (create spot VM if not)
#   2. Deploy latest render service code to the node
#   3. Install GPU deps (torch+cuda, flashhead/leaptalk weights)
#   4. Start render service + health probe (port 9523)
#   5. Register GPU node with controller (flip L1→L2)
#   6. Run bakeoff scorecard and print results
#
# Env vars required (load from .env or GCP Secret Manager):
#   GCP_SA_KEY_JSON, GCP_ZONE, GITHUB_TOKEN, HF_TOKEN, CONTROLLER_URL
# Never commit .env — secrets stay in GCP Secret Manager.

set -euo pipefail

# ── config ────────────────────────────────────────────────────────────────
PROJECT="${GCP_PROJECT:-posh-eden}"
ZONE="${GCP_ZONE:-us-east1-c}"
INSTANCE="berylize-node"
RENDER_PORT=9523
CONTROLLER_URL="${CONTROLLER_URL:-http://localhost:9500}"
REPO="https://github.com/tyronne-os/beryl-relentless"
BRANCH="${GIT_BRANCH:-main}"
WEIGHTS_DIR="/opt/beryl/weights"
SERVICE_DIR="/opt/beryl/render"
HEALTH_TIMEOUT=120   # seconds to wait for warm
SKIP_DEPLOY="${1:-}"
SA_KEY_PATH="${GCP_SA_KEY_JSON_PATH:-/tmp/sa.json}"

log() { echo "[gpu_on] $*" >&2; }

# ── GCP auth (override proxy-injected token) ──────────────────────────────
gcloud_auth() {
    if [[ -f "$SA_KEY_PATH" ]]; then
        CLOUDSDK_AUTH_ACCESS_TOKEN="" GOOGLE_APPLICATION_CREDENTIALS="$SA_KEY_PATH" \
            gcloud auth activate-service-account --key-file="$SA_KEY_PATH" --project="$PROJECT" 2>/dev/null || true
    fi
}

gcloud_cmd() {
    CLOUDSDK_AUTH_ACCESS_TOKEN="" \
    GOOGLE_APPLICATION_CREDENTIALS="$SA_KEY_PATH" \
    gcloud "$@" --project="$PROJECT"
}

# ── 1. Ensure instance is running ─────────────────────────────────────────
ensure_instance_running() {
    log "Checking $INSTANCE status..."
    STATUS=$(gcloud_cmd compute instances describe "$INSTANCE" \
        --zone="$ZONE" --format="value(status)" 2>/dev/null || echo "MISSING")

    if [[ "$STATUS" == "RUNNING" ]]; then
        log "$INSTANCE already RUNNING — skipping create"
        return 0
    fi

    if [[ "$STATUS" == "TERMINATED" || "$STATUS" == "STOPPED" ]]; then
        log "Starting stopped instance $INSTANCE..."
        gcloud_cmd compute instances start "$INSTANCE" --zone="$ZONE"
        wait_for_ssh
        return 0
    fi

    # Create spot VM from base image
    log "Creating spot VM $INSTANCE (g2-standard-4 + L4)..."
    gcloud_cmd compute instances create "$INSTANCE" \
        --zone="$ZONE" \
        --machine-type="g2-standard-4" \
        --accelerator="type=nvidia-l4,count=1" \
        --maintenance-policy=TERMINATE \
        --provisioning-model=SPOT \
        --instance-termination-action=STOP \
        --image-family="ubuntu-2204-lts" \
        --image-project="ubuntu-os-cloud" \
        --boot-disk-size=200GB \
        --boot-disk-type=pd-ssd \
        --metadata="install-nvidia-driver=true" \
        --tags="beryl-render,http-server" \
        --scopes="cloud-platform"
    wait_for_ssh
}

wait_for_ssh() {
    log "Waiting for SSH on $INSTANCE..."
    for i in $(seq 1 30); do
        if gcloud_cmd compute ssh "$INSTANCE" --zone="$ZONE" \
            --command="echo ok" --ssh-flag="-o ConnectTimeout=5" 2>/dev/null; then
            log "SSH ready"
            return 0
        fi
        sleep 5
    done
    log "ERROR: SSH timeout after 150s"
    exit 1
}

# ── 2. Deploy render service code ─────────────────────────────────────────
deploy_code() {
    if [[ "$SKIP_DEPLOY" == "--skip-deploy" || "$SKIP_DEPLOY" == "--bakeoff-only" ]]; then
        log "Skipping code deploy (flag set)"
        return 0
    fi

    log "Deploying render service to $INSTANCE..."
    NODE_EXTERNAL_IP=$(gcloud_cmd compute instances describe "$INSTANCE" \
        --zone="$ZONE" --format="value(networkInterfaces[0].accessConfigs[0].natIP)")

    # Push render service + GPU deps setup script
    gcloud_cmd compute scp \
        deploy/render_service.py \
        deploy/setup_gpu_node.sh \
        "${INSTANCE}:/tmp/" \
        --zone="$ZONE"

    gcloud_cmd compute ssh "$INSTANCE" --zone="$ZONE" --command="
        set -e
        export HF_TOKEN='${HF_TOKEN:-}'
        sudo mkdir -p $SERVICE_DIR $WEIGHTS_DIR
        sudo mv /tmp/render_service.py $SERVICE_DIR/
        sudo bash /tmp/setup_gpu_node.sh
    "
    log "Code deployed"
}

# ── 3. Start render service ────────────────────────────────────────────────
start_render_service() {
    log "Starting render service on port $RENDER_PORT..."
    gcloud_cmd compute ssh "$INSTANCE" --zone="$ZONE" --command="
        sudo systemctl daemon-reload
        sudo systemctl enable beryl-render 2>/dev/null || true
        sudo systemctl restart beryl-render
        echo 'render service started'
    "
}

# ── 4. Health probe ────────────────────────────────────────────────────────
wait_for_warm() {
    log "Health probing render service (timeout ${HEALTH_TIMEOUT}s)..."
    NODE_IP=$(gcloud_cmd compute instances describe "$INSTANCE" \
        --zone="$ZONE" --format="value(networkInterfaces[0].accessConfigs[0].natIP)")

    local elapsed=0
    while [[ $elapsed -lt $HEALTH_TIMEOUT ]]; do
        STATUS_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
            "http://${NODE_IP}:${RENDER_PORT}/health" --max-time 3 2>/dev/null || echo "000")
        if [[ "$STATUS_CODE" == "200" ]]; then
            log "Render service WARM (${elapsed}s)"
            echo "$NODE_IP"
            return 0
        fi
        sleep 3
        elapsed=$((elapsed + 3))
        log "  waiting... ${elapsed}s (last status: $STATUS_CODE)"
    done
    log "ERROR: render service did not warm in ${HEALTH_TIMEOUT}s"
    exit 1
}

# ── 5. Register with controller and flip L1→L2 ────────────────────────────
flip_to_l2() {
    local node_ip="$1"
    log "Registering GPU node with controller → flipping L1→L2..."
    curl -s -X POST "${CONTROLLER_URL}/stage/upgrade" \
        -H "Content-Type: application/json" \
        -d "{\"target\": \"L2\", \"gpu_url\": \"http://${node_ip}:${RENDER_PORT}\"}" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print('[gpu_on] stage:', d.get('stage','?'), '|', d.get('reason',''))" \
        2>/dev/null || log "Controller not reachable — flip manually via API"
}

# ── 6. Bakeoff ────────────────────────────────────────────────────────────
run_bakeoff() {
    local node_ip="${1:-localhost}"
    log "Running bakeoff scorecard..."
    python3 bakeoff/scorecard_runner.py \
        --render-url "http://${node_ip}:${RENDER_PORT}" \
        --verify-url "${CONTROLLER_URL:-http://localhost:9525}" \
        --output "bakeoff/results/$(date +%Y%m%d_%H%M%S).json" \
        2>&1 | tee /tmp/bakeoff_latest.log
    log "Bakeoff complete — results in bakeoff/results/"
}

# ── main ──────────────────────────────────────────────────────────────────
main() {
    log "=== GPU ON: $INSTANCE ($PROJECT / $ZONE) ==="
    gcloud_auth
    ensure_instance_running
    deploy_code
    start_render_service
    NODE_IP=$(wait_for_warm)
    flip_to_l2 "$NODE_IP"
    if [[ "$SKIP_DEPLOY" != "--skip-deploy" ]]; then
        run_bakeoff "$NODE_IP"
    fi
    log "=== L2 ACTIVE — render node: http://${NODE_IP}:${RENDER_PORT} ==="
}

main "$@"
