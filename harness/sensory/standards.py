"""
Sensory standards — acceptance thresholds with their sources.
All numbers live here. Nothing is hard-coded elsewhere.

Source categories:
  published  — peer-reviewed paper or industry standard
  project    — derived from the Berylize build (measured on berylize-node)
  engineering — engineering estimate; must be measured to graduate to project/published
"""
from __future__ import annotations
from typing import NamedTuple


class Standard(NamedTuple):
    value: float | int | str
    unit: str
    source: str          # published | project | engineering
    citation: str        # enough to find the source


# ── Eye / blink ────────────────────────────────────────────────────────────────

BLINK_RATE_MIN  = Standard(12,   "blinks/min", "published",
    "Doughty 2002, Optom Vis Sci 79(9):564-9 — normal range 8-21, mean ~17")
BLINK_RATE_MAX  = Standard(21,   "blinks/min", "published",
    "Doughty 2002 — upper tail; >30 suggests fatigue or irritation")
BLINK_RATE_TARGET = Standard(17, "blinks/min", "published",
    "Doughty 2002 mean; use as tuning target for JEV·DIRECTOR")
BLINK_DURATION_MIN = Standard(80,  "ms", "published",
    "Manning et al 2013 — voluntary blink ~100-150 ms; reflexive ~50-80 ms")
BLINK_DURATION_MAX = Standard(400, "ms", "published",
    "Manning et al 2013 — >400 ms is a slow/incomplete blink")

# ── Lip-sync / mouth ───────────────────────────────────────────────────────────

LIPSYNC_PASS_MS  = Standard(133,  "ms",  "published",
    "ITU-R BT.1359-1 Table 1 — lip sync tolerance ±125 ms; 133 ms is the Berylize gate")
LIPSYNC_LEAD_MS  = Standard(-40,  "ms",  "project",
    "Berylize bakeoff: audio >40 ms ahead of mouth looks un-dubbed; asymmetric rule pending user decision")
LIPSYNC_PEAK_R   = Standard(0.45, "r",   "project",
    "lipsync.py MIN_PEAK_R — below this the cross-correlation is not trusted; aliasing risk below 0.30")
LIPSYNC_WINDOW_S = Standard(0.80, "s",   "project",
    "lipsync.py LAG_RANGE_S — ±0.8 s window; aliases beyond ±0.7 s at r<0.45 filter removed")

# ── Identity drift ─────────────────────────────────────────────────────────────

IDENTITY_DRIFT_MAX = Standard(0.15, "cosine distance", "engineering",
    "L2 norm of face embedding delta vs reference frame; 0.15 engineering threshold, needs bakeoff calibration")

# ── Render throughput ──────────────────────────────────────────────────────────

FPS_MIN             = Standard(24,   "fps",  "engineering",
    "Cinema standard minimum; FlashHead targets ~28 fps on L4 (measured: 37.5 fps cold)")
FIRST_CHUNK_BUDGET  = Standard(500,  "ms",   "project",
    "CLAUDE.md acceptance criterion; FlashHead measured 635-680 ms today — fails this gate")
FIRST_CHUNK_ACTUAL  = Standard(680,  "ms",   "project",
    "berylize-node measured 2026-10 — known to fail FIRST_CHUNK_BUDGET; tracked for bakeoff")

# ── Audio / voice ──────────────────────────────────────────────────────────────

WPM_MIN     = Standard(110, "wpm", "engineering", "selftest.yaml voice gate")
WPM_MAX     = Standard(200, "wpm", "engineering", "selftest.yaml voice gate")
WER_MAX     = Standard(0.10, "fraction", "engineering", "selftest.yaml voice gate — ASR round-trip error")
LEAD_SILENCE_MAX = Standard(0.6, "s", "engineering", "selftest.yaml — Kokoro warmup silence")
CLIPPING_MAX = Standard(0.001, "fraction", "engineering", "selftest.yaml — 0.1% sample clipping")

# ── Triage light levels (ordered worst → best) ────────────────────────────────
# Each level has a human meaning; levels are compared by index.

TRIAGE_LEVELS = ["red", "orange", "amber", "green", "gold"]

def triage_index(level: str) -> int:
    try:
        return TRIAGE_LEVELS.index(level.lower())
    except ValueError:
        return -1


def worst(*levels: str) -> str:
    """Return the worst (lowest) triage level from a set."""
    idx = min((triage_index(l) for l in levels), default=0)
    return TRIAGE_LEVELS[max(0, idx)]


def triage_for(value: float | None, *, good: float, warn: float, bad: float,
               higher_is_better: bool = True) -> str:
    """
    Map a scalar to a triage level.
    higher_is_better=True: gold >= good > green >= warn > amber >= bad > orange/red.
    Flip for thresholds where lower is better (e.g. drift, latency).
    """
    if value is None:
        return "amber"
    if higher_is_better:
        if value >= good:  return "gold"
        if value >= warn:  return "green"
        if value >= bad:   return "amber"
        return "red"
    else:
        if value <= good:  return "gold"
        if value <= warn:  return "green"
        if value <= bad:   return "amber"
        return "red"
