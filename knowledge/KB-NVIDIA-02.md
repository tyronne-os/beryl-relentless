# KB-NVIDIA-02 — Prebuilt NVIDIA Digital-Human Workflows

Researched 2026-10-07 via search snippets (docs.nvidia.com, arxiv, forums blocked from sandbox) + GitHub + Hugging Face.
Items marked (V) were verified on a primary page; (S) = search snippet only.

## 1. Tokkio 5.0 — the reference "prebuilt" (S)
- Flow: browser (WebRTC via VST) -> ACE Controller (Pipecat orchestrator) -> Riva Parakeet ASR -> LLM/RAG (NIM or any agent) -> TTS -> Audio2Face-3D -> Animation Graph (+ gesture triggers) -> Unreal Renderer -> Pixel Streaming -> browser.
- Event-driven: message bus (Redis). Client connect/disconnect events start/stop per-session pipelines.
- SDR (Stream Distribution & Routing) assigns each session to a free pod of {ACE Controller, Animation Graph, Unreal Renderer}.
- Capacity: 1-6 concurrent users per deployment, set by GPU count.
- Minimum GPUs (reference workflow): 1 stream = 2x T4 / 2x L4 / 2x A10; 3 streams = 4x of those; 6 streams = 4x L4 or 4x A10. A single-stream 720p avatar can fit one L40-class GPU.
- Deploy: two machines (controller + application), Ubuntu 22.04, >=700 GB disk, scripted baremetal/CSP deploy; Helm/Kubernetes underneath.
- Tokkio 4.x differed (Omniverse renderer era); 5.0 moved to Unreal + Pipecat. Do not mix version docs.

## 2. NVIDIA AI Blueprint: Digital Humans for Customer Service (S)
- Tokkio + RAG backend. NIMs: nv-embedqa-e5-v5, nv-rerankqa-mistral4b-v3, Llama3-8b-instruct, Parakeet-ctc-1.1b ASR, FastPitch-HiFiGAN TTS (ElevenLabs also supported), Audio2Face-3D, Audio2Face-2D, other ACE services.
- Repo: github.com/NVIDIA-AI-Blueprints/digital-human (V; README is pointer only, Apache-2.0). Helm charts + reference code on build.nvidia.com.
- Lesson: LLM/RAG is a swappable slot behind a fixed avatar harness — same idea as Beryl's design.

## 3. ACE Controller + NVIDIA Pipecat (S)
- Pipecat-based frame-processor pipeline; NVIDIA adds processors for Riva ASR/TTS, Audio2Face, Foundational RAG; NvidiaLLMService wraps any NIM LLM.
- Also: Hugging Face Space nvidia/voice-agent-examples (S) — a runnable voice-agent example worth reading for turn-taking/barge-in wiring.
- Lesson: frames flowing through typed processors is the cleanest model for the "same harness, swap models" node contract.

## 4. [EXCLUDED] Audio2Face-3D — mesh/rig pipeline, out of scope.

## 5. Audio2Face-2D / Speech Live Portrait NIM (S)
- Photo + audio -> animated portrait. Input: 720p-4K portrait, 16 kHz mono PCM. Output 512x512 RGB. MFCC -> LSTM landmarks -> generative renderer. Controls: blink, head movement, gaze. Modes: performance / quality.
- Targets Ada/Ampere/Turing/Blackwell, excludes A100/H100. Reachable via NGC container (hosted Maxine endpoints 404'd).
- Good quality benchmark for the open FlashHead/LeapTalk path.

## 6. [EXCLUDED] Kairos / gaming-avatar sample — gaming + MetaHuman, out of scope.

## 7. Patterns to carry into Beryl's design
1. Controller owns the conversation; per-session routing to a free pod (SDR pattern).
2. Bus + health events between services.
3. Per-node GPU minimums published up front — Beryl's node.yaml cards should do the same, measured not assumed.
4. Swappable LLM/RAG slot; fixed avatar chain.
5. Blueprint ships Helm + reference code => "prebuilt" means a deployable spec, not a demo.

## Open verification items
- Re-fetch Tokkio 5.0 architecture + reference-workflow pages from a machine with docs.nvidia.com access.
- Confirm L4 Spot/on-demand prices from Google billing catalog.
- Confirm TypeSafe JEV route/schema from docs.typesafe.ai.
