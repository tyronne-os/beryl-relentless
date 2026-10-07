#!/usr/bin/env bash
# prep_fixture.sh — put the reference face photo where the bake-off expects it.
# Usage: ./deploy/prep_fixture.sh            (lists your newest images, pick by number)
#        ./deploy/prep_fixture.sh 3          (use number 3 from that list)
#        ./deploy/prep_fixture.sh /path/to/photo.jpg
# Photo stays on your machine: bakeoff/fixtures/reference.* is gitignored.
set -euo pipefail
cd "$(dirname "$0")/.."
DEST=bakeoff/fixtures/reference.jpg
SRC_DIR="${PHOTO_DIR:-$HOME/Downloads}"
mkdir -p bakeoff/fixtures

mapfile -t IMGS < <(find "$SRC_DIR" -maxdepth 2 -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -12 | cut -d' ' -f2-)

ARG="${1:-}"
if [[ -z "$ARG" ]]; then
    echo "Newest images in $SRC_DIR:"
    for i in "${!IMGS[@]}"; do printf '  %2d) %s\n' "$((i+1))" "${IMGS[$i]}"; done
    echo; echo "Pick the clearest front-facing, mouth-closed one:  ./deploy/prep_fixture.sh <number>"
    exit 0
fi
if [[ "$ARG" =~ ^[0-9]+$ ]]; then SRC="${IMGS[$((ARG-1))]:-}"; else SRC="$ARG"; fi
[[ -f "$SRC" ]] || { echo "not found: $SRC"; exit 1; }

python3 - "$SRC" "$DEST" <<'PY' 2>/dev/null || cp "$SRC" "$DEST"
import sys
from PIL import Image
im = Image.open(sys.argv[1]).convert("RGB")
w, h = im.size; s = min(w, h)                       # center-crop to square, FlashHead is 512x512
im = im.crop(((w-s)//2, (h-s)//2, (w-s)//2+s, (h-s)//2+s)).resize((1024, 1024))
im.save(sys.argv[2], quality=95)
PY
echo "reference photo ready: $DEST  (from $SRC)"
