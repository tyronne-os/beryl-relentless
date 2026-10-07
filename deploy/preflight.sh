#!/usr/bin/env bash
# preflight.sh — read-only checks before gpu_on.sh. Changes nothing.
# Exit 0 = safe to fire. Exit 1 = at least one FAIL (fix it first).
# Usage: ./deploy/preflight.sh

set -uo pipefail
SCRIPT_TAG=preflight
cd "$(dirname "$0")/.."
# shellcheck source=deploy/lib.sh
source deploy/lib.sh

FAILS=0; WARNS=0
pass() { printf '  [ OK ] %s\n' "$1"; }
warn() { printf '  [WARN] %s\n' "$1"; WARNS=$((WARNS+1)); }
fail() { printf '  [FAIL] %s\n         -> %s\n' "$1" "$2"; FAILS=$((FAILS+1)); }

echo "== LOCAL =="
for c in gcloud curl python3 ssh; do
    command -v "$c" >/dev/null && pass "$c installed" || fail "$c missing" "install it (gcloud: sudo apt install google-cloud-cli)"
done

if [[ ! -f "$SA_KEY_PATH" ]]; then
    fail "SA key file not found: $SA_KEY_PATH" "fix GCP_SA_KEY_JSON_PATH in .env (quote paths containing spaces)"
else
    KEYINFO=$(python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(d.get('project_id',''),d.get('client_email',''))" "$SA_KEY_PATH" 2>/dev/null || true)
    if [[ -z "$KEYINFO" ]]; then
        fail "SA key is not valid JSON" "re-download the key from GCP Console"
    elif [[ "${KEYINFO%% *}" != "$PROJECT" ]]; then
        fail "SA key belongs to project '${KEYINFO%% *}', expected '$PROJECT'" "use the posh-eden key"
    else
        pass "SA key valid (${KEYINFO#* })"
    fi
fi

if [[ -z "${HF_TOKEN:-}" || "${HF_TOKEN}" == hf_xxx* || "${HF_TOKEN}" == *yourrealtoken* ]]; then
    fail "HF_TOKEN is unset or still a placeholder" "put your real token in .env (huggingface.co/settings/tokens)"
else
    CODE=$(curl -s -o /dev/null -m 8 -w '%{http_code}' -H "Authorization: Bearer $HF_TOKEN" https://huggingface.co/api/whoami-v2 || true)
    [[ "$CODE" == "200" ]] && pass "HF_TOKEN accepted by Hugging Face" || fail "HF_TOKEN rejected (HTTP $CODE)" "create a new read token"
fi

if ls bakeoff/fixtures/reference.jpg bakeoff/fixtures/reference.png >/dev/null 2>&1; then
    pass "reference face photo present"
else
    fail "no reference photo at bakeoff/fixtures/reference.jpg" "run ./deploy/prep_fixture.sh then ./deploy/prep_fixture.sh <number>"
fi

if (( FAILS > 0 )); then
    echo; echo "Stopping: fix the local FAILs first (remote checks need a working key)."; exit 1
fi

echo "== GCP =="
gc auth activate-service-account --key-file="$SA_KEY_PATH" --quiet >/dev/null 2>&1 \
    && pass "service account activated" || fail "gcloud auth failed" "key revoked/rotated? download a fresh one"
STATUS=$(gc compute instances describe "$INSTANCE" --zone="$ZONE" --project="$PROJECT" --format='value(status)' 2>/dev/null || true)
case "$STATUS" in
    RUNNING)            pass "$INSTANCE is RUNNING" ;;
    TERMINATED|STOPPED) warn "$INSTANCE is $STATUS (gpu_on.sh will start it)"; STATUS_STOPPED=1 ;;
    *)                  fail "$INSTANCE not found / no permission (status='$STATUS')" "check SA roles: compute.admin + iam.serviceAccountUser" ;;
esac

if (( FAILS == 0 )) && [[ "${STATUS_STOPPED:-0}" != 1 ]]; then
    echo "== NODE (one SSH round-trip) =="
    REMOTE=$(node_ssh '
        echo "gpu=$(nvidia-smi --query-gpu=name,memory.free --format=csv,noheader 2>/dev/null | tr -d " " || echo none)"
        echo "disk_gb=$(df -BG --output=avail /opt 2>/dev/null | tail -1 | tr -dc 0-9)"
        echo "venv=$([ -f /opt/beryl/venv-fh/.ready ] && echo yes || echo no)"
        echo "unit_port=$(grep -o "RENDER_PORT=[0-9]*" /etc/systemd/system/beryl-render.service 2>/dev/null | cut -d= -f2)"
        echo "svc=$(systemctl is-active beryl-render 2>/dev/null)"
        echo "port_owner=$(sudo ss -ltnp 2>/dev/null | grep ":'"$RENDER_PORT"' " | grep -o "users:((\"[a-z0-9._-]*\"" | head -1)"
        echo "weights=$([ -f /opt/beryl/weights/.fh_ready ] && du -sm /opt/beryl/weights/*/ 2>/dev/null | tr "\t" ":" | tr "\n" " ")"
        echo "flashhead_mod=$([ -d /opt/beryl/flashhead/flash_head ] && echo ok)"
    ' 2>/dev/null) || REMOTE=""
    if [[ -z "$REMOTE" ]]; then
        fail "SSH to $INSTANCE failed" "run: gcloud compute ssh $INSTANCE --zone=$ZONE and read the error"
    else
        get() { echo "$REMOTE" | sed -n "s/^$1=//p"; }
        G=$(get gpu);       [[ "$G" == *L4* || "$G" == *T4* ]] && pass "GPU visible: $G" || fail "no GPU visible to nvidia-smi" "driver missing; setup_gpu_node.sh will try, may need reboot"
        D=$(get disk_gb);   [[ "${D:-0}" -ge 30 ]] && pass "disk free: ${D} GB" || fail "only ${D:-?} GB free on /opt" "weights need ~30 GB; delete old model files"
        [[ "$(get venv)" == yes ]] && pass "python venv present" || warn "FlashHead venv not built yet (first run ~10 min)"
        UP=$(get unit_port)
        if [[ -z "$UP" ]]; then warn "systemd unit not installed yet (gpu_on.sh will write it)"
        elif [[ "$UP" != "$RENDER_PORT" ]]; then warn "unit uses port $UP but config says $RENDER_PORT (gpu_on.sh rewrites it)"
        else pass "unit port matches ($RENDER_PORT)"; fi
        PO=$(get port_owner); SV=$(get svc)
        if [[ -n "$PO" && "$SV" != active ]]; then fail "port $RENDER_PORT already used by another process: $PO" "pick another RENDER_PORT"
        else pass "port $RENDER_PORT free or ours (service: ${SV:-none})"; fi
        W=$(get weights); [[ -n "$W" ]] && pass "weights on disk: $W" || warn "no model weights on node yet -> render runs in PASSTHROUGH mode"
        FM=$(get flashhead_mod); [[ "$FM" == ok ]] && pass "FlashHead code present" || warn "FlashHead code not installed yet (gpu_on.sh installs it)"
    fi
fi

echo
if (( FAILS > 0 )); then echo "PREFLIGHT: $FAILS FAIL, $WARNS warn — do not fire."; exit 1; fi
echo "PREFLIGHT: GO ($WARNS warn)."
