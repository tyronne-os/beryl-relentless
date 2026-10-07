#!/usr/bin/env bash
# gpu_off.sh — back to L1: close tunnel, stop render service, stop (default) or delete the VM.
# Usage: ./deploy/gpu_off.sh [--stop | --delete | --keep-vm]
set -uo pipefail
SCRIPT_TAG=gpu_off
cd "$(dirname "$0")/.."
source deploy/lib.sh
ACTION="${1:---stop}"

curl -s -m 5 -X POST "${CONTROLLER_URL}/stage/degrade" -H "Content-Type: application/json" \
    -d '{"reason":"gpu_off.sh"}' >/dev/null 2>&1 || log "controller not reachable — clients fall back to L1 on next request"

tunnel_stop
gc auth activate-service-account --key-file="$SA_KEY_PATH" --quiet >/dev/null 2>&1 || true
node_ssh "sudo systemctl stop beryl-render 2>/dev/null || true" >/dev/null 2>&1 || true

case "$ACTION" in
    --delete)  gc compute instances delete "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --quiet ;;
    --keep-vm) log "VM left running (still billing)" ;;
    *)         gc compute instances stop "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --quiet ;;
esac
log "=== L1 active ==="
