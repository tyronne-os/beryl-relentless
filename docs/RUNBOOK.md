# Beryl Relentless — Operator Runbook

For someone who has never seen this project. All commands run from the repo root on your Ubuntu laptop unless stated. Env var names only appear here; never put values in this repo.

Goal of the backend: one 2D photo + live audio -> conversational 3D-appearing avatar. Stages: L0 idle (client loop), L1 CPU+API chain, L2 GPU render on the GCP L4 node `berylize-node` (project `posh-eden`, zone `us-east1-c`).

## (a) One-time laptop setup

```bash
sudo apt update
sudo apt install -y git curl python3 python3-pip openssh-client
# google-cloud-cli: add Google's apt repo first, then install
sudo apt install -y apt-transport-https ca-certificates gnupg
curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
sudo apt update && sudo apt install -y google-cloud-cli
gcloud --version
git clone https://github.com/tyronne-os/beryl-relentless
cd beryl-relentless
git checkout claude/brave-pascal-xjvmwk   # deploy fixes land here first; preflight fails if you are missing them
pip3 install httpx pillow     # needed by bakeoff/scorecard_runner.py and prep_fixture.sh
```

(The apt repo lines are Google's standard install steps; the exact commands were not run in this project's history: unverified. If they fail, follow Google's current Ubuntu install page.)

Create `.env` in the repo root. It is gitignored; never commit it.

```bash
cp .env.example .env
nano .env
```

Rules for `.env`:
- One `KEY=value` per line. No spaces around `=`. No stray text lines (a bare word is executed as a command by bash).
- QUOTE any value containing spaces, e.g. `GCP_SA_KEY_JSON_PATH="/home/me/Downloads/My Folder/key.json"`.
- Replace every placeholder (`hf_...`, `nvapi-...`) or preflight will fail.
- Note: `.env.example` lists `GCP_SA_KEY_JSON` (the JSON itself). The deploy scripts read the key from a file path instead (see below).

Variable names used by the deploy scripts (`deploy/lib.sh`, `preflight.sh`):

| Name | Used for | Default |
|---|---|---|
| `GCP_SA_KEY_JSON_PATH` | path to service-account key file | `/tmp/sa.json` |
| `GCP_PROJECT` | GCP project | `posh-eden` |
| `GCP_ZONE` | zone | `us-east1-c` |
| `GCP_INSTANCE` | VM name | `berylize-node` |
| `HF_TOKEN` | Hugging Face read token (weights download on node) | none, required |
| `RENDER_PORT` | GPU render port | `9523` |
| `CONTROLLER_URL` | local controller | `http://localhost:9500` |
| `HEALTH_TIMEOUT` | seconds gpu_on waits for health | `180` |

Other names in `.env.example` (for the L1 chain, not needed for L2 deploy): `HUGGING_FACE_HUB_TOKEN`, `NVIDIA_API_KEY`, `NGC_API_KEY`, `NGC_ENTERPRISE_KEY`, `TYPESAFE_API_KEY`, `JEV_API_KEY`, `GITHUB_TOKEN`, `GH_TOKEN`, `GCP_SA_KEY_JSON`, `KAGGLE_USERNAME`, `KAGGLE_KEY`, `CRANE_JEV`, `CRANE_JEV_MODEL`, `CRANE_STAGE`.

Load it into your shell before running scripts (the scripts do not source `.env` themselves: unverified, check `deploy/lib.sh`, which has no `.env` loading):

```bash
set -a; source .env; set +a
```

## (b) GCP prerequisites

1. Service account in project `posh-eden` with roles `roles/compute.admin` and `roles/iam.serviceAccountUser` (GCP Console -> IAM). Without compute permission, `gcloud compute instances list` returns "Required 'compute.instances.list' permission".
2. Download its JSON key to your laptop, outside the repo, and point `GCP_SA_KEY_JSON_PATH` at it.
3. If your environment injects a proxy access token, gcloud ignores your key. `deploy/lib.sh` already runs every gcloud call as:
   `CLOUDSDK_AUTH_ACCESS_TOKEN="" GOOGLE_APPLICATION_CREDENTIALS="$GCP_SA_KEY_JSON_PATH" gcloud ...`
   If you run gcloud by hand, use the same prefix.
4. The VM `berylize-node` (L4 GPU) must already exist; gpu_on.sh only starts it.

## (c) Reference photo prep

The photo stays local (`bakeoff/fixtures/reference.*` is gitignored). Use a clear, front-facing, mouth-closed photo of someone who has consented (see SCALING-AND-TRAINING-DATA.md, privacy).

```bash
./deploy/prep_fixture.sh            # lists the 12 newest images in ~/Downloads (override: PHOTO_DIR=/dir)
./deploy/prep_fixture.sh 3          # use number 3 from the list
./deploy/prep_fixture.sh "/path/with spaces/photo.jpg"
```

Healthy output: `reference photo ready: bakeoff/fixtures/reference.jpg  (from ...)`. It centre-crops to square and resizes to 1024x1024.

## (d) preflight -> gpu_on -> gpu_off

### 1. Preflight (read-only, changes nothing)
```bash
./deploy/preflight.sh
```
Checks: gcloud/curl/python3/ssh installed; SA key file exists, is valid JSON, and belongs to `posh-eden`; `HF_TOKEN` set, not a placeholder, accepted by Hugging Face; reference photo present; SA can activate and see the VM; if the VM is running, one SSH round-trip checks GPU visible, >=30 GB free on /opt, venv, systemd unit port, port free, weights, FlashHead code.
Healthy: every line `[ OK ]` (a `[WARN]` for "venv not built yet", "weights not on node yet" is normal on first run) and last line `PREFLIGHT: GO (N warn).` Exit 1 and `[FAIL]` lines include the fix.

### 2. GPU on
```bash
./deploy/gpu_on.sh               # add --no-bakeoff to skip the scorecard run
```
Steps: runs preflight; starts the VM if not RUNNING; copies `render_service.py` and `setup_gpu_node.sh` to the node; runs setup (idempotent: clones SoulX-FlashHead, builds venv with torch 2.7.1 cu128, downloads ~8 GB weights, writes systemd unit `beryl-render` with your `RENDER_PORT`); restarts the service; waits up to `HEALTH_TIMEOUT` seconds for `/health` ON the node over SSH; opens the SSH tunnel; tells the controller to go L2; runs the bake-off.
Timing: first run about 20 minutes (venv ~10 min plus weights); later runs shorter (unverified exact figure). Re-running is safe; there is no skip flag.
Healthy: `[gpu_on] tunnel OK`, no `render model = passthrough` warning, and `=== done. tunnel stays open ... ===`.
If you see `WARNING: render model = passthrough`, no real model loaded; L2 is not real. Check logs:
```bash
gcloud compute ssh berylize-node --zone=us-east1-c --project=posh-eden --command="sudo journalctl -u beryl-render -n 50 --no-pager"
```
(prefix with `CLOUDSDK_AUTH_ACCESS_TOKEN="" GOOGLE_APPLICATION_CREDENTIALS="$GCP_SA_KEY_JSON_PATH"` if needed).
`controller not running locally (ok for now)` is expected today: no controller HTTP server exists in the repo yet (unverified; `harness/controller/session.py` has no server).

Quick manual check while the tunnel is open:
```bash
curl -s localhost:9523/health
```

### 3. GPU off
```bash
./deploy/gpu_off.sh              # default --stop: stops the VM
./deploy/gpu_off.sh --delete     # deletes the VM (you lose its disk state)
./deploy/gpu_off.sh --keep-vm    # leaves it running; STILL BILLING
```
Notifies the controller to degrade, closes the tunnel, stops `beryl-render`, then stops (or deletes) the VM. Healthy last line: `[gpu_off] === L1 active ===`.

## (e) The SSH tunnel

The GCP firewall only lets SSH (port 22) in. We do not open other ports. `lib.sh` `tunnel_start` runs `gcloud compute ssh ... -N -L 9523:localhost:9523`, so `http://localhost:9523` on your laptop reaches the render service on the node. PID is in `/tmp/beryl_tunnel.pid`, log in `/tmp/beryl_tunnel.log`. Health waits happen on the node itself (curl over SSH), not from outside, so a closed firewall port cannot fake a failure. `gpu_off.sh` kills the tunnel. If the tunnel dies, re-run `./deploy/gpu_on.sh` (safe) or check `cat /tmp/beryl_tunnel.log`.

## (f) Port map

Project services use 9500+ (another project on these machines reserves lower ports).

| Port | Service | Source | Status |
|---|---|---|---|
| 9500 | controller (CONTROLLER_URL default) | `deploy/lib.sh` | no server in repo yet (unverified) |
| 9520 | ASR (faster-whisper) | `harness/nodes/asr/adapter.py` | adapter exists |
| 9521 | LISTEN | `harness/nodes/listen/adapter.py` | adapter exists |
| 9522 | MOTION | `harness/nodes/motion/adapter.py` | adapter exists. Note `src/server/avatar_chain.py` also defaults its TTS URL to 9522 (`/tts`): possible collision, unverified |
| 9523 | GPU render (on node) | `deploy/render_service.py` | tunnelled to laptop |
| 9524 | render fan-out | `harness/nodes/render/adapter.py` | adapter exists |
| 9525 | VERIFY | `harness/nodes/verify/adapter.py` | adapter exists |

## (g) Cost notes

Per session-hour, from CLAUDE.md (unit prices are assumptions, not billing data):

| Config | $/hr |
|---|---|
| V1 minimal | ~0.26-0.38 |
| V2 balanced (test first) | ~0.57 |
| V3 max | ~2.38 |
| V4 CPU-only small | ~0.36 |
| Tokkio-class reference | ~1.46 |

`gpu_off.sh` stops the VM, which stops GPU/CPU billing; persistent disk storage continues to bill while stopped (standard GCP behaviour; amount unverified). `--keep-vm` keeps billing. Real L4 spot price from the GCP billing catalog is still an open item. Always run `gpu_off.sh` when finished.

## (h) Claude session skills — what actually helped

Two skills were added during the session that cracked the "endless error loops" problem. Here is an honest account of what each did.

### Hugging Face MCP connector (not a Claude Code skill — a session connector)

**Verdict: directly prevented a silent failure.** During research for the HF SDK upgrade, the connector was used to read the real `huggingface_hub` 2.1.1 CLI reference. That reading caught a real gotcha: in 2.x, `--include "Pattern_A/*" "Pattern_B/*"` treats the second argument as a filename, **not** an additional pattern. The correct form is `--include "Pattern_A/*" --include "Pattern_B/*"` (flag repeated per pattern). Without catching this, the weights download would have silently fetched only `Model_Lite/*` and written `.fh_ready`, and the render service would have failed at startup with a missing VAE file — after a 30-minute setup run. The connector was also used to verify the exact HF repo layout for `Soul-AILab/SoulX-FlashHead-1_3B` (Model_Lite 6.1 GB, VAE_LTX 1.7 GB, VAE_Wan 0.5 GB) and the `facebook/wav2vec2-base-960h` download patterns.

**When to use it:** Any time a new HF repo, model weights, or HF CLI command is added to a deploy script — verify the repo layout and CLI reference before writing the command.

### One-Shot Agentic Feature Generation plugin

**Verdict: not used in this session yet.** The plugin was added after the primary setup work was underway. Its value is for future bakeoff automation: the scorecard runner (`bakeoff/scorecard_runner.py`) calls `/render` and measures real frame metrics. One-Shot's code-execution capability could run a bakeoff slot end-to-end — including fixture prep, tunnel health check, and scorecard parse — without manual steps. The right time to wire it in is after the local bakeoff runs cleanly (i.e., after `pip3 install httpx pillow` and the next `gpu_on.sh --no-bakeoff` run).

**When to use it:** Step 8 onwards — automated bakeoff runs comparing FlashHead vs LeapTalk vs AvatarForcing.

### Summary table

| Tool | Session impact | When it matters |
|---|---|---|
| HF MCP connector | Caught `--include` multi-pattern syntax bug; verified weights repo layout | Any new `hf download` command in deploy scripts |
| One-Shot plugin | Not yet exercised; agentic code-exec for bakeoff automation | Step 8+, automated render bakeoff |

## (i) Reading the bake-off scorecard

Output: `bakeoff/results/<timestamp>.json` (gitignored). Console shows per test PASS / FAIL / `FAIL (critical)`. Top-level fields: `all_green`, `critical_failures`, and `tests[]` each with `id`, `passed`, `critical`, `render_model`, `render_device`, `scorecard`, `first_frame_ms`. Exit code 0 only if no critical test fails.

| Test id | Critical | Measured? |
|---|---|---|
| `no_css_only_motion` | yes | Real: painted-pixel diff computed on server from rendered frames (`render_service.py _painted_stats`) |
| `first_frame_latency` (< 500 ms) | yes | Real: wall-clock round trip of the `/render` call |
| `fps_realtime` (>= 24) | yes | Real, from the render service's reported fps |
| `lipsync_range` (|offset| ≤ 133 ms) | yes | Measured on a full-length rendered mp4 (saved as `bakeoff/results/lipsync_lipsync_range.mp4`): mouth-region motion vs speech-envelope rate, cross-correlated. Red with a reason if correlation is weak, ffmpeg/numpy are missing, or there is no speech. Manual check: `python3 -m harness.nodes.verify.lipsync CLIP.mp4` |
| `identity_stable` (drift <= 0.15) | no | NOT yet measured. Runner passes no identity_drift; red/uninformative by design |
| `stage_consistent` | no | Compares stated stage "L2" to telemetry the runner itself supplies; weak signal |

Red on lip-sync or identity is the correct honest result until those measurements are built. Do not "fix" them by hard-coding values (that was removed, see LESSONS-LEARNED.md). If `render_model` is `passthrough`, any green result is meaningless. Also: the runner uses a generated 5 s silent WAV when fixture audio files are missing from `bakeoff/fixtures/`.
