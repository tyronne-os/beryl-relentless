# Self-test: Claude judges Beryl in three stages

Idea (yours): Claude tests Beryl in three stages so we fix and improve in increments.
1. **Voice** - the vernacular quality of what Beryl says and how it is delivered.
2. **Face** - lip-sync and facial expression.
3. **Cluster** - how the back end reacts as a system.

Code: `harness/selftest/`. Spec (rubrics, gates, fixtures, thresholds): `harness/selftest/selftest.yaml`.
Tests: `python3 tests/test_selftest.py` (offline, about 40 s). Run: `python -m harness.selftest.run --stage voice`.

## What I added to your idea, and why

| Addition | Why |
|----------|-----|
| **Gates first, judge second.** Every stage has measured pass/fail gates. Claude can add a "no", never turn a failed or unmeasured gate into a "yes". | A judge that can overrule measurements will eventually be talked into green. Same rule as JEV VERIFY. |
| **Claude only judges what numbers cannot.** Wording naturalness, expression, uncanny features, graceful degradation. | Cheaper, more repeatable, and every score has evidence behind it. |
| **The judge is tested before it is trusted (calibration).** Known-good vs known-bad evidence; Claude must separate them by a margin. Bad clips are made automatically from a good clip with ffmpeg (audio delayed 400 ms, video frozen, heavy blur). | "Claude tests himself" has a self-preference risk. Calibration is the check that the judge is not just agreeable. Uncalibrated scores are labelled advisory and cannot move the baseline. |
| **Three independent judgements, median, agreement check.** Low agreement = inconclusive, never a quiet pass. | One LLM score is noisy. |
| **"Not assessable" is allowed.** | A judge forced to score everything will invent scores. |
| **Evidence is fenced as untrusted data.** | LLM replies and ASR text can contain instructions aimed at the judge. |
| **Blind judging.** The judge never sees earlier scores, run history, or what the builders intended. | Prevents anchoring and leniency toward our own work. |
| **Ranked fix list routed to a node and knob.** Every criterion maps to the one place to change (e.g. `delivery_dynamics` -> TTS voice choice). | This is what makes it incremental: each run ends with "change this next". |
| **One change per run, ledger, deltas.** `--change-note "..."` records the single change; the next run prints what moved. | You learn whether a change helped instead of guessing. |
| **Ratchet baseline.** A criterion may not fall more than 0.5 below the committed baseline, and a gate that used to pass may not stop passing. The baseline moves only with `--update-baseline --i-reviewed`, a passing stage and a calibrated judge. | Gains are locked in. |
| **Stop at the first failing stage.** | Fix the lowest layer first; a bad voice makes the face stage meaningless to tune. |

## What Claude can and cannot judge (read this before trusting a score)

- **Claude cannot hear audio.** Stage 1 scores the reply text (wording, register, speakability) and judges delivery
  from measured numbers (pace, pauses, energy range, pitch spread, ASR round-trip error). "Does it sound human" still
  needs a person listening. That is increment 4.
- **Claude sees stills, not motion.** Stage 2 gets a labelled contact sheet and a strip of consecutive frames at the loudest
  syllable. Blink timing, gaze drift and micro-motion are not judged; frozen-frame and painted-pixel gates cover the worst cases.
- **Stage 3 reads text and numbers**, an incident-style report built from health, timings and fallback markers.
- **Third-party exposure.** Reply text, ASR text, frames and the reference photo go to the Anthropic API. Use only
  fixture content and the test photo, never a real user's face or voice (see `docs/SCALING-AND-TRAINING-DATA.md`).
- **Self-preference.** The judge is Claude and the builder is Claude. Calibration, blind prompts and gates reduce this; they do
  not remove it. Your own listening and viewing is the final check (increment 4).

## Stages

### 1. Voice (`stages.run_voice`)
Evidence: 3 scripted lines (TTS only, isolates the voice) and 6 LLM turns (LLM then TTS), each synthesised, measured and
sent through ASR to get word error rate.
Gates: ASR WER <= 0.10, clipping <= 0.1%, 110-200 wpm, lead silence <= 0.6 s, duration >= 0.5 s, every item synthesised.
Judge criteria: conversational_register, persona_fit, speakability, responsiveness, delivery_pace_and_pauses, delivery_dynamics.
Fix routing: wording criteria -> LLM persona prompt; pace -> Kokoro speed; dynamics -> voice choice (the voice studio).

