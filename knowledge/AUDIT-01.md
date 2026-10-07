# Audit 01 — Server Endpoint Inventory + Real-vs-SIM

Date: 2026-10-07. Source: tyronne-os/77793 src/server/.

## Summary verdict

| Layer | Status |
|-------|--------|
| LLM chat (Qwen local / NIM API) | REAL |
| JEV perceive / performance / proprioception | REAL |
| TTS — Kokoro port 8012, speaches port 8013 | REAL (ports up) |
| GPU lifecycle — berylize-node gcloud + SSH tunnel | REAL |
| Podman / workstation / modellab | REAL |
| Avatar animation (blendshapes, gaze, micro-expression) | **SIM** — CSS/JS labels only |
| ASR in avatar chat | **MISSING** — no server-side ASR wired |
| Motion latents → renderer | **MISSING** — explicit "later" comment in jev.py |
| Audio2Face / video diffusion | **MISSING** |

## The gap we are filling

```
TODAY:   JEV JSON labels → browser CSS animation
TARGET:  JEV → motion latents → client WebGPU warp renderer (L1)
                              → diffusion student on berylize-node GPU (L2)
```

## /ws/avatar-chat end-to-end (current)

```
Client message
  → gpu.ping()
  → jev.perceive()          [REAL — TypeSafe API]
  → chat.handle()           [REAL — Qwen/NIM LLM stream]
  → jev.performance()       [REAL — drives CSS face labels]
  → jev.proprioception()    [REAL — contradiction check]
  → WS done
    → client must call /api/services/kokoro/tts separately
```

TTS is NOT called server-side. Client calls Kokoro after receiving `done`.
VAD / turn_complete rubric exists but is REST-only, not wired into WS loop.

## JEV rubrics already wired (11 exist)

| # | Name | Where | Status |
|---|------|--------|--------|
| 1 | perceive | avatar + build chat | REAL, auto |
| 2 | turn_complete | REST /api/jev/turn | REAL, manual only |
| 3 | performance | avatar chat post-reply | REAL, auto |
| 4 | proprioception | avatar chat post-reply | REAL, auto |
| 5 | judge_turn | multi-avatar | REAL, auto |
| 6 | pick_speaker | multi-avatar | REAL, auto |
| 7 | route_task | build chat | REAL, async |
| 8 | rerank | knowledge retrieval | REAL |
| 9 | memory_worth | all turns fire-and-forget | REAL |
| 10 | review_verdict | engineers loop | REAL |
| 11 | same_problem | engineers loop | REAL |

**Missing (Build Step 4):** JEV·FACE, JEV·VOICE, JEV·PERSONA, JEV·DIRECTOR, JEV·VERIFY

## deploy.py PIPELINES

Only ONE pipeline defined: `nvidia-prebuilt` (Tokkio NIM probe).
It is a **connectivity probe only** — does not execute inference.
Omniverse stage hardcoded `skip: True` permanently.

Stages: mic → asr (grpc.nvcf.nvidia.com) → llm (integrate.api.nvidia.com) → tts → a2f → anm → ov(skip)

**Build Step 2 adds:** local/CPU pipeline entries + health bus wiring.

## gpu.py — what it does

- gcloud start/stop/describe berylize-node
- After start: SSH port-forward local:8010 → remote:8000 (Qwen vLLM)
- Background poll every 15s: nvidia-smi over SSH → util, mem, temp, power
- Auto-pause after idle_timeout (default 2h); never pauses mid-inference
- gpu.ping() called on every WS message (resets idle timer)
- Cost tracked at $0.40/hr (configurable)

## TODOs visible in existing code

1. Only `nvidia-prebuilt` pipeline in deploy.py — no CPU/local/HF pipeline
2. Omniverse permanently skipped — no path from A2F blendshapes to renderer
3. Audio2Face NOT wired into avatar chat — jev.py line 348: "same schema maps onto render-model expression conditioning **later**"
4. `turn_complete` REST-only — VAD early-answer not active in WS loop
5. Two TTS backends (Kokoro 8012, speaches 8013) — no server-side routing from avatar turn
6. `_NIM_MODELS` contains `openai/gpt-oss-20b` — likely placeholder
7. `CRANE_RUNS_DIR` log files never pruned (only list capped at 50)
