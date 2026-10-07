# JEV design and standards

JEV is TypeSafe System One (`POST https://api.typesafe.ai/v1/systemone`, model `jev-latest`). Beryl uses it as a
typed judge at five points in the chain. It is early access, so the design assumes it can be slow, down or wrong.

Spec (single source of truth): `harness/jev/jev.yaml`. Code: `harness/jev/{face,voice,persona,director,verify}.py`
plus the shared `harness/jev/_contract.py`. Standards tests: `python3 tests/test_jev_standards.py` (offline, about 7 s).

## Principles

1. **Advisory, never load-bearing.** Every placement has a deterministic fallback. Nothing in `deploy/` or
   `harness/controller/` imports JEV, so repairs never depend on it (S12).
2. **All-or-nothing answers.** A JEV answer is used whole or not at all. A slot that is present but malformed (wrong
   type, out of range, not one of the question's choices, NaN) rejects the whole answer and the fallback result is used (S3).
3. **Bounded time.** Every call has a hard deadline of `timeout_s + 0.5 s`, enforced with `asyncio.wait_for`, so a
   hung or slow-dripping upstream cannot hold a turn (S4).
4. **Fail open for experience, closed for proof.** If a placement breaks even on its fallback path, face/voice/persona/director
   return a neutral result (persona = `continue`, so the avatar is never muted). VERIFY returns not-consistent / no-motion,
   never a green receipt.
5. **Deterministic first.** VERIFY's measured pixel/telemetry result is primary. JEV may only make it stricter
   (True to False, and only above `verify_override_confidence` 0.7), never greener (S8).
6. **One vocabulary.** The fallback can only emit values JEV can also emit, so downstream code sees one set (S10).
7. **Data minimisation.** Reply text and history are truncated before leaving the box; the API key travels only in the
   `Authorization` header (S11).

## Placements

| # | Placement | Inputs | Output | Consumer | Fallback rule (thresholds in `jev.yaml`) |
|---|-----------|--------|--------|----------|-----------------------------------------|
| 1 | FACE | user MediaPipe AUs, Beryl blendshapes | `user_emotion`, `beryl_emotion`, `match`, `confidence` | PERSONA | AU threshold rules; match 1.0 if same emotion else 0.3 |
| 2 | VOICE | user prosody, Beryl TTS prosody, reply text | `tone`, `contradicts_words`, `stress`, `confidence` | PERSONA | energy/rate rules; stress rises with loudness (dBFS floor -45) |
| 3 | PERSONA | face, voice, last 6 turns, persona card, reply | `in_character`, `tone_ok`, `action` continue/soften/yield | gates the LLM reply | yield if contradicts > 0.8; soften if match < 0.3 and stress > 0.7 |
| 4 | DIRECTOR | persona decision, performance plan, reply | `gaze`, `blink_rate`, `nod`, `micro_expression`, `intensity` | MOTION conditioning | emotion to gaze/micro tables; intensity 0.6 continue else 0.3 |
| 5 | VERIFY | telemetry, painted-pixel stats | `consistent`, `is_motion_visible`, `agrees`, stages | sensory gland / scorecard | changed pixels > 100 = real motion; stated L1/L2 with none = CSS-only, stage L0 |

Result fields carry `source`: `jev`, `jev+deterministic` (VERIFY) or `fallback`, so the scorecard shows which path produced it.

Lip-sync is not a JEV call. It is measured on the rendered mp4 (`harness/nodes/verify/lipsync.py`) and feeds
`lipsync_offset_ms` into the VERIFY scorecard; acceptance is `lipsync.lipsync_ok` (|offset| <= 133 ms).

## Request and answer shape (as implemented)

Request: `{model, state: {...placement inputs...}, questions: {key: {type, question, choices|scale}}}` with
`Authorization: Bearer <key>`. Answer: `{answers: {key: {choice | score | yes_prob, confidence}}}`.
`choice` must be one of the sent choices; `score` is 0..2 (three scale anchors); `yes_prob` and `confidence` are in [0,1].

## Where JEV sits in a turn (`src/server/avatar_chain.py`)

`ASR -> LLM -> [FACE || VOICE] -> PERSONA (gate) -> DIRECTOR -> TTS -> MOTION`. The three JEV stages are serial and
sit between the LLM reply and the first audio.

| Condition | Added to time-to-first-audio |
|-----------|------------------------------|
| No key or `CRANE_JEV=false` | about 0 (pure Python) |
| JEV healthy | 3 serial round trips (not yet measured) |
| JEV hung | up to (2.5+0.5) + (2.5+0.5) + (2.0+0.5) = 8.5 s worst case |

## Known gaps (be honest before a demo)

- **Never run against the real API.** The tests use a fake upstream shaped like the assumed answer format. The
  `noul` question type and the answer field names are unverified against docs.typesafe.ai. If the docs differ, change
  `jev.yaml` (`question_types`, question `type`) and the three parse helpers in `_contract.py`.
- **VOICE is fed placeholder prosody.** `avatar_chain.py` passes constants (150 Hz, -20 dB, 140 wpm), not measured user prosody.
- **Serial JEV before TTS** adds latency whenever JEV is live. Options: lower `timeout_s`, run DIRECTOR concurrently with
  TTS start, or let PERSONA run after the first audio chunk with default `continue`.
- **Thresholds are first guesses**, set from reasoning, not from recorded sessions. Calibrate from real turns.
- `jev.yaml` is a registry, not a node contract, so it is larger than the ~15-field node YAMLs. It is still data only,
  loaded with `yaml.safe_load`, with no expressions.

## Standards (tests in `tests/test_jev_standards.py`)

| # | Standard |
|---|----------|
| S1 | A well-formed JEV answer yields a schema-valid result tagged `jev` |
| S2 | No key, or `CRANE_JEV=false`: deterministic result and zero network calls |
| S3 | 5xx, refused, hung, non-JSON, wrong shape, out-of-enum/range/type, NaN: valid output, never raises |
| S4 | A hung upstream cannot hold a turn beyond timeout + 1 s; result marked fallback |
| S5 | Fallbacks are deterministic (same input, same output) |
| S6 | Null or garbage values from the browser still give a valid result |
| S7 | Persona gate semantics hold, and JEV cannot invent an action |
| S8 | VERIFY: JEV can only tighten, CSS-only motion never passes, an internal error fails closed |
| S9 | Voice stress fallback rises with loudness and is not saturated for normal speech |
| S10 | Fallback and JEV share one vocabulary |
| S11 | Reply text and history are bounded; the key is only in the `Authorization` header, never body or logs |
| S12 | Nothing in `deploy/` or `harness/controller/` imports JEV or calls TypeSafe |
| S13 | `jev.yaml` is the single source: request questions equal the spec, neutral results are schema-valid, no vocabulary literals in modules |

Changing a vocabulary, threshold, limit or question: edit `jev.yaml`, run the suite. Adding a placement: add its
sections to `jev.yaml` (the loader rejects a spec missing a placement), a module using `_contract`, and a row in `CALLS`
in the test file.