### 2. Face (`stages.run_face`)
Evidence: a rendered clip per fixture; measured lip-sync offset and correlation (`harness/nodes/verify/lipsync.py`),
frozen-frame run, fps, first-chunk time, painted-pixel change; images: reference photo, 8-frame contact sheet, 6-frame strip.
Gates: |lip-sync| <= 133 ms, correlation >= 0.45, fps >= 24, frozen run <= 4 frames, painted motion, first chunk <= 500 ms.
Judge criteria: lip_sync_perceived, expression_match, facial_naturalness, artifacts, identity_hold, head_pose_plausibility.
Known: first chunk is about 680 ms today, so this stage fails on that gate until the render budget or model changes (a decision for the bake-off, not a bug).
Known: "expression on demand" is not testable yet because render and director do not take a requested emotion end to end; the spec marks it disabled.

### 3. Cluster (`stages.run_cluster`)
Evidence: node health, per-node timings and per-turn totals recorded during stages 1-2 (`Recorder`), fallback markers,
GPU headroom, and fault-injection outcomes when enabled.
Gates: required nodes healthy, render model is not passthrough, no render load error, turn p95 <= 4 s,
zero unannounced fallbacks (a silent passthrough or a swallowed empty result), GPU free >= 2 GB.
Judge criteria: graceful_degradation, failure_visibility, latency_budget, stage_ladder_behavior, resource_headroom, recovery.
Fault injection (kill TTS, block JEV, stop render, stop the GPU VM) is specified in the YAML and **off**; it stops real services
on the live node, so it needs your explicit go-ahead. With it off, `recovery` is reported not assessable, not guessed.

## How the judge is called
`harness/selftest/judge.py`. Model from `selftest.yaml` (`claude-opus-5-5`; switch to `claude-sonnet-5-5` to cut cost,
then re-calibrate). Structured outputs (`output_config.format` JSON schema) with `effort: medium`; no sampling parameters,
no forced tool use (rejected by this model). Each repeat is validated: unknown or missing criterion, score outside 1-5,
non-JSON, or a refusal discards that repeat. Fewer than two valid repeats = inconclusive. If the API is unreachable the stage
degrades to **provisional** (gates only) rather than failing silently. Results are cached by content hash (evidence, images,
model, rubric version), so re-running unchanged evidence costs nothing.

## The loop
```
change ONE thing  ->  run a stage with --change-note "what changed"
  -> gates (measured)  ->  judge (3 repeats)  ->  verdict + ranked fix list + delta vs last run + ratchet check
  -> fix the top item  ->  repeat; when it passes and you have reviewed it: --update-baseline --i-reviewed
```
Stages run in order and stop at the first that does not pass (`--keep-going` overrides).

## Increments (each one waits for your go-ahead)
| # | Increment | Needs |
|---|-----------|-------|
| 0 | **Done:** framework, spec, judge, calibration, ledger/ratchet, tests (this change). Nothing run live. | - |
| 1 | Stage 1 live on the node (Kokoro + ASR + NIM LLM); calibrate the voice judge; compare the 7 voices from the voice studio. | `ANTHROPIC_API_KEY`, node up, SSH tunnels (about GPU hours for ASR/TTS only if CPU is not enough) |
| 2 | Stage 2 live on the real clip; calibrate the face judge from `clip_warm.mp4`; first real lip-sync number. | the clip, `reference.jpg` on the laptop |
| 3 | Stage 3 passive (health + recorded timings), then fault injection scenario by scenario. | your approval for each scenario that stops a service |
| 4 | Human review sheet: you rate the same items; we measure judge-vs-human agreement and fix the rubric where they differ. This is the real check on "sounds and looks human". | your time |
| 5 | Wire the ledger into the bake-off so FlashHead, LeapTalk and AvatarForcing are scored by the same stages. | bake-off slots |

## What is verified and what is not
Verified here (offline): spec validity; judge request shape, schema, discard rules, aggregation, caching, injection fencing;
audio metrics and WER on synthetic audio; stage verdict logic; lip-sync and frozen-frame gates on synthetic H.264 clips;
degraded-clip generator (audio shift, freeze, blur) measurably degrades; ledger, ratchet, baseline rules; calibration logic.
**Not verified:** any call to the real Claude API from this code; `LiveServices` against the real cluster; the LLM reply path
(it uses a stand-in persona prompt that must mirror the production `chat.py` prompt); thresholds (first guesses, calibrate on real runs);
whether the judge's scores track human perception at all (increment 4).

## Setup on the laptop
```bash
.venv/bin/pip install anthropic pillow numpy imageio-ffmpeg
# ANTHROPIC_API_KEY goes in your local env file / Secret Manager like the other keys (the name is listed in CLAUDE.md)
.venv/bin/python -m harness.selftest.run --stage voice --no-judge          # gates only, free
.venv/bin/python -m harness.selftest.run --stage voice --calibrate         # does the judge separate good from bad?
.venv/bin/python -m harness.selftest.run --stage voice --change-note "baseline"
```
