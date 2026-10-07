# CRANE-IT V1 Session Handoff — Updated 2026-10-07 (Session 2)

**Next agent: read this top-to-bottom. Everything you need is here.**

---

## FINISH LIST (the only work left for a working L2 demo; do in this order)
Execution rule: run these from a session that HAS the GCP key and ssh (laptop or berylize-node), never relay commands to the operator.
1. ~~Fix TTS/MOTION port clash~~ **DONE** — `avatar_chain.py` fixed, Kokoro now on port 8012.
2. ~~Real speech fixtures~~ **DONE (session 3)** — generated on the node (laptop has no espeak/sudo) and copied back to `bakeoff/fixtures/` (gitignored). `make_fixtures.sh` now also writes `silence_5s.wav`. To regenerate from the laptop: scp the script to the node, run it there, scp `*.wav` back.
3. ~~gpu_on + clean scorecard~~ **DONE (session 3)** — scorecard runner now does an untimed warm-up render first (cold start after service restart is ~60 s and was timing out test #1; lesson 17). Clean card, real speech: motion PASS (silence 324 px vs speech 1.4–2.7k px changed), fps 35 PASS, first-chunk ~680 ms FAIL (budget decision), lipsync + identity unmeasured (red by design).
4. ~~Save a real clip~~ **DONE (session 3)** — `POST /render {..., "save_mp4": true}` renders the full audio, muxes H.264+AAC to `/opt/beryl/renders/<id>.mp4` (last 50 kept), returns `video_url`; `GET /video/{id}` serves it. Client: `.venv/bin/python bakeoff/make_clip.py [--photo P] [--audio A]` → `bakeoff/results/clip_*.mp4`. Measured warm: 5.76 s clip in 8.7 s wall (35 fps render + 1.6 s encode), 512x512 25 fps. Not yet wired into `src/server` / Studio upload (front end is final; needs a backend proxy route only).
5. Lip-sync measurement (SyncNet-style) in VERIFY.
6. `./deploy/gpu_off.sh` when done (VM bills while running).

## LOCAL CODING AGENT (free, no Claude tokens)
Berylize 14B runs on the GPU node itself — use it for routine edits/searches to save Claude tokens for hard reasoning.
```bash
# install once on the node
curl -fsSL https://ollama.com/install.sh | sudo sh
ollama pull qwen2.5-coder:14b
ollama run qwen2.5-coder:14b    # interactive chat on the L4, zero laptop RAM needed
```
Or from laptop with tunnel open (`bash deploy/gpu_ollama.sh`):
```bash
OLLAMA_HOST=http://localhost:11434 ollama run qwen2.5-coder:14b
```
Modelfile with project persona: `models/Berylize.modelfile` — `ollama create berylize14b -f models/Berylize.modelfile`

---

## SESSION UPDATE 2026-10-07 Session 2 — hardening, local agent, port fixes

### What was accomplished
| Item | Status |
|---|---|
| TTS/MOTION port collision fixed | **DONE** — `avatar_chain.py` TTS was hitting port 9522 (MOTION). Now correctly calls Kokoro at port 8012 via `/v1/audio/speech` |
| Speech fixture generator | **DONE** — `bakeoff/make_fixtures.sh` generates real 16 kHz WAVs from espeak-ng on the node |
| Berylize 14B local agent | **DONE** — `models/Berylize.modelfile` (Ollama, qwen2.5-coder:14b). Runs on GPU node, zero laptop RAM |
| GPU Ollama deploy script | **DONE** — `deploy/gpu_ollama.sh` installs Ollama on the node, pulls model, opens tunnel on port 11434 |
| Pipeline progress | **~49% overall; ~75% of L2 demo path** |

### Next steps (priority order)
1. Create fixtures on node: `sudo apt install -y espeak-ng sox && bash bakeoff/make_fixtures.sh`
2. Run `./deploy/gpu_on.sh` and scorecard with real audio
3. Build photo-in endpoint → mp4 clip output (the demo deliverable)
4. Lip-sync measurement in VERIFY (last critical red test)
5. LeapTalk / AvatarForcing bakeoff to settle first-frame latency decision

### Port map (complete, no collisions)
| Port | Service |
|---|---|
| 8012 | Kokoro TTS |
| 8013 | Speaches TTS fallback |
| 9520 | ASR (faster-whisper) |
| 9521 | LISTEN (Duplug) |
| 9522 | MOTION |
| 9523 | GPU render (tunnelled from node) |
| 9524 | Render fan-out |
| 9525 | VERIFY |
| 11434 | Ollama on GPU node (tunnel via `deploy/gpu_ollama.sh`) |

---

## SESSION UPDATE 2026-10-07 Session 1 — L2 GPU deploy COMPLETE

### What was accomplished
The GPU render pipeline (L2) is now live on `berylize-node` (project `posh-eden`, zone `us-east1-c`).

| Item | Status |
|---|---|
| FlashHead-1.3B Lite loaded on node | **DONE** — `model: flashhead-lite`, CUDA active, 18.8 GB VRAM free of 23.7 GB |
| Weights on disk | **DONE** — 33 GB total: Model_Lite, VAE_LTX, VAE_Wan, wav2vec2-base-960h |
| SSH tunnel open (port 9523) | **DONE** — `localhost:9523/health` returns live |
| HF SDK split venv | **DONE** — weights in `/opt/beryl/venv-hf` (hub 2.x), model in `/opt/beryl/venv-fh` (hub <1.0) |
| Systemd unit `beryl-render` | **DONE** — `WorkingDirectory=/opt/beryl/flashhead`, auto-restart on failure |
| Bakeoff scorecard ran | **PARTIAL** — node side works; local machine missing `httpx` (fix: `pip3 install httpx pillow`) |
| Preflight branch check | **DONE** — REPO section fails if you're on a stale branch missing deploy fixes |

### First L2 scorecard (measured, warm, 2 runs agree)
FlashHead Lite on L4: **~35 fps** (passes >=24), **first chunk ~689 ms** (fails the 500 ms first-frame budget; steady, not warmup). Motion is real GPU rendering. Lip-sync and identity are unmeasured (red by design). Open decision: the 500 ms budget vs the chunk size / model choice; settle it in the FlashHead vs LeapTalk vs AvatarForcing bake-off, do not edit the threshold to pass.

### Branch to use
All deploy fixes are on **`claude/brave-pascal-xjvmwk`** — not `main`. `git checkout claude/brave-pascal-xjvmwk && git pull` before running anything. Preflight now catches this and tells you exactly what to run.

### Next steps (priority order)
1. **Run the local bakeoff**: `pip3 install httpx pillow` then `python3 bakeoff/scorecard_runner.py` with the tunnel open. Gets the first real L2 scorecard (latency, FPS, painted-pixel). Lip-sync and identity stay red until VERIFY is built — that is correct.
2. **Fix TTS/MOTION port collision**: `src/server/avatar_chain.py` has `_TTS_URL` pointing to port 9522, same as MOTION. TTS (Kokoro) needs its own port (suggest 9519 or 9518; port map is in RUNBOOK section f).
3. **Step 6 — photo-in endpoint**: Upload endpoint → render node. CPU/L1: LivePortrait warp. GPU/L2: FlashHead. Connects the Studio photo slot to the live render pipeline.
4. **Step 7 — VERIFY real measurements**: SyncNet-style lip-sync offset (currently unmeasured, correctly red on scorecard). FPS and first-frame are already real.
5. **Post-first-frame Podman path**: Snapshot working node as GCE machine image (no new tooling; captures speed win immediately). Then one Podman image per bakeoff slot (FlashHead / LeapTalk / AvatarForcing) to eliminate pip-resolver failures on fresh spot VMs.

### What was fixed this session (strikes/lessons)
See `docs/LESSONS-LEARNED.md` rows 13–16 for the full record. Short version:
- **CWD bug (strike 1)**: `flash_head/inference.py` opens its config with a relative path at import time. Both the smoke test and the systemd `WorkingDirectory` must be set to `/opt/beryl/flashhead`. Fixed in commit `c408469` (smoke test) and `2072f25` (systemd unit).
- **Wrong branch pull**: Fixes were on `claude/brave-pascal-xjvmwk`; user pulled `main`. Preflight now fails fast with exact `git checkout` instructions if deploy-branch commits are missing.
- **HF SDK 2.x vs transformers 4.57.3**: `huggingface_hub>=2.1` is incompatible with FlashHead's required `transformers==4.57.3`. Solution: weights download uses a separate `/opt/beryl/venv-hf` with hub 2.x; model venv constrained to `huggingface_hub<1.0`.
- **`--include` multi-pattern syntax**: In hub 2.x, `--include "A" "B"` treats `B` as a filename. Fixed to `--include "A" --include "B"` (repeated flag). Caught by reading the real CLI docs via HF MCP connector.
- **Independent step markers**: Weights download now happens first with its own `.fh_ready` marker, independent of the model venv. A pip failure no longer blocks or re-runs the 8 GB download.

### Key file locations
| File | Purpose |
|---|---|
| `deploy/setup_gpu_node.sh` | Idempotent node setup (runs on the node via `gpu_on.sh`) |
| `deploy/gpu_on.sh` | Start VM, copy files, run setup, start service, open tunnel, run bakeoff |
| `deploy/gpu_off.sh` | Stop service, close tunnel, stop/delete VM |
| `deploy/preflight.sh` | Read-only checks before gpu_on (run this first every time) |
| `deploy/render_service.py` | FastAPI render service on the node (FlashHead inference) |
| `bakeoff/scorecard_runner.py` | Scorecard runner (needs `httpx` + `pillow` locally) |
| `docs/RUNBOOK.md` | Full operator runbook including skills documentation |
| `docs/LESSONS-LEARNED.md` | Every distinct failure + root cause + fix |

---

## BUILD TWO — backend rapid build (2026-10-07, planned, not started)

Scope: backend only. The front end (Studio, Suite, landing) is final; do not touch it. Extend `src/server/{jev,deploy,chat,multiavatar,gpu}.py`, do not rewrite. Existing GPU: `berylize-node` (project posh-eden, us-east1-c, L4).

### Rules
- Every node gets a smoke test before anything else. Count errors per node; on the 3rd distinct failure STOP patching and vendor a known-good reference (Hugging Face / NVIDIA GitHub): Pipecat + nvidia voice-agent-examples, JoyVASA, FLOAT, LivePortrait (check InsightFace license), Kokoro, faster-whisper.
- The user will supply an upgraded node spec; fold it into the node YAML before step 2.

### Build order
1. Audit: run server and tests, list live endpoints, mark real vs SIM (blendshapes are SIM today).
2. Own YAML contract (no third-party skill): `node.yaml` per node + `pipelines.yaml`, loaded once with `yaml.safe_load` (pyyaml already in requirements), schema-checked at boot and in CI. About 15 flat fields per node: slot, model, fallbacks[], device, port, key_env NAME (never the value), probe, latency_budget_ms, max_failures, repair policy. No expressions or loops in YAML; branching lives in Python.
3. Hard-coded repair ladder (about 200 lines, no LLM in the hot path): retry once with jitter -> restart node -> swap to next declared fallback (pre-resolved at load, kept warm) -> degrade stage L2->L1->L0 -> escalate. Auto-generated repair report (JSON + short markdown, to `runs/` and the sensory gland): failing node, probe evidence, each ladder step with timestamps, outcome, stage after. 3rd failure of a node sets `needs_research`.
4. CPU chain real: ASR (faster-whisper/Parakeet INT8) -> LLM (chat.py) -> TTS (Kokoro INT8) over `/ws/avatar-chat`.
5. JEV x5 in `jev.py`: face, voice, persona, director, verify, each with a deterministic fallback (JEV is early access; a repair never depends on JEV).
6. Motion node (audio -> latents, JoyVASA/FLOAT class) replaces SIM blendshapes.
7. Photo-in: upload -> render (L1 warp on CPU; L2 FlashHead/LeapTalk on GPU) -> existing Studio.
8. VERIFY real measurements: lip-sync offset, FPS, first-frame latency, painted-pixel motion.
9. `gpu_on` on berylize-node, bake-off, scorecard.

### Cost reference (1 session-hour; unit prices are assumptions except JEV $0.042/M in)
V1 budget ~$0.26-0.38 | V2 balanced ~$0.57 (test first) | V3 max ~$2.38 | V4 small-model CPU ~$0.36 | Tokkio-class ref ~$1.46. JEV is about $0.06-0.23/hr, negligible next to GPU and any MLLM judge.

### Environment variable names (values go in the Claude environment settings, never in the repo or chat)
`NVIDIA_API_KEY` (build.nvidia.com, nvapi-...), `NGC_API_KEY` (NGC container pulls), `NGC_ENTERPRISE_KEY` (name the existing deploy.py reads), `HF_TOKEN`, `TYPESAFE_API_KEY` (jev.py also accepts `JEV_API_KEY`), `GCP_SA_KEY_JSON`, `GCP_PROJECT_ID`, `GCP_ZONE`.
Allowed network domains: docs.nvidia.com, build.nvidia.com, api.ngc.nvidia.com, huggingface.co, googleapis.com.

---

## SESSION UPDATE 2026-10-03 (Beryl Mastering Suite, Deploy, Model Lab, NVIDIA keys)

### What was built this session
| Feature | Where | Status |
|---|---|---|
| **BERYL SUITE tab** (node-graph pipeline builder, ported from the "Beryl Mastering Suite" design) | `src/client/src/components/BerylSuite.tsx` | Working |
| **DEPLOY button + pipeline dropdown + SESSIONS tray** | header of BerylSuite; backend `src/server/deploy.py` | Working |
| **MODEL LAB tab** (compare up to 6 models, TTFT / tok-s) | `ModelLab.tsx`, `src/server/modellab.py` | Working |
| NVIDIA NIM + Hostinger setup scripts | `scripts/setup_ngc_nim.sh`, `setup_tokkio_ace.sh`, `setup_enterprise_keys.sh` | Working (prompt via /dev/tty) |

### Beryl Suite behaviour (matches the design screenshot)
- 9 nodes (MIC, ASR, LLM, TTS, A2F, ANM, IPS, OV, BUS), 4 layouts (Circle default, Rectangle, Vertical, Triangle), zoom/Fit, L0/L1/L2 stage buttons.
- Presets: BERYL (Kaggle brain, local voice) and TOKKIO BASELINE (Riva ASR, Nemotron, Riva TTS, Audio2Face-3D, Omniverse). Also in the quick-actions menu next to WIRING SPEC.
- **Health colours**: HOT = green, CLIENT = blue, UNTESTED/TESTING = amber, **DOWN = bright red (#ff1f3d) node with glow, and every edge touching a DOWN node turns solid red**. Edges between two healthy nodes are green dashed.
- Edge labels show latency (e.g. "think · 350ms"); uses the measured TTFT/ms after a node was tested, else the model budget.
- Node cards show port + ms; Swap cycles models; Test is a real check (LLM nodes: one real completion through `/api/lab/compare`; others: TCP connect through `/api/lab/probe`, which only allows NVIDIA, HF, loopback and pipeline hosts).
- Header: **Disconnect / Connect** (Disconnect cancels speech, releases all nodes; Connect redeploys the selected pipeline), "All hot N/M" pill (click = test all).
- Inspector: model one-click swap, "any model" box (any Kaggle/Ollama/llama.cpp/HF/NIM id), editable endpoint / port / key env-var NAME / latency budget; WIRING SPEC export (copy or .json). Key values never reach the browser.
- Live Studio: Beryl portrait (`src/client/src/assets/beryl.jpg`), blendshape bars (**simulated signal, labelled SIM**, not real Audio2Face output), **DRIFT / SKEW are real measurements of the browser's 140 ms media-clock timer**, E2E budget vs Tokkio's 1450 ms reference (published targets, not measurements).
- **Talk**: browser SpeechRecognition (Chrome/Edge only) -> `/ws/avatar-chat` (CRANE's chat brain + JEV) -> speaks the reply with speechSynthesis. Mic path not tested from automation (cannot speak into a mic); the socket round trip was tested and returned a reply.
- Claude API and Grok API model options were deliberately removed at the user's request. Do not re-add.

### Deploy (one click)
- `GET /api/deploy/pipelines` lists pipelines; `GET /api/deploy/stream/{id}?slot=N` is an SSE stream that probes each stage in order and the UI lights nodes as they clear.
- Only pipeline today: `nvidia-prebuilt` ("#1 NVIDIA PRE-BUILT (Tokkio NIM)"): MIC (client), ASR (TCP to grpc.nvcf.nvidia.com:443), LLM (real authorised 1-token call with `NGC_ENTERPRISE_KEY`, retried once), TTS and A2F (TCP only), ANM (client), OV (skipped: needs own GPU node).
- It auto-runs once when the Suite opens. Each click adds a numbered session card (#1, #2...). Cards track stage status only; they are NOT yet separate avatar/chat instances.
- **To add a pipeline**: add one entry to `PIPELINES` in `src/server/deploy.py`. It appears in the dropdown automatically.
- Limitation: ASR/TTS/A2F checks prove NVIDIA is reachable, not that the key is entitled to those gRPC services (no grpc client installed). Only the LLM stage proves auth.

### Model Lab and providers
- `/api/lab/sources|compare|ollama-pull|probe`. Reasoning models (GLM 5.3, Nemotron Lightning, Muse Glimmer) put output in `reasoning_content`; the Lab shows it under "model's thinking" and flags empty answers.
- OpenCode (`~/.config/opencode/opencode.jsonc`, NOT in this repo) now has providers: `kaggle` (default), `nim`, `nim-enterprise`, `huggingface` (router URL `https://router.huggingface.co/v1`), `llamacpp`, `local`, `berylize`. **That file holds literal keys; never commit it.** Keys also live in `~/.hermes/.env`.
- `multiavatar.load_pipelines()` reads every provider from opencode.jsonc and puts Kaggle first (`CRANE_DEFAULT_PIPELINE` overrides), so new providers appear in Multi-Suite and Model Lab with no code change.

### NVIDIA findings (verified by probing, 2026-10-03)
- Both keys unlock the free-endpoint chat catalog. Enterprise-only: llama-3.2-90b-vision, nemotron-3.5-lightning. Standard-only: nemotron-3-ultra-550b.
- Retired (404 on both keys): llama-3.1-nemotron-70b, qwen2.5-coder-32b-instruct, meta/llama-3.1-70b, 01-ai/yi-large.
- Free-tier latency is variable (gemma-4-31b cold start ~19 s).

### Fixes to existing code
- `chat.py`: `_NIM_MODELS` fallback list replaced with live models (gemma-4-31b-it first); system prompt no longer crashes when no project is open (`WS.root` None). Avatar chat works with no project.
- `main.py`: registers `modellab` and `deploy` routers.

### Known gaps / next steps (priority order)
1. **Kaggle is offline** (stale tunnel URL in opencode.jsonc). Restart the notebook, then `bash scripts/kaggle_ops.sh sync`. Beryl's brain then becomes fast; until then it falls back to NVIDIA gemma (slow cold start).
2. **Face-to-face no-keyboard build session with Beryl** (user's stretch goal, SaaS UI). Design assets: `~/Downloads/CRANE IT/Crane IT Landing Page Design (1).zip` (landing page + design system in `_ds/`). Talk already gives mic -> brain -> voice; still needed: continuous hands-free loop, barge-in, Beryl driving build actions, and a landing/SaaS shell. User also mentioned a "My Boo" folder of extra Beryl photos in Downloads; not found by name, ask for the exact path.
3. Talk uses CRANE's chat brain, not the model chosen on the Agent/Brain node. Wire the node's model into the avatar socket.
4. Real Audio2Face: blendshapes are simulated. Needs the A2F gRPC client (grpcio + nvidia-ace protos) against the NIM.
5. Tokkio ACE: re-run `bash scripts/setup_tokkio_ace.sh --nim-only` to confirm `tokkio_nim.json` (was started in background, never confirmed).
6. After each reboot start the server with keys loaded: `cd src/server && set -a; . ~/.hermes/.env; set +a; ../../.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000` (the deploy LLM check reads `NGC_ENTERPRISE_KEY` from the environment). `run_crane.sh` not updated for this.
7. Live-reload UI dev: `cd src/client && npx vite` serves :8004 and proxies /api and /ws to :8000. Production UI is `npm run build` (output to `src/server/static`, `assets/` is gitignored).
8. Mirror Loop (IPS) and Omniverse (OV) show red/DOWN locally because nothing listens on 8020/8030. Correct behaviour.
9. The old MASTERING tab (JEV face cues) is separate and not merged into the Suite.

### ✅ REPO RENAMED — CRANE-IT → CRANE-IT-V1 (2026-10-03)

- GitHub: https://github.com/tyronne-os/CRANE-IT-V1
- All UI updated: app title = "CRANE-IT V1", node watermark = "CRANE-IT V1", CodeAudit header = "CRANE-IT V1"
- `src/server/static/` is now **gitignored** — the stale prebuilt bundle that caused old UI to reappear is gone from the repo
- Fresh clone: run `cd src/client && npx vite build` once before using port 8000

### ✅ CONFIRMED FIX — page reverting to old UI (2026-10-03)

**Root cause:** `:8000` serves a prebuilt static bundle (`src/server/static/`). Every front-end code change only takes effect on `:8004` (Vite hot-reload) until you rebuild. If the bundle is stale, `:8000` shows the old version and a hard refresh or direct visit to `:8000` brings back deleted panels.

**Rule going forward (DO NOT SKIP):**
1. **Start the API with:** `bash scripts/start_crane_api.sh` — this loads `~/.hermes/.env` first so NGC_ENTERPRISE_KEY is set. Never start uvicorn directly.
2. **After any UI change:** `cd src/client && npx vite build` — this rebuilds `src/server/static/` from the current source. Until this runs, `:8000` is stale.
3. `:8004` is always current (Vite dev). `:8000` only matches after a rebuild. Use `:8004` for live development.

---

## CURRENT STATE (what works RIGHT NOW)

| What | Status |
|------|--------|
| OpenCode IDE | ✅ `~/.opencode/bin/opencode` v1.18.34 — run `opencode` from any project dir |
| Ollama (local) | ✅ Running — all 4 models on USB stick at `/mnt/usbpool/ollama` |
| dolphin3:8b | ✅ Pulled, uncensored lead model — default in OpenCode (`local/dolphin3:8b`) |
| qwen2.5-coder:3b | ✅ Pulled |
| phi3.5:latest | ✅ Pulled |
| starcoder2:3b | ✅ Pulled |
| USB Pool (85 GB LVM) | ✅ `/mnt/usbpool` — auto-mounts on boot |
| Kaggle GPU tunnel | ⏳ Batch run in progress — v2 notebook pulling qwen3-coder:30b (~20 min) |
| BitNet | ⏳ Clone re-started in terminal — llama.cpp submodule still cloning |
| berylize-node (GCP) | ❓ Not reconnected this session — tunnels down |

---

## USING OPENCODE RIGHT NOW

```bash
cd /mnt/elana/ai_apps/crane
opencode
```

Default model is `local/dolphin3:8b` (uncensored, no guardrails, runs on CPU via Ollama).
Switch models with `/models` inside OpenCode.

Configs:
- `~/.config/opencode/opencode.jsonc` — providers, models, MCP, permissions
- `~/.config/opencode/AGENTS.md` — global agent instructions (Claude Code style)

---

## KAGGLE GPU — AUTONOMOUS MANAGEMENT

The Kaggle GPU is **fully automated** now. No browser needed.

**v2 notebook is live:** `https://www.kaggle.com/code/tjjacques/crane-ollama-server`

What v2 does differently from v1:
- Installs `zstd` before Ollama (v1 failed on this)
- Pulls `qwen3-coder:30b` as `crane-agent` (30B, tool calling, 32k context, spans both T4s)
- Pulls `dolphin3:8b` as `crane-chat` (uncensored, no tools)
- Both models tuned with `OLLAMA_KV_CACHE_TYPE=q8_0` and `OLLAMA_SCHED_SPREAD=1`
- Cell 5 watchdog auto-restarts Ollama and tunnel if either dies
- Saves tunnel URL to `/kaggle/working/tunnel_url.txt` in the notebook filesystem

**To manage from the terminal (no browser, no Kaggle UI):**
```bash
# Check if run is still going
bash /mnt/elana/ai_apps/crane/scripts/kaggle_ops.sh status

# Get the current tunnel URL (only works once Cell 4 has run)
bash /mnt/elana/ai_apps/crane/scripts/kaggle_ops.sh url

# Auto-detect URL and patch opencode.jsonc
bash /mnt/elana/ai_apps/crane/scripts/kaggle_ops.sh sync

# Push a new notebook version (triggers a fresh run)
bash /mnt/elana/ai_apps/crane/scripts/kaggle_ops.sh push
```

**When Kaggle tunnel is up**, OpenCode default switches to `kaggle/crane-agent` (Qwen3-Coder 30B).

**When tunnel dies (URL changes):**
```bash
bash /mnt/elana/ai_apps/crane/scripts/kaggle_ops.sh sync
```

---

## RESUME TASKS (in priority order)

### 1. Confirm BitNet clone finishes — PENDING
Check terminal tab 7:
```bash
# If the clone finished, run deps:
cd /mnt/usbpool/bitnet && pip3 install -r requirements.txt
# Then download the model:
python3 setup_env.py -md /mnt/usbpool/bitnet/models -q i2_s
```
Model: `microsoft/BitNet-b1.58-2B-4T` (~400 MB, CPU-only inference)

### 2. Sync the Kaggle tunnel URL — PENDING
Once the Kaggle batch run finishes (allow ~30-45 min from push):
```bash
bash /mnt/elana/ai_apps/crane/scripts/kaggle_ops.sh sync
```
This patches `opencode.jsonc` and verifies the tunnel responds.

### 3. Push all local commits to GitHub
```bash
cd /mnt/elana/ai_apps/crane && git push origin main
```
Local commits not yet pushed: `02a0a48`, `8371ba1`

### 4. Reconnect berylize-node (GCP L4 GPU)
```bash
bash /mnt/elana/ai_apps/crane/scripts/connect_berylize.sh
```
Then verify 14B download completed and start vLLM:
```bash
gcloud compute ssh berylize-node --zone=us-east1-c --project=posh-eden \
  --command='du -sh /mnt/disks/extra-storage/huggingface/models--Qwen--Qwen2.5-Coder-14B-Instruct-AWQ/blobs/'
# Should be ~9.2 GB. Then:
gcloud compute ssh berylize-node --zone=us-east1-c --project=posh-eden \
  --command='sudo systemctl start berylize-vllm && sudo journalctl -fu berylize-vllm'
```

### 5. Abliterate the coder models (if guardrails detected)
Tool: `sunkencity999/blasphemer` (Heretic fork)
- `qwen2.5-coder:3b`, `phi3.5:latest`, `starcoder2:3b` may have guardrails
- `dolphin3:8b` is already fully uncensored — skip it

### 6. Test full CRANE IDE
```bash
cd /mnt/elana/ai_apps/crane
uvicorn src.server.main:app --host 0.0.0.0 --port 8000
```
Then: Mastering tab → mic → avatar should go idle→listening→thinking→speaking via berylize TTS

---

## SYSTEM MAP

| Component | Location | Status |
|-----------|----------|--------|
| CRANE FastAPI | `/mnt/elana/ai_apps/crane/` port 8000 | Not running |
| OpenCode | `~/.opencode/bin/opencode` v1.18.34 | ✅ Ready |
| Ollama | `/usr/local/bin/ollama` | ✅ Running |
| USB Pool | `/mnt/usbpool` (85 GB LVM, 4 drives) | ✅ Mounted |
| Ollama models | `/mnt/usbpool/ollama` | ✅ All 4 pulled |
| BitNet | `/mnt/usbpool/bitnet` | ⏳ Cloning |
| Kaggle T4x2 | `crane-agent` (qwen3-coder:30b) | ⏳ Pulling |
| berylize-node | GCP L4, `34.74.41.235` | ❓ Tunnels down |
| vLLM (14B) | berylize port 8010 | ❓ Not started |
| Speaches TTS | berylize port 8013 | ❓ Container may still be running |

---

## PORT MAP

| Port | Service |
|------|---------|
| 8000 | CRANE FastAPI |
| 8001 | CRANE preview |
| 8002–8005 | CRANE reserved |
| 8010 | berylize vLLM (Qwen2.5-Coder-14B-AWQ) |
| 8011 | MiniMax H3 — not yet served |
| 8012 | Kokoro TTS standalone — not started |
| 8013 | Speaches / elana-voice Podman |
| 11434 | Ollama local |

---

## KEY FILES

| File | Purpose |
|------|---------|
| `~/.config/opencode/opencode.jsonc` | OpenCode providers, models, MCP, permissions |
| `~/.config/opencode/AGENTS.md` | Global agent instructions (Claude Code emulation) |
| `scripts/kaggle_ops.sh` | Kaggle API: push/status/url/sync |
| `scripts/kaggle_set_url.sh` | Patches opencode.jsonc with new tunnel URL |
| `scripts/kaggle_ollama_server.ipynb` | Notebook v2 (qwen3-coder:30b agent) |
| `scripts/setup_usbpool.sh` | Rebuild LVM pool if drives change |
| `scripts/setup_ollama_models.sh` | Pull all 4 local models to USB |
| `scripts/connect_berylize.sh` | SSH tunnels + start berylize services |
| `src/server/main.py` | FastAPI app — all endpoints + WebSockets |
| `src/server/mcp_server.py` | CRANE MCP server (SSE, port 8000/mcp) |
| `src/server/coderag.py` | CodeRAG small model service (needs Ollama) |
| `src/client/src/components/MasteringPanel.tsx` | Animated avatar + mic + TTS |

---

## KNOWN ISSUES

1. BitNet shallow clone corrupted submodule — full re-clone running now
2. Kaggle tunnel URL changes every notebook session — run `kaggle_ops.sh sync` to fix
3. berylize-node GPU had detach event — fixed with stop+start, but tunnels need re-opening
4. elana-voice container not set to auto-start on reboot (no systemd unit)
5. MiniMax H3 weights on nvme0n2 but vLLM serve not configured (port 8011 empty)
6. REPORTS tab in CRANE nav is a stub

---

## WHAT TO TELL NEXT CLAUDE SESSION

"Continue CRANE setup. HANDOFF.md is at `/mnt/elana/ai_apps/crane/HANDOFF.md`. All 4 local models are pulled to `/mnt/usbpool/ollama`. OpenCode is configured at `~/.config/opencode/opencode.jsonc`. The next tasks are: (1) run `kaggle_ops.sh sync` to get the Kaggle tunnel URL, (2) finish BitNet install, (3) push unpushed commits, (4) reconnect berylize-node."

---

## SESSION UPDATE 2026-10-03 06:35 AM New Orleans (CDT; user wrote "CST") — user has been awake ~24 h

### THE EXACT PROBLEM, AS I UNDERSTAND IT
The Beryl Suite pipeline graph shows green, but **nothing proves the avatar actually talks**.
1. **Green != working.** `src/server/deploy.py` probes ASR / TTS / A2F with a **TCP connect only** to `grpc.nvcf.nvidia.com:443`. Only the LLM stage is a real authorized call (1-token NIM request). No audio is ever sent through ASR -> LLM -> TTS -> Audio2Face, so "the avatar does not talk" is **still unverified**, not fixed. This is why the user feels it is "whack-a-mole": the lights never test the real signal path.
2. **"LLM: NGC_ENTERPRISE_KEY not set"** was caused by me, not the key. I restarted uvicorn without sourcing `~/.hermes/.env`. The key itself is fine (70 chars, `nvapi-` prefix, no quotes/spaces; checked without printing it). Fix: start the API with `scripts/start_crane_api.sh` (sources the env file, then uvicorn). **Any restart that skips this reproduces the error.** After the fix the probe returned LLM "authorized" 346 ms, 6/6 stages live.
3. **Page "reverting to the deleted page"**: two servers exist. `:8004` = live Vite dev copy (always current). `:8000` = FastAPI serving a **prebuilt snapshot** in `src/server/static` (was stale and still contained the MASTERING panel). Rebuilt with `cd src/client && npx vite build`. `index.html` is now served with `Cache-Control: no-store`. **After any front-end change, `:8000` is stale until rebuilt; use `:8004` for work.** Note `vite build` rewrites `src/server/static` (assets are gitignored, `index.html` is tracked) and can restart uvicorn via --reload.
4. **Triage clock reset / data wiped** on every new red node (one global incident). Rewritten: **one incident per red node**, each with its own clock, error code and log; open incidents sit side by side (1 = full width, 2 = halves, 3+ = scroll sideways). Incidents are never deleted (localStorage `beryl-incidents`, last 200). An incident only closes when its node is confirmed `hot`/`client` (not while `testing`/`untested`). On close, one record per node is POSTed to `/api/reports/triage` (`src/server/data/triage_reports.jsonl`).
5. **OV (Omniverse) red** is expected: nothing listens on :8030; it needs its own GPU node. ANM red = nothing on :8015. Neither is a key problem.

### DONE THIS SESSION
- BerylSuite: GPU meter (vendor GCP·L4, util, VRAM, uptime timer, cost) polling `/api/gpu/status`; collapsible NODE INSPECTOR; IPS Mirror Loop made client-side; per-node triage incidents (above); error-code chips + "resolved by" (auto-retest / manual-test).
- Backend: `reports.py` (`/api/reports/triage`, `/export`), `source.py` (read-only `/api/source/tree|file`, secret files skipped, key-shaped strings redacted, path traversal blocked).
- New **CODE tab** (`CodeAudit.tsx`): VS Code style read-only viewer — file tree, tabs, search, the live **generated pipeline code**, and the saved triage log. `CodePanel` gained a `readOnly` prop.
- **MASTERING tab removed** (user request). `MasteringPanel.tsx` deleted; `AvatarFace` moved to `AvatarFace.tsx` (Multi-Suite uses it).
- GCP `berylize-node` (L4) started via `/api/gpu/start`.

### OPEN / NOT DONE (priority order)
1. **Real end-to-end talk test** (speech -> Riva ASR -> LLM -> Riva TTS -> A2F blendshapes) using the NVIDIA Riva python client / A2F gRPC; make the lights reflect it. NVIDIA troubleshooting notes found: TTS/A2F audio sample rate must match (Unreal path is 16 kHz only), A2F burst mode causes jitter -> use `blendshape_streaming_fps: 90`, A2F public API errors ("invalid response from UAM" = bad key).
2. **NVIDIA CLI/SDK NOT updated**: `ngc` is not installed on this machine and `.venv` has no `pip`. Install `ngc-cli` and `nvidia-riva-client` (use `python -m ensurepip` or a fresh venv).
3. **GCP berylize-node is RUNNING and billing (~$0.40/h)**. Pause with `POST /api/gpu/pause` when done (auto-pause after 2 h idle). I tried to serve Qwen3.5-9B GGUF with `/usr/local/lib/ollama/llama-server` on remote :8000 (needs sudo; files in `/opt/aurelia/models` are owner-only). **Load success was not verified**; the Ollama systemd unit is broken (`/usr/local/bin/ollama` missing). `vllm` exists at `~/.local/bin/vllm` for user `tjlsudadverified_gmail_com`; a Qwen2.5-Coder-32B-abliterated is in that user's HF cache.
4. Kaggle tunnel still offline (stale URL in `opencode.jsonc`): restart notebook, `bash scripts/kaggle_ops.sh sync`.
5. NVIDIA GUIDE dropdown / per-node doc links were built from the Tokkio PDF and are **unverified**; docs moved (301 to `archive.docs.nvidia.com`). Check each URL.
6. Pipeline codegen (`pipelineCode` in BerylSuite) is template-based and not proven executable; a "CODE" tab inside the node inspector is half-wired (type exists, no button) and now superseded by the top-level CODE tab.
7. Blendshapes in the live studio are still simulated ("SIM").
8. Security: `~/.config/opencode/opencode.jsonc` holds API keys in plaintext (outside the repo). Do not commit it. Rotate if it was ever shared.

### NOT COMMITTED ON PURPOSE
`src/server/data/` (runtime triage log) and `src/server/static/index.html` (build output).

## Saved utility: USB restore
`sudo bash scripts/usb_nuke.sh` — proven on hardware (user-confirmed). One command wipes and exFAT-formats up to 3 USB sticks (labels BERYL-01..03), USB-only, boot disk protected.
