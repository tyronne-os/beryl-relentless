#!/usr/bin/env bash
# deploy/push_secrets.sh — ONE-TIME: upload all .env keys to GCP Secret Manager.
#
# Run once from your laptop with the SA key loaded:
#   source .env && bash deploy/push_secrets.sh
#
# After this, you never need .env again. miranda.sh pulls everything from
# Secret Manager automatically. Keep .env as a local backup but never rely on it.
set -uo pipefail
SCRIPT_TAG="push_secrets"
source "$(dirname "$0")/lib.sh"

ENV_FILE="${1:-.env}"
[[ -f "$ENV_FILE" ]] || { echo "ERROR: $ENV_FILE not found"; exit 1; }

log "uploading secrets from $ENV_FILE to GCP Secret Manager (project: $PROJECT)..."
echo

PUSHED=0
SKIPPED=0

while IFS= read -r line || [[ -n "$line" ]]; do
    # skip blank lines and comments
    [[ -z "$line" || "$line" == \#* ]] && continue
    # must be KEY=value
    [[ "$line" != *=* ]] && continue

    KEY="${line%%=*}"
    VALUE="${line#*=}"
    # strip surrounding quotes
    VALUE="${VALUE%\"}"
    VALUE="${VALUE#\"}"
    VALUE="${VALUE%\'}"
    VALUE="${VALUE#\'}"

    # skip empty values and placeholders
    [[ -z "$VALUE" ]] && { echo "  SKIP  $KEY (empty)"; SKIPPED=$((SKIPPED+1)); continue; }
    [[ "$VALUE" == *placeholder* || "$VALUE" == *your_* || "$VALUE" == *xxx* ]] && {
        echo "  SKIP  $KEY (placeholder)"; SKIPPED=$((SKIPPED+1)); continue; }

    SECRET_NAME="beryl-${KEY,,}"   # lowercase, prefixed with beryl-
    SECRET_NAME="${SECRET_NAME//_/-}"  # underscores to dashes (SM naming rules)

    # create secret if it doesn't exist, then add a new version
    if ! gc secrets describe "$SECRET_NAME" --project="$PROJECT" >/dev/null 2>&1; then
        gc secrets create "$SECRET_NAME" \
            --project="$PROJECT" \
            --replication-policy="automatic" >/dev/null 2>&1
    fi

    printf '%s' "$VALUE" | gc secrets versions add "$SECRET_NAME" \
        --project="$PROJECT" \
        --data-file=- >/dev/null 2>&1

    echo "  OK    $KEY → projects/$PROJECT/secrets/$SECRET_NAME"
    PUSHED=$((PUSHED+1))

done < "$ENV_FILE"

echo
log "done — $PUSHED secrets pushed, $SKIPPED skipped."
echo
echo "  Miranda will now load all secrets from Secret Manager automatically."
echo "  Your .env stays as a local backup only."
