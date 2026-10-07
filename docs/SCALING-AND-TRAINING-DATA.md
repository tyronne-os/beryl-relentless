# Scaling and Training Data (PLAN, NOT DONE)

Nothing here is implemented unless it says so. Figures not found in the repo or transcript are marked unverified.

## 1. Scaling plan

### Fast boot (not built)
Today a fresh node needs about 20 minutes on first run (venv ~10 min plus ~8 GB weights; `setup_gpu_node.sh`). Plan: after one successful `gpu_on.sh`, snapshot the boot disk (or build a custom GCE image) containing `/opt/beryl/venv-fh`, `/opt/beryl/flashhead`, `/opt/beryl/weights`, and the systemd unit. New nodes boot from it and only run the idempotent setup, which skips finished steps (`.ready`, `.fh_ready`). Expected boot ~1 minute is a target, unverified. Alternative: keep weights on a separate persistent disk and attach it (CLAUDE.md step "attach weights disk").

### Spot VMs
CLAUDE.md specifies a GCE spot VM (g2-standard-4 + 1x L4; T4 fallback). Spot VMs can be preempted: L2 must fall back to L1 (already the stage-ladder rule). Real L4 spot price: unverified, open item. `gpu_on.sh` currently starts an existing VM; creating spot VMs from the image is not implemented.

### Controller and routing
Reference pattern from `knowledge/KB-NVIDIA-02.md`: Tokkio's SDR (Stream Distribution & Routing) assigns each session to a free pod; message bus plus per-session lifecycle on client connect/disconnect. Plan: one Beryl controller owns sessions and the L0/L1/L2 state, keeps a registry of GPU render pods with health, and assigns each new session to a free pod, else serves L1. The controller HTTP service does not exist yet (port 9500 reserved).

### Concurrency per L4
Tokkio reference (KB-NVIDIA-02, from search snippets, not verified on primary page): 1-6 concurrent users per deployment, set by GPU count; 1 stream minimum 2x L4 for the full Tokkio workflow. For Beryl's FlashHead Lite on one L4: no repo note gives a concurrency figure. Streams per L4: unverified. KB-RESEARCH-03 says FlashHead is "up to 40 FPS" (source: its notes) and that 96 FPS is a 4090 number; nothing measured on L4. Measure with `GET /benchmark` and the bake-off before promising any number. Plan the pod count as: measured sustainable fps per GPU / 24 fps per stream (24 fps is the bake-off threshold).

### Region and quota
Current zone `us-east1-c`. GPU quota (L4/T4 per region, spot vs on-demand quotas are separate in GCP) must be requested before scaling; current quota values: unverified. Pick regions near users to reduce latency (first-frame budget is 500 ms in the bake-off) and keep the weights image in each region (images are global; disks are zonal).

### Other prerequisites
Per-node port plan (9500+), tunnels replaced by an internal load balancer or IAP for multi-node (not designed), and secrets via GCP Secret Manager rather than `.env` for production.

## 2. Data the system produces or should capture

| Data | Where / status | Use |
|---|---|---|
| Bake-off scorecards | `bakeoff/results/*.json` (produced today, gitignored) | Compare render models (FlashHead vs LeapTalk vs AvatarForcing), regression tracking |
| Per-turn JEV outputs: face, voice, persona, director, verify | `harness/jev/*.py` return dicts; not persisted yet (plan: write per-turn records) | Evaluation of emotion match, tone, in-character rate; training data for replacing JEV with local models |
| VERIFY painted-pixel stats (`changed_pixel_area`, `frame_diff_mean`) | Produced by `render_service.py`, passed to VERIFY | Detect fake/CSS-only motion; motion quality trend |
| Latency per node | Node `latency_budget_ms` in YAML; measured per-call timing partly present (`first_frame_ms`) | Budget enforcement, capacity planning |
| ASR transcripts | Produced by ASR node; not stored | Word-error evaluation, LLM context. Sensitive: see privacy |
| Motion latents | Produced by MOTION node (kb/s stream) | Train/evaluate audio-to-motion; small compared to video |
| Lip-sync offset, identity drift | NOT measured yet | Must be built before they can be data |

Capture design (plan): one JSONL record per turn with session id (random, not linked to a person), stage, per-node timings, JEV outputs, VERIFY stats, model ids and versions. Store text and numbers by default; raw media only with consent.

## 3. Consent and privacy

- Reference photos are private: `bakeoff/fixtures/reference.*` and `*.wav` fixtures and `bakeoff/results/*.json` are gitignored. Never commit or paste them.
- Get explicit written consent from any real person whose likeness or voice is used (photo, cloned voice, or avatar made from them). Recognising a known person is an acceptance test; use only people who agreed.
- Do not log raw user audio or video without consent. Default to transcripts/features with no raw media; tell users what is stored.
- Retention limits: set and document them before collecting (suggested starting point, a decision for the owner: delete raw media at session end; keep derived metrics for a fixed period). No retention policy exists yet.
- Third parties: audio/text sent to NIM, Hugging Face, or TypeSafe JEV leaves your control; check each provider's data terms (unverified here).
- Licences gate training use: LivePortrait/InsightFace (flagged non-commercial), TontaubeV1 (custom community licence) are open items in CLAUDE.md.
- Credentials never go into datasets or logs.
