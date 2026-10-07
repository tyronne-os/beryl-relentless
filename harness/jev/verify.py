"""
JEV·VERIFY — cross-check telemetry + painted-pixel stats → sensory gland.
Output: {stated_stage, telemetry_stage, consistent, is_motion_visible,
         occluding_layer, duplug_state, audio_energy_pattern, agrees}

This is the receipts node. It catches CSS-only SIM masquerading as real motion.
Deterministic metrics are PRIMARY; JEV is a typed cross-check that can only make a result STRICTER.
Fails CLOSED: if VERIFY itself breaks it reports not-consistent / no-motion, never a green receipt.
Vocabulary, thresholds, timeout and question wording live in jev.yaml.
"""
import logging
import os

import httpx

from harness.jev._contract import SPEC, call_jev, conf, guarded, prob, questions, timeout_for
from harness.nodes.verify.lipsync import lipsync_ok

log = logging.getLogger("jev.verify")

_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "")
_MODEL = os.environ.get("CRANE_JEV_MODEL", SPEC["model_default"])
_TIMEOUT = timeout_for("verify")
_ENABLED = os.environ.get("CRANE_JEV", "true").lower() == "true"

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_STAGES = SPEC["vocab"]["stages"]
_NEUTRAL = SPEC["neutral"]["verify"]
_T = SPEC["fallback"]["verify"]
_OVERRIDE_CONF = SPEC["verify_override_confidence"]


def _deterministic_fallback(telemetry: dict, painted_pixels: dict) -> dict:
    stated = telemetry.get("stated_stage", "L0")
    stage_known = isinstance(stated, str) and stated in _STAGES
    if not stage_known:
        stated = "L0"
    motion_pixels = painted_pixels.get("changed_pixel_area", 0)
    has_motion = motion_pixels > _T["motion_pixels_min"]
    duplug_state = telemetry.get("duplug_state", "unknown")
    duplug_state = duplug_state if isinstance(duplug_state, str) else "unknown"
    audio_active = telemetry.get("audio_energy_rms", 0.0) > _T["audio_active_rms"]

    # Stated L1/L2 with no painted-pixel change is still CSS SIM.
    telemetry_stage, consistent = stated, stage_known
    if stated in ("L1", "L2") and not has_motion:
        telemetry_stage, consistent = "L0", False

    occluding = painted_pixels.get("occluding_layer", None)
    return {
        "stated_stage": stated,
        "telemetry_stage": telemetry_stage,
        "consistent": consistent,
        "is_motion_visible": has_motion,
        "occluding_layer": occluding if isinstance(occluding, (str, type(None))) else None,
        "duplug_state": duplug_state,
        "audio_energy_pattern": "active" if audio_active else "silent",
        "agrees": not (audio_active and duplug_state == "silent"),
        "motion_pixels": motion_pixels,
        "source": "fallback",
    }


@guarded(_NEUTRAL)
async def check(telemetry: dict, painted_pixels: dict) -> dict:
    """
    telemetry: {stated_stage, duplug_state, audio_energy_rms, fps_actual, lipsync_offset_ms, first_frame_latency_ms}
    painted_pixels: {changed_pixel_area, occluding_layer, frame_diff_mean}
    Deterministic values always; JEV can only tighten them.
    """
    base = _deterministic_fallback(telemetry, painted_pixels)

    if not _ENABLED or not _KEY:
        return base

    try:
        payload = {
            "model": _MODEL,
            "state": {"telemetry": telemetry, "painted_pixels": painted_pixels, "deterministic_result": base},
            "questions": questions("verify"),
        }
        ans = await call_jev(_client, _URL, payload, _KEY, _TIMEOUT)
        # Parse every vote before applying any, so one bad slot changes nothing.
        votes = {k: (conf(ans, k, 0.0), prob(ans, k, 0.5)) for k in ("consistent", "is_motion_visible", "agrees")}
        for k, (c, p) in votes.items():
            if c > _OVERRIDE_CONF and p <= 0.5:
                base[k] = False
        base["source"] = "jev+deterministic"
        return base
    except Exception as exc:
        log.warning("JEV·VERIFY failed (%r) — deterministic result stands", exc)
        return _deterministic_fallback(telemetry, painted_pixels)


def scorecard(verify_result: dict, metrics: dict) -> dict:
    """
    Build the full sensory gland scorecard from verify + raw metrics.
    metrics: {lipsync_offset_ms, fps, first_frame_latency_ms, identity_drift}
    """
    lipsync = metrics.get("lipsync_offset_ms")
    fps = metrics.get("fps", 0)
    first_frame = metrics.get("first_frame_latency_ms", 0)
    drift = metrics.get("identity_drift")

    return {
        "stage": verify_result.get("telemetry_stage", "L0"),
        "stage_consistent": verify_result.get("consistent", False),
        "motion_real": verify_result.get("is_motion_visible", False),
        "lipsync_ok": lipsync_ok(lipsync),
        "lipsync_offset_ms": lipsync,
        "fps_ok": fps >= 24,
        "fps": fps,
        "first_frame_ok": first_frame <= 500,
        "first_frame_ms": first_frame,
        "identity_ok": drift is not None and drift <= 0.15,
        "identity_drift": None if drift is None else round(drift, 3),
        "audio_agrees": verify_result.get("agrees", True),
        "all_green": all([
            verify_result.get("consistent", False),
            verify_result.get("is_motion_visible", False),
            lipsync_ok(lipsync),
            fps >= 24,
            first_frame <= 500,
            drift is not None and drift <= 0.15,
        ]),
    }
