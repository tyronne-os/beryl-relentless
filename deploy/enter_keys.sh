#!/usr/bin/env bash
# enter_keys.sh — you paste the keys, everything else is automatic.
# Asks only for keys not already in .env, derives the alias names, fills the
# defaults, authenticates gcloud, and pushes to Secret Manager. Safe to re-run.
set -uo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=".env"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

have() { grep -q "^$1=.\+" "$ENV_FILE"; }
put()  {
    grep -v "^$1=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
    printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"; chmod 600 "$ENV_FILE"
}
get()  { grep "^$1=" "$ENV_FILE" | tail -n1 | cut -d= -f2-; }

# ask KEY "label" [alias ...]  — paste, hidden; blank keeps what is already there
ask() {
    local key=$1 label=$2; shift 2
    if have "$key"; then
        printf '  %-22s already set\n' "$key"
    else
        local v=""
        while [[ -z "$v" ]]; do
            printf '  %-22s (%s)\n  paste: ' "$key" "$label"
            read -rs v; echo
            v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
            [[ -z "$v" ]] && echo "  (empty, paste again)"
        done
        put "$key" "$v"
    fi
    local a
    for a in "$@"; do put "$a" "$(get "$key")"; done
}

echo "Paste each key when asked. Nothing else is needed."
echo
ask HF_TOKEN         "Hugging Face read token"        HUGGING_FACE_HUB_TOKEN
ask NVIDIA_API_KEY   "NVIDIA / NGC key"               NGC_API_KEY NGC_ENTERPRISE_KEY
ask ANTHROPIC_API_KEY "Anthropic key (Claude judge)"
ask GITHUB_TOKEN     "GitHub token"                   GH_TOKEN
ask TYPESAFE_API_KEY "TypeSafe / JEV key"             JEV_API_KEY
ask KAGGLE_USERNAME  "Kaggle username"
ask KAGGLE_KEY       "Kaggle key"

# GCP service-account JSON: paste it on one line, or give the path to the .json file
if ! have GCP_SA_KEY_JSON; then
    while true; do
        printf '  %-22s (service-account JSON: paste it, or give the file path)\n  paste: ' GCP_SA_KEY_JSON
        read -rs raw; echo
        if [[ -f "$raw" ]]; then raw=$(python3 -c 'import json,sys;print(json.dumps(json.load(open(sys.argv[1]))))' "$raw") || raw=""; fi
        if python3 -c 'import json,sys;d=json.loads(sys.argv[1]);assert d.get("private_key")' "$raw" 2>/dev/null; then
            put GCP_SA_KEY_JSON "$(python3 -c 'import json,sys;print(json.dumps(json.loads(sys.argv[1])))' "$raw")"
            break
        fi
        echo "  (that is not a complete service-account JSON, try again)"
    done
else
    printf '  %-22s already set\n' GCP_SA_KEY_JSON
fi

have GCP_ZONE        || put GCP_ZONE us-east1-c
have CRANE_JEV       || put CRANE_JEV true
have CRANE_JEV_MODEL || put CRANE_JEV_MODEL jev-latest
have CRANE_STAGE     || put CRANE_STAGE L1

echo
echo "Saved to .env (private, gitignored). Pushing to Secret Manager..."

SA=$(mktemp); chmod 600 "$SA"
trap 'shred -u "$SA" 2>/dev/null || rm -f "$SA"' EXIT
get GCP_SA_KEY_JSON > "$SA"

if ! command -v gcloud >/dev/null; then
    echo "gcloud is not installed here. Keys are in .env; run this again on a machine with gcloud."
    exit 0
fi
if gcloud auth activate-service-account --key-file="$SA" --quiet >/dev/null 2>&1; then
    cp "$SA" /tmp/sa.json; chmod 600 /tmp/sa.json
    export CLOUDSDK_AUTH_ACCESS_TOKEN=""
    bash deploy/push_secrets.sh "$ENV_FILE" && echo && echo "DONE. You never need to paste these again."
else
    echo "Could not authenticate with that service-account key. Check GCP_SA_KEY_JSON, then re-run (the other keys are kept)."
    exit 1
fi
