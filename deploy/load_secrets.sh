#!/usr/bin/env bash
# deploy/load_secrets.sh — source this to pull all beryl-* secrets from
# GCP Secret Manager into the current shell as environment variables.
#
# Usage (sourced, never run directly):
#   source deploy/load_secrets.sh
#
# Requires: gcloud authenticated via SA key (GCP_SA_KEY_JSON_PATH must be set,
# or GOOGLE_APPLICATION_CREDENTIALS already pointing at the key file).
# Falls back to .env if Secret Manager is unreachable (offline / no key yet).

_SM_PROJECT="${GCP_PROJECT:-posh-eden}"
_SM_SA_KEY="${GCP_SA_KEY_JSON_PATH:-${GOOGLE_APPLICATION_CREDENTIALS:-}}"

_gc_sm() {
    CLOUDSDK_AUTH_ACCESS_TOKEN="" \
    GOOGLE_APPLICATION_CREDENTIALS="$_SM_SA_KEY" \
    gcloud "$@"
}

_load_from_sm() {
    local secrets
    secrets=$(_gc_sm secrets list --project="$_SM_PROJECT" \
        --filter="name:beryl-" --format="value(name)" 2>/dev/null) || return 1

    [[ -z "$secrets" ]] && return 1

    while IFS= read -r secret_path; do
        local secret_name="${secret_path##*/}"          # strip path prefix
        local env_key="${secret_name#beryl-}"           # strip beryl- prefix
        env_key="${env_key//-/_}"                       # dashes back to underscores
        env_key="${env_key^^}"                          # uppercase

        local value
        value=$(_gc_sm secrets versions access latest \
            --secret="$secret_name" \
            --project="$_SM_PROJECT" 2>/dev/null) || continue

        export "$env_key"="$value"
    done <<< "$secrets"

    return 0
}

if [[ -z "$_SM_SA_KEY" ]]; then
    echo "[secrets] WARN: GCP_SA_KEY_JSON_PATH not set — trying .env fallback" >&2
elif _load_from_sm; then
    echo "[secrets] loaded from GCP Secret Manager (project: $_SM_PROJECT)" >&2
else
    echo "[secrets] WARN: Secret Manager unreachable — falling back to .env" >&2
fi

# fallback: source .env if it exists and SM didn't load HF_TOKEN (key signal)
if [[ -z "${HF_TOKEN:-}" && -f .env ]]; then
    set -a; source .env; set +a
    echo "[secrets] loaded from .env (fallback)" >&2
fi
