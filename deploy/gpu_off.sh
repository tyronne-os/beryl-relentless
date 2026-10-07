#!/usr/bin/env bash
# gpu_off.sh — deactivate L2, return to L1, stop/delete spot VM
# Usage: ./deploy/gpu_off.sh [--stop | --delete]
#   --stop   (default): stop the VM but keep disk (fast restart)
#   --delete: delete the VM entirely (cheapest; disk stays if separate)

set -euo pipefail

PROJECT="${GCP_PROJECT:-posh-eden}"
ZONE="${GCP_ZONE:-us-east1-c}"
INSTANCE="berylize-node"
CONTROLLER_URL="${CONTROLLER_URL:-http://localhost:8000}"
SA_KEY_PATH="${GCP_SA_KEY_JSON_PATH:-/tmp/sa.json}"
ACTION="${1:---stop}"

log() { echo "[gpu_off] $*" >&2; }

gcloud_cmd() {
    CLOUDSDK_AUTH_ACCESS_TOKEN="" \
    GOOGLE_APPLICATION_CREDENTIALS="$SA_KEY_PATH" \
    gcloud "$@" --project="$PROJECT"
}

# 1. Tell controller to degrade to L1
log "Signalling controller: degrade L2→L1..."
curl -s -X POST "${CONTROLLER_URL}/stage/degrade" \
    -H "Content-Type: application/json" \
    -d '{"reason": "gpu_off.sh called"}' \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('[gpu_off] stage:', d.get('stage','?'))" \
    2>/dev/null || log "Controller not reachable — clients will fall back to L1 on next request"

# 2. Stop render service on node (graceful)
log "Stopping render service on $INSTANCE..."
gcloud_cmd compute ssh "$INSTANCE" --zone="$ZONE" \
    --command="sudo systemctl stop beryl-render 2>/dev/null || true" 2>/dev/null || true

# 3. Stop or delete the VM
if [[ "$ACTION" == "--delete" ]]; then
    log "Deleting VM $INSTANCE..."
    gcloud_cmd compute instances delete "$INSTANCE" --zone="$ZONE" --quiet
    log "VM deleted — restart with gpu_on.sh"
else
    log "Stopping VM $INSTANCE (disk preserved)..."
    gcloud_cmd compute instances stop "$INSTANCE" --zone="$ZONE"
    log "VM stopped — restart fast with gpu_on.sh (--skip-deploy if code unchanged)"
fi

log "=== L1 ACTIVE — GPU cost: \$0/hr ==="
