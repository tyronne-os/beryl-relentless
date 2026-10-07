"""
JEV·VERIFY — cross-check telemetry + painted-pixel stats → sensory gland.
Output: {stated_stage, telemetry_stage, consistent, is_motion_visible,
         occluding_layer, duplug_state, audio_energy_pattern, agrees}

This is the receipts node. It catches CSS-only SIM masquerading as real motion.
Deterministic metrics are PRIMARY — JEV is a typed cross-check on top.
"""
import logging
import os

import httpx

log = logging.getLogger("jev.verify")

_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
_KEY = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("JEV_API_KEY", "")
_MODEL = os.environ.get("CRANE_JEV_MODEL", "jev-latest")
_TIMEOUT = float(os.environ.get("JEV_VERIFY_TIMEOUT_S", "2.5"))
_ENABLED = os.environ.get("CRANE_JEV", "true").lower() == "true"

_client = httpx.AsyncClient(timeout=_TIMEOUT)

_STAGES = {"L0": "Idle presence only (client cached loop)", "L1": "Live CPU+API chain", "L2": "GPU cinematic render"}


def _deterministic_fallback(telemetry: dict, painted_pixels: dict) -> dict:
    stated = telemetry.get("stated_stage", "L0")
    motion_pixels = painted_pixels.get("changed_pixel_area", 0)
    has_motion = motion_pixels > 100
    duplug_state = telemetry.get("duplug_state", "unknown")
    audio_energy = telemetry.get("audio_energy_rms", 0.0)

    # If stated L1/L2 but zero pixel change, it's still CSS SIM
    telemetry_stage = stated
    consistent = True
    if stated in ("L1", "L2") and not has_motion:
        telemetry_stage = "L0"
        consistent = False

    agrees = not (audio_energy > 0.05 and duplug_state == "silent")

    return {
        "stated_stage": stated,
        "telemetry_stage": telemetry_stage,
        "consistent": consistent,
        "is_motion_visible": has_motion,
        "occluding_layer": painted_pixels.get("occluding_layer", None),
        "duplug_state": duplug_state,
        "audio_energy_pattern": "active" if audio_energy > 0.05 else "silent",
        "agrees": agrees,
        "motion_pixels": motion_pixels,
        "source": "fallback",
    }


async def check(telemetry: dict, painted_pixels: dict) -> dict:
    """
    telemetry: {stated_stage, duplug_state, audio_energy_rms, fps_actual,
                lipsync_offset_ms, first_frame_latency_ms}
    painted_pixels: {changed_pixel_area, occluding_layer, frame_diff_mean}
    Returns the full verify scorecard.
    Always returns deterministic values; JEV adds typed cross-check if available.
    """
    # Always run deterministic check first
    base = _deterministic_fallback(telemetry, painted_pixels)

    if not _ENABLED or not _KEY:
        return base

    try:
        payload = {
            "model": _MODEL,
            "state": {
                "telemetry": telemetry,
                "painted_pixels": painted_pixels,
                "deterministic_result": base,
            },
            "questions": {
                "consistent": {
                    "type": "noul",
                    "question": "Does the telemetry data confirm that the stated rendering stage is actually active and producing real motion?",
                },
                "is_motion_visible": {
                    "type": "noul",
                    "question": "Do the painted_pixels metrics confirm that real pixel-level motion is occurring (not CSS transform only)?",
                },
                "agrees": {
                    "type": "noul",
                    "question": "Does the audio energy pattern agree with the duplug listener state? (Active audio should not show 'silent' duplug state)",
                },
            },
        }
        resp = await _client.post(_URL, json=payload, headers={"Authorization": f"Bearer {_KEY}"})
        resp.raise_for_status()
        ans = resp.json().get("answers", {})

        # JEV overrides deterministic only if confidence is high
        jev_consistent = ans.get("consistent", {}).get("yes_prob", 0.5)
        jev_motion = ans.get("is_motion_visible", {}).get("yes_prob", 0.5)
        jev_agrees = ans.get("agrees", {}).get("yes_prob", 0.5)

        if ans.get("consistent", {}).get("confidence", 0) > 0.7:
            base["consistent"] = jev_consistent > 0.5
        if ans.get("is_motion_visible", {}).get("confidence", 0) > 0.7:
            base["is_motion_visible"] = jev_motion > 0.5
        if ans.get("agrees", {}).get("confidence", 0) > 0.7:
            base["agrees"] = jev_agrees > 0.5

        base["source"] = "jev+deterministic"
        return base

    except Exception as exc:
        log.warning("JEV·VERIFY failed (%s) — deterministic result stands", exc)
        return base


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
        "lipsync_ok": lipsync is not None and 40 <= lipsync <= 133,
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
            lipsync is not None and 40 <= lipsync <= 133,
            fps >= 24,
            first_frame <= 500,
            drift is not None and drift <= 0.15,
        ]),
    }
