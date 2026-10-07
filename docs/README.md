# docs index

- [RUNBOOK.md](RUNBOOK.md) — operator steps: laptop setup, GCP, preflight / gpu_on / gpu_off, ports, cost, scorecard.
- [LESSONS-LEARNED.md](LESSONS-LEARNED.md) — every failure so far: symptom, cause, fix, rule.
- [JEV-DESIGN.md](JEV-DESIGN.md) — JEV placements, contract, failure semantics, standards S1-S13 (spec: `harness/jev/jev.yaml`).
- [SELF-TEST-DESIGN.md](SELF-TEST-DESIGN.md) — Claude judges Beryl in three stages (voice, face, cluster): gates, judge discipline, calibration, ledger, increments.
- [SCALING-AND-TRAINING-DATA.md](SCALING-AND-TRAINING-DATA.md) — plan (not done): scaling and data capture, consent.

Repo layout:
- `CLAUDE.md`, `HANDOFF.md` — boot context and build notes; `knowledge/` — research notes
- `deploy/` — preflight, gpu_on, gpu_off, prep_fixture, setup_gpu_node, render_service
- `harness/` — nodes (asr, listen, motion, render, verify...), `jev/`, `controller/`, `pipelines.yaml`
- `bakeoff/` — scorecard runner, `fixtures/` and `results/` (private, gitignored)
- `src/server/avatar_chain.py` — chat chain; `.env.example` — variable names only
