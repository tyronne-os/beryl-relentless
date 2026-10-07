#!/usr/bin/env bash
# launch.sh — open Miranda Voice Studio in your browser (no server needed)
#
# Usage:
#   bash launch.sh             # opens index.html directly
#   bash launch.sh --serve     # serves on localhost:8090 (for stricter browsers)
#
# Requires: a running Kokoro TTS server at localhost:8012
#   Start it via: bash ../miranda.sh   (or your GPU node tunnel)

FILE="$(cd "$(dirname "$0")" && pwd)/index.html"

if [[ "${1:-}" == "--serve" ]]; then
    PORT="${2:-8090}"
    echo "Serving Miranda Voice Studio at http://localhost:${PORT}"
    echo "Press Ctrl-C to stop."
    cd "$(dirname "$0")"
    if command -v python3 >/dev/null 2>&1; then
        python3 -m http.server "$PORT"
    elif command -v npx >/dev/null 2>&1; then
        npx --yes serve -p "$PORT" .
    else
        echo "ERROR: python3 or npx required for --serve mode."
        exit 1
    fi
else
    echo "Opening Miranda Voice Studio..."
    if [[ "$OSTYPE" == darwin* ]]; then
        open "$FILE"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$FILE"
    elif command -v wslview >/dev/null 2>&1; then
        wslview "$FILE"
    else
        echo "Open this file in your browser:"
        echo "  $FILE"
    fi
fi
