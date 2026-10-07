#!/usr/bin/env bash
# miranda.sh — one-click launch: preflight → start GPU node → open Miranda UI
#
# Usage (from repo root — no source .env needed):
#   bash miranda.sh
#
# Secrets are loaded automatically from GCP Secret Manager (falls back to .env).
# One-time setup: source .env && bash deploy/push_secrets.sh
#
# Stop everything:
#   bash miranda.sh --stop
set -uo pipefail
cd "$(dirname "$0")"

# ── Load secrets from GCP Secret Manager (or .env fallback) ──────────────────
source deploy/load_secrets.sh

# ── --stop ────────────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--stop" ]]; then
    bash deploy/open_ui.sh --stop
    bash deploy/gpu_off.sh
    exit 0
fi

echo
echo "╔══════════════════════════════════════╗"
echo "║           M I R A N D A              ║"
echo "║       Berylize Labs — Local AI       ║"
echo "╚══════════════════════════════════════╝"
echo

# ── Step 1: predeploy gate (static checks, no network needed) ─────────────────
echo "[ 1/3 ] Running preflight checks..."
bash deploy/predeploy.sh || { echo "Preflight failed — fix errors above before launching."; exit 1; }

# ── Step 2: start GPU node (idempotent — skips if already RUNNING) ────────────
echo
echo "[ 2/3 ] Starting GPU node..."
bash deploy/gpu_on.sh --no-bakeoff

# ── Step 3: open Miranda UI (LibreChat + Qwen via Ollama) ─────────────────────
echo
echo "[ 3/3 ] Opening Miranda..."
bash deploy/open_ui.sh
