#!/usr/bin/env bash
# Shared helpers for gpu_on.sh / gpu_off.sh / preflight.sh. Source, don't run.

PROJECT="${GCP_PROJECT:-posh-eden}"
ZONE="${GCP_ZONE:-us-east1-c}"
INSTANCE="${GCP_INSTANCE:-berylize-node}"
SA_KEY_PATH="${GCP_SA_KEY_JSON_PATH:-/tmp/sa.json}"
RENDER_PORT="${RENDER_PORT:-9523}"
CONTROLLER_URL="${CONTROLLER_URL:-http://localhost:9500}"
TUNNEL_PID_FILE="/tmp/beryl_tunnel.pid"

log()  { echo "[${SCRIPT_TAG:-beryl}] $*" >&2; }

# gcloud with the platform proxy token cleared and our SA key in charge
gc() {
    CLOUDSDK_AUTH_ACCESS_TOKEN="" GOOGLE_APPLICATION_CREDENTIALS="$SA_KEY_PATH" gcloud "$@"
}

# run one command string on the node over SSH (port 22 only; no firewall changes)
node_ssh() {
    gc compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --quiet \
        --ssh-flag="-o ConnectTimeout=10" --ssh-flag="-o ServerAliveInterval=15" \
        --command="$1"
}

tunnel_up() {
    [[ -f "$TUNNEL_PID_FILE" ]] && kill -0 "$(cat "$TUNNEL_PID_FILE")" 2>/dev/null
}

tunnel_start() {
    tunnel_up && return 0
    gc compute ssh "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --quiet -- \
        -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 \
        -L "${RENDER_PORT}:localhost:${RENDER_PORT}" >/tmp/beryl_tunnel.log 2>&1 &
    echo $! > "$TUNNEL_PID_FILE"
    for _ in $(seq 1 15); do
        curl -s -m 2 -o /dev/null "http://localhost:${RENDER_PORT}/health" && return 0
        tunnel_up || break
        sleep 1
    done
    return 0   # tunnel may be up while service still warming; caller probes
}

tunnel_stop() {
    tunnel_up && kill "$(cat "$TUNNEL_PID_FILE")" 2>/dev/null
    rm -f "$TUNNEL_PID_FILE"
}
