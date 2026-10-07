#!/usr/bin/env bash
# enter_keys.sh — guided key wizard. Prompts one key at a time; writes to .env.
# At the end, optionally pushes all keys to GCP Secret Manager.
# Usage: bash deploy/enter_keys.sh
# Paste each value and press Enter. Leave blank to skip a key.
set -uo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=".env"
PUSHED=0

_heading() { printf '\n\033[1;36m%s\033[0m\n' "=== $1 ==="; }
_ask()     {
    local KEY=$1 HINT=$2
    printf '  \033[1m%-32s\033[0m  # %s\n' "$KEY" "$HINT"
    printf '  Paste value (Enter to skip): '
    read -r VAL
    [[ -z "$VAL" ]] && return
    # Remove key if already present, then append
    grep -v "^${KEY}=" "$ENV_FILE" > "${ENV_FILE}.tmp" 2>/dev/null || true
    printf '%s=%s\n' "$KEY" "$VAL" >> "${ENV_FILE}.tmp"
    mv "${ENV_FILE}.tmp" "$ENV_FILE"
    printf '  \033[32m✓ saved\033[0m\n'
}

# Ensure .env exists and is gitignored
touch "$ENV_FILE"
if ! git check-ignore -q "$ENV_FILE" 2>/dev/null; then
    printf '\033[33mWARN: .env is not gitignored — add it before committing\033[0m\n'
fi

printf '\033[1mBeryl key wizard — paste each value and press Enter (blank = skip)\033[0m\n'

_heading "Hugging Face"
_ask HF_TOKEN               "read token from hf.co/settings/tokens"
_ask HUGGING_FACE_HUB_TOKEN "same token — some libs read this name"

_heading "NVIDIA / NGC"
_ask NVIDIA_API_KEY     "NGC API key (enterprise NIM)"
_ask NGC_API_KEY        "NGC container pull key"
_ask NGC_ENTERPRISE_KEY "enterprise NIM endpoints"

_heading "Anthropic (Claude judge)"
_ask ANTHROPIC_API_KEY "sk-ant-... from console.anthropic.com/settings/keys"

_heading "GitHub"
_ask GITHUB_TOKEN "ghp_... from github.com/settings/tokens"
_ask GH_TOKEN     "same token — gh CLI reads this name"

_heading "TypeSafe / JEV"
_ask TYPESAFE_API_KEY "from app.typesafe.ai (also: JEV_API_KEY)"
_ask JEV_API_KEY      "same key, alternate env name"

_heading "Google Cloud"
_ask GCP_SA_KEY_JSON '{\"type\":\"service_account\",...} — paste the full JSON on one line'
_ask GCP_ZONE        "e.g. us-east1-c"

_heading "Kaggle"
_ask KAGGLE_USERNAME "your Kaggle username"
_ask KAGGLE_KEY      "from kaggle.com/settings → API"

_heading "Pipeline flags (defaults shown)"
printf '  CRANE_JEV=true  CRANE_JEV_MODEL=jev-latest  CRANE_STAGE=L1\n'
printf '  Press Enter to keep defaults, or paste overrides:\n'
_ask CRANE_JEV       "true / false"
_ask CRANE_JEV_MODEL "jev-latest"
_ask CRANE_STAGE     "L0 / L1 / L2"

printf '\n\033[1mKeys saved to %s\033[0m\n' "$ENV_FILE"

# Offer Secret Manager push
printf '\nPush to GCP Secret Manager now? (requires gcloud auth) [y/N] '
read -r PUSH
if [[ "${PUSH,,}" == "y" ]]; then
    if [[ -f deploy/push_secrets.sh ]]; then
        # shellcheck disable=SC1091
        set -a; source "$ENV_FILE"; set +a
        bash deploy/push_secrets.sh
        PUSHED=1
    else
        printf '\033[33mdeploy/push_secrets.sh not found — skipping push\033[0m\n'
    fi
fi

[[ $PUSHED -eq 0 ]] && printf '\nTo push to Secret Manager later: source .env && bash deploy/push_secrets.sh\n'
printf '\nDone.\n'
