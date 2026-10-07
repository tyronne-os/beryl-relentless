# KB-RESEARCH-03 — Hugging Face Papers Sweep (Apr–Oct 2026)

Method: hf_fs search over hf://papers, then metadata.json for code/model links.
"Code" = GitHub repo or HF weights linked on the paper page.
**Licenses NOT yet checked** for any item below — the license-gate rule applies before adoption.

---

## A. Streaming talking-head / avatar render (the render node)

| Paper | Date | Why it matters | Code/weights |
|---|---|---|---|
| LeapTalk 2608.00079 | Jul 29 2026 | Single-step Brownian-bridge distillation; paper claims 1 step, up to 200 FPS; anchored to persistent reference => less identity drift | GitHub zhangrongxiang/LeapTalk (~200 stars), HF z-rx/leaptalk, HF Space hugging-apps/leaptalk-talking-head. NOTE: 200 FPS is a paper claim, unverified on L4; 96 FPS is FlashHead's 4090 number. |
| AvatarForcing 2603.14331 | Mar 15 2026 | One-step streaming, 1.3B student, 34 ms/frame, dual-anchor forcing for infinite streams | HF lycui/AvatarForcing; Space AIBRUH/avatar-forcing (Tyronne's own HF account, not running) |
| Avatar Forcing 2601.00664 | Jan 2 2026 | Real-time interactive head avatar, diffusion forcing + label-free preference optimisation | paper only |
| Hallo-Live 2604.23632 | Apr 26 2026 | Text->joint audio+video, 20.4 FPS/0.94 s on 2xH200; HP-DMD reweights by fidelity, speech naturalness, A/V sync | HF fudan-generative-ai/Hallo-Live. Over budget; useful for preference-distillation recipe. |
| InteractiveAvatar 2606.22905 | Jun 30 2026 | Long-Short Visual Memory (identity consistency) + Reasoning-Reaction Module (intent-aware) | none linked |
| Avatar-Forever 2608.12107 | Aug 12 2026 | decoupled parallel training, chunk caching; flags Gemma-3 dependency | not checked |
| Omni-LiveAvatar 2608.13602 | Aug 17 2026 | minute-level joint A/V; code not released | none |
| LiveAnimate 2608.11745 | Aug 13 2026 | pose-driven 14B, bounded attention cache | not checked |
| Live Avatar 2512.04677 | Dec 2025 | the 14B / 5xH800 reference (existence proof, over budget) | known |

---

## B. Enabling recipes for fewer-step / lower-GPU streaming

- **Causal Forcing++ 2605.15141** (May 14 2026): frame-wise 1-2 step AR distillation, ~50% lower first-frame latency; code thu-ml/Causal-Forcing + minWM (HF MIN-Lab/minWM).
- **minWM 2605.30263** (May 2026): full-stack open framework to turn bidirectional video diffusion into real-time causal models.
- **DSA 2606.04432**: dynamic per-frame step allocation — directly relevant to "less dependency on GPU".
- **Speculative decoding for AR video 2604.17397**: directly relevant to GPU relief.
- Causal-rCM 2606.25473, Mask Forcing 2609.09123, ViRDM 2609.28923 — fewer steps/less compute per frame.
- **SANA-Streaming 2605.30409**: real-time hi-res video-to-video on consumer GPUs (candidate for upscale/SR slot).

---

## C. Listening / conversational awareness (the "alive when silent" gap)

- **UniLS 2512.09327** (Dec 2025): first end-to-end unified speak+listen face motion from dual-track audio; two-stage (audio-free motion prior, then audio modulation); up to 44.1% better listening metrics; HF xg-chu/UniLS. **Strongest match to Beryl's idle/listening thesis.**
- [DROPPED] ReactMotion 2603.15083 — listener body motion, not 2D-photo head/face class.
- **REALM 2609.33095** (Sep 27 2026): coarse-to-fine reactive listener facial motion with speaker-cue timing. Newest relevant; code not checked.
- Full-duplex speech papers (eval criteria + training recipes for turn-taking/backchannel/barge-in):
  - Duplex-MPE 2609.31948, Duplex Cue 2609.13117, "Conversation is a Two-Body Problem" 2610.08125 (Oct 6 2026), Multi-Faceted Interactivity Alignment 2606.11167, BayLing-Duplex 2606.14528, UAF 2604.19221, DuplexChat corpus 2607.04941.

---

## D. Evaluation / "receipts" (the sensory gland)

- **VideoFDB 2605.30256** (May 28 2026, NVIDIA authors): first benchmark for full-duplex audio-visual agents; 237 dyadic clips, 11 nonverbal dynamics; rubric LM-as-judge; dataset nvidia/video-full-duplex-benchmark (gated).
  **KEY FINDING: cascaded speech→avatar systems "fundamentally preclude" full-duplex nonverbal cues** — validates adding LISTEN node + JEV cross-checks rather than plain ASR→LLM→TTS→render chain.
- **Artifact-Bench 2605.18984** (May 2026): MLLMs detect artifacts in AI video poorly => do not rely on generic MLLM judge alone; supports calibrated typed classifiers + deterministic metrics.
- WorldJen 2605.03475, **FlowPortrait 2603.00159** (GRPO with MLLM-based reward for lip-sync/expressiveness/motion), **HighSync 2605.16918** (lip-sync, 512x512) — reward/eval designs reusable as scorecard components.
- Older standard: SyncNet/Sync-C style lip-sync metrics (Perceptually Accurate 3D Talking Head 2503.20308 definitions), FantasyTalking2 2508.11255 (preference optimisation).

---

## E. Speech nodes (ASR/TTS)

**ASR:**
- Voxtral Realtime 2602.11298 (Feb 2026): sub-second streaming matching offline
- Qwen-Audio-3.0-ASR 2609.07549 (Sep 2026): streaming + hotwords
- CarelessWhisper 2508.12301: turn Whisper causal
- Unified ASR transducer 2604.19079

**TTS:**
- **TontaubeV1 2609.08703** (Sep 2026): 2.9B, ~200 ms first audio on RTX 5090; **custom community license — check before adopting**
- X2Streaming-TTS 2608.18661: token-level streaming from LLM text
- CTC-TTS 2602.19574, ultra-low-latency block-wise TTS 2604.12438
- Compare to chosen Qwen3-TTS 0.6B (97 ms claim)

---

## F. Other reference

- Avatar V 2606.13872 (Jun 2026): video-reference avatar generation (production-scale, reference only)
- KlingAvatar 2.0 (Dec 2025), LongCat-Video-Avatar 1.5 2605.26486 (May 2026) — reference only
- Kev (jaredpalmer/kev-0.5b/0.8b/4b/9b/27b, updated Sep–Oct 2026): replaced Decider with JEV; Kev becomes optional offline fallback only.

---

## G. THE CORE CLASS: single 2D image + audio → 3D-appearing live human (VASA-1 class, no mesh/rig)

Filter: exclude rigged-mesh, Gaussian-splat/3D-reconstruction, gaming, body-only.
Keep: one reference photo in, video out, real head pose/parallax feel without an explicit 3D asset.

**VASA-1 (2404.10667, Apr 2024, Microsoft Research Asia):** THE concept.
Single image + audio → 512x512 online video up to 40 FPS, negligible start latency.
Key design: (1) expressive DISENTANGLED face latent space (identity / appearance / head pose / facial dynamics factored apart); (2) diffusion model generates holistic facial dynamics + head motion IN that latent space from audio; (3) decoder renders frames from latents.
Microsoft will not release code/weights. The "3D-appearing" quality comes from 3D-aware latent/warping, not from a mesh.

**Open siblings (all need license + repo check before adoption):**
- **JoyVASA 2411.09209** (Nov 2024): explicit open VASA-style; decoupled facial representation + identity-independent diffusion motion generation; portraits AND animals; multilingual. Closest open reproduction of VASA's two-stage idea.
- **FLOAT 2412.01064** (Dec 2024, 47 upvotes): flow matching in a learned motion latent space, emotion-enhanced; fast sampling vs diffusion.
- IMTalker 2511.22167 (Nov 2025): implicit motion transfer + cross-attention; efficient.
- Teller 2503.18429 (Mar 2025): real-time STREAMING audio-driven portrait, autoregressive motion generation.
- **LivePortrait 2407.03168** (Jul 2024): implicit-keypoint warping renderer, very fast; the likely "renderer" half for a motion-latent generator. **FLAG: original release depends on InsightFace (non-commercial) — verify license before adopting.**
- ChatAnyone 2503.21144 (Mar 2025): real-time stylised portrait video chat, hierarchical motion diffusion (head+upper body), explicit hand control.
- Live Avatar / SoulX-FlashTalk 2512.04677 / 2512.23379 (Dec 2025, Alibaba): 14B real-time proof — the "can be done" existence proof but over budget.
- **SoulX-FlashHead 1.3B, LeapTalk 2608.00079, AvatarForcing 2603.14331**: 1.3B-class distilled video-diffusion students — the bake-off candidates.
- 2026 additions: AsymTalker 2605.02948, ExpPortrait 2602.19900, MegaAvatar 2609.39273 (Wan2.2-TI2V-5B), Avatar Forcing 2601.00664, InteractiveAvatar 2606.22905.

**KEY ARCHITECTURAL INSIGHT for cost — two families:**

| Family | Examples | GPU need | Quality |
|--------|----------|----------|---------|
| Motion-latent + light renderer | VASA/JoyVASA/FLOAT/LivePortrait | Far less GPU, high FPS | Can look flat on large head turns |
| Distilled video-diffusion students | FlashHead/LeapTalk/AvatarForcing | 1.3B–14B, heavier | Richer parallax/occlusion/hair |

Bake-off both families on the same photo. Hybrid option (latent motion for idle/listening cheaply; diffusion render only when speaking) is a design option, not a decision.

---

## H. "AI actress on CNN" reference (web search, not HF)

Tilly Norwood (AI "actress" by Xicoia/Particle 6) appeared on CNN Sep 8 2026, also CBS, NBC, Variety, THR, ITV.
She identified on-screen objects, described the reporter's clothing, spoke French.
BUT: could not recognise her creator at first; struggled to improvise a scene (dog-died prompt); clips needed heavy editing/prompt guidance.
=> Evidence of what polished, **produced** output looks like — NOT live end-to-end realism.

**Use as acceptance-test checklist:**
- Recognise a known person ✓
- Spontaneous improvisation without editing ✓
- Emotion on demand ✓
- Hold identity over a long session ✓
- No CSS-only "motion" ✓

No pipeline details available for Tilly. Do not claim a specific model stack.

---

## Takeaways for the build

1. Render slot candidates for L4 bake-off: SoulX-FlashHead-1.3B, LeapTalk (1-step), AvatarForcing (34 ms/frame). All 1.3B-class => single-GPU plausible; none verified on L4.
2. Listening is now first-class (UniLS, REALM, VideoFDB). Duplug alone only classifies state; a listener-motion generator is the missing node between Duplug state and the render node.
3. Evaluation: combine deterministic sync/FPS/first-frame metrics + JEV typed checks + MLLM rubric (VideoFDB/FlowPortrait style). Treat MLLM-only judging as weak (Artifact-Bench finding).
4. GPU relief levers: fewer-step distillation (Causal Forcing++), dynamic step allocation (DSA), speculative decoding for AR video, moving ASR/LLM/JEV off-GPU.
5. Verify before adopting: licenses for every repo/weights; LeapTalk 200 FPS claim; UniLS/REALM weights usability.
