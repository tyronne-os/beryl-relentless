# Beryl Relentless — Session Boot Context

**What this is:** Private backend build for Berylize.com / Beryl Labs.
Goal: single 2D photo + live audio → 3D-appearing conversational human, zero uncanny valley.
VASA-1 class — no mesh, no rig, no game engine.

**Context repo (read-only, never push there):** `tyronne-os/77793`
**Build repo (all work goes here):** `tyronne-os/beryl-relentless`
**Front end (DO NOT TOUCH):** already final in `tyronne-os/77793` `src/client/`

---

## GCP target
- Project: `posh-eden`
- Zone: `us-east1-c`
- VM: `berylize-node` (L4 GPU, existing)

## Required env vars (load from GCP Secret Manager / `.env`, never commit)
```
HF_TOKEN                 # Hugging Face read token
HUGGING_FACE_HUB_TOKEN   # same token, some libs read this name
NVIDIA_API_KEY           # NVIDIA enterprise NIM key (copied from NGC key)
NGC_API_KEY              # NGC container pull key
NGC_ENTERPRISE_KEY       # what deploy.py reads for enterprise NIM endpoints
ANTHROPIC_API_KEY        # Claude judge for harness/selftest (three-stage self-test)
GITHUB_TOKEN             # GitHub PAT
GH_TOKEN                 # same token, gh CLI reads this name
TYPESAFE_API_KEY         # TypeSafe System One / JEV key (also: JEV_API_KEY)
GCP_SA_KEY_JSON          # service account JSON for gcloud auth
GCP_ZONE                 # us-east1-c
KAGGLE_USERNAME          # Kaggle (for dataset access / extra RAM)
KAGGLE_KEY               # Kaggle API key
```

---

## Build rules
1. Backend only. Front end is final — no changes to `src/client/`.
2. Extend existing code: `src/server/{main,chat,jev,deploy,multiavatar,modellab,gpu}.py` (in tyronne-os/77793 — copy what's needed, don't rewrite from scratch).
3. Every node ships with a smoke test first.
4. **Three-strikes rule:** 3rd distinct failure on a node → stop patching, vendor a known-good reference from HF / NVIDIA GitHub (Pipecat, JoyVASA, FLOAT, LivePortrait, Kokoro, faster-whisper).
5. JEV stays OUT of repair decisions — early-access, can be slow/down. Repairs must not depend on it.
6. No GPU required to build L0/L1. GPU only activates on `scripts/gpu_on.sh`.
7. Secrets never committed. Run secret scan before every push.
8. YAML node contracts: flat ~15 fields, loaded via `yaml.safe_load`. No expressions in YAML; branching in Python.

---

## Pipeline chain
```
MIC (client WebRTC+VAD)
  → ASR (faster-whisper/Parakeet INT8, CPU)
  → LISTEN (Duplug 0.6B INT8, CPU, parallel)
  → LLM (Nemotron via NIM API)
  → TTS (Kokoro 82M INT8, CPU; Qwen3-TTS/TontaubeV1 as bake-off slots)
  → MOTION (JoyVASA/FLOAT audio→latents; UniLS/REALM listener motion)
  → RENDER (client WebGPU warp renderer ← latents kb/s; L2 server diffusion student)
  → VERIFY
```

## Stage ladder
- **L0 Idle Presence** — client-only cached motion loop (blink, breath, gaze drift). Alive on page load. Zero server cost. Hides GPU spin-up.
- **L1 Live (GPU OFF baseline)** — full CPU+API chain. Motion latents streamed (kb/s) to client WebGPU renderer.
- **L2 Cinematic (GPU ON)** — spot-GPU burst: distilled video-diffusion student (FlashHead-1.3B vs LeapTalk vs AvatarForcing bake-off). Falls back to L1 on failure/idle timeout.

