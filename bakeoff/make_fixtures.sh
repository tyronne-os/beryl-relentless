#!/usr/bin/env bash
# bakeoff/make_fixtures.sh — generate speech fixture WAVs using espeak-ng.
# Run on the GPU node (or any Ubuntu machine) where espeak-ng is available.
# Output: bakeoff/fixtures/hello_beryl.wav, long_session_30s.wav, silence_5s.wav (16 kHz mono)
#
# Usage (from repo root):
#   bash bakeoff/make_fixtures.sh
#
# To install espeak-ng on Ubuntu:
#   sudo apt install -y espeak-ng sox
set -euo pipefail
cd "$(dirname "$0")/.."

FIXTURES=bakeoff/fixtures
mkdir -p "$FIXTURES"

ok()  { printf '  [ OK ] %s\n' "$1"; }
fail(){ printf '  [FAIL] %s\n' "$1"; exit 1; }

command -v espeak-ng >/dev/null 2>&1 || fail "espeak-ng not found — run: sudo apt install -y espeak-ng sox"
command -v sox       >/dev/null 2>&1 || fail "sox not found — run: sudo apt install -y espeak-ng sox"

# ── hello_beryl.wav — ~5 s natural English greeting ──────────────────────
HELLO_TEXT="Hello Beryl. How are you doing today? I would love to have a conversation with you."
TMP=$(mktemp /tmp/fixture_XXXXX.wav)
espeak-ng -v en-us -s 145 -p 50 -a 100 -w "$TMP" "$HELLO_TEXT"
# resample to 16 kHz mono WAV
sox "$TMP" -r 16000 -c 1 -b 16 "$FIXTURES/hello_beryl.wav"
rm -f "$TMP"
SECS=$(sox --info -D "$FIXTURES/hello_beryl.wav" 2>/dev/null || echo "?")
ok "hello_beryl.wav — ${SECS}s at 16 kHz mono"

# ── long_session_30s.wav — ~30 s to test identity stability ─────────────
LONG_TEXT="Hello Beryl, my name is Tyler. I want to test how well you hold your identity over a longer conversation. Tell me about yourself. What do you enjoy talking about? I am curious to learn more about you and see how you respond to different kinds of questions. Can you tell me what makes you unique as an AI companion? I would like to have a real conversation today."
TMP2=$(mktemp /tmp/fixture_long_XXXXX.wav)
espeak-ng -v en-us -s 140 -p 48 -a 100 -w "$TMP2" "$LONG_TEXT"
sox "$TMP2" -r 16000 -c 1 -b 16 "$FIXTURES/long_session_30s.wav"
rm -f "$TMP2"
SECS2=$(sox --info -D "$FIXTURES/long_session_30s.wav" 2>/dev/null || echo "?")
ok "long_session_30s.wav — ${SECS2}s at 16 kHz mono"

# ── silence_5s.wav — idle face: proves pixels move with no speech ───────
sox -n -r 16000 -c 1 -b 16 "$FIXTURES/silence_5s.wav" trim 0.0 5.0
ok "silence_5s.wav — 5s at 16 kHz mono"

echo
echo "Fixtures written to $FIXTURES/"
echo "Run the scorecard: python3 bakeoff/scorecard_runner.py (with tunnel open)"