## JEV placements (TypeSafe System One)
- `POST https://api.typesafe.ai/v1/systemone`, model `jev-latest`
- OpenRouter `typesafe/jev-1.13` as fallback
- Every JEV call has a deterministic local fallback
1. **JEV·FACE** — user MediaPipe AUs + Beryl blendshapes → `{user_emotion, beryl_emotion, match, confidence}`
2. **JEV·VOICE** — prosody user+TTS → `{tone, contradicts_words, stress, confidence}`
3. **JEV·PERSONA** — fuse Face+Voice+history+persona card → `{in_character, tone_ok, action: continue|soften|yield}`; gates LLM reply
4. **JEV·DIRECTOR** — Persona decision → `{gaze, blink_rate, nod, micro_expression, intensity}` into MOTION
5. **JEV·VERIFY** — telemetry + painted-pixel stats → sensory gland

## GPU-ON activation
`scripts/gpu_on.sh`: (1) create GCE spot VM (g2-standard-4 + 1x L4; T4 fallback) from baked image, (2) attach weights disk, (3) start render service + health probe, (4) register node with controller, (5) controller flips L1→L2 when warm (~8-15s), (6) run bakeoff + publish scorecard.

## Cost reference (1 session-hour)
| Config | $/hr |
|--------|------|
| V1 minimal | ~$0.26–0.38 |
| V2 balanced (test first) | ~$0.57 |
| V3 max | ~$2.38 |
| V4 CPU-only small | ~$0.36 |
| Tokkio-class reference | ~$1.46 |

## Build order
1. Audit — run server + tests, list live endpoints, mark real vs SIM (blendshapes are SIM today)
2. Node contract YAML (`harness/nodes/*/node.yaml` + `harness/pipelines.yaml`) + health bus on top of `deploy.py`
3. CPU chain real: ASR → LLM → TTS → `/ws/avatar-chat`. Reference: Pipecat / nvidia voice-agent-examples
4. JEV x5 in `jev.py` with deterministic fallbacks
5. Motion node: audio → latents (JoyVASA / FLOAT), replaces SIM blendshapes
6. Photo-in: upload endpoint → render node (LivePortrait warp on CPU/L1; FlashHead/LeapTalk on GPU L2)
7. VERIFY: real measurements (lip-sync offset, FPS, first-frame, painted-pixel motion) → sensory gland
8. `gpu_on.sh` on berylize-node, bake-off, scorecard

## Acceptance tests (Tilly Norwood standard)
- Recognise a known person
- Improvise an unscripted scene without prompt guidance
- Show a requested emotion on demand
- Hold identity over a long session
- No CSS-only "motion" — VERIFY reads painted pixels, not CSS transform values
- Lip-sync within 40–133 ms offset range
- First frame under latency budget

## Repo layout
```
beryl-relentless/
  CLAUDE.md                          ← this file (session boot context)
  HANDOFF.md                         ← build notes
  knowledge/
    KB-NVIDIA-02.md                  ← Tokkio/ACE/Audio2Face reference
    KB-RESEARCH-03.md                ← HF Papers sweep (VASA-1 class + eval)
  harness/
    controller/                      ← session owner, health bus, stage-ladder FSM
    nodes/{mic,asr,listen,llm,tts,motion,render,verify}/
      node.yaml                      ← model slot, device, latency budget, health probe
      adapter.py                     ← node implementation
    jev/{face,voice,persona,director,verify}.py
    pipelines.yaml
  deploy/
    gpu_on.sh
    gpu_off.sh
  bakeoff/                           ← fixed photo+audio set, scorecard runner
  .env.example                       ← env var names only, no values
  .gitignore                         ← includes .env, *.key, *.json creds
```

## Key open items (verify before coding)
- LivePortrait/InsightFace license (community flags as non-commercial — check before adopting)
- JEV schema exact field types — confirm at docs.typesafe.ai
- TontaubeV1 license (custom community license)
- Real L4 spot price from GCP billing catalog
- CPU INT8 motion FPS — measure, do not assume
- LeapTalk 200 FPS claim — unverified on L4; 96 FPS is FlashHead 4090 number

---

## Saved utilities (user-approved, proven working)
- **USB restore / wipe:** `sudo bash scripts/usb_nuke.sh` — prompt-free, USB-only (TRAN=usb), protects boot disk, wipes + exFAT-formats BERYL-01..03 in sequence. Args: `[count] [countdown_s]`. When the user asks to wipe/restore/reset USB sticks, point them to this script; do not rewrite it.
