#!/usr/bin/env bash
# predeploy.sh — static gate. No network, no GPU, no SSH. Every check here is a past failure
# from docs/LESSONS-LEARNED.md turned into a test. Exit 1 = do not spend GPU time.
set -uo pipefail
cd "$(dirname "$0")/.."
FAILS=0
ok()   { printf '  [ OK ] %s\n' "$1"; }
bad()  { printf '  [FAIL] %s\n         -> %s\n' "$1" "$2"; FAILS=$((FAILS+1)); }
chk()  { if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1" "$3"; fi; }

echo "== SYNTAX =="
for f in deploy/*.sh; do chk "bash -n $f" "bash -n $f" "fix the shell syntax error"; done
for f in deploy/*.py bakeoff/*.py harness/nodes/*/adapter.py harness/jev/*.py; do
    [[ -f "$f" ]] && chk "python parse $f" "python3 -c 'import ast,sys;ast.parse(open(sys.argv[1]).read())' $f" "fix the Python syntax error"
done
if command -v shellcheck >/dev/null; then
    chk "shellcheck (errors only)" "shellcheck -S error deploy/*.sh" "run: shellcheck -S error deploy/*.sh"
fi

echo "== LESSONS AS TESTS =="
S=deploy/setup_gpu_node.sh
chk "L13 systemd unit sets WorkingDirectory to the FlashHead repo" "grep -q '^WorkingDirectory=\$FH' $S" "FlashHead opens configs relative to CWD"
chk "L13 import check runs from repo root" "grep -q 'cd \"\$FH\" &&' $S" "wrap the import check in (cd \"\$FH\" && ...)"
chk "L16 model venv pins huggingface_hub<1.0" "grep -q 'huggingface_hub<1.0' $S" "transformers 4.57.3 needs hub<1.0"
chk "L16 weights use a separate hub-2.x venv" "grep -q 'venv-hf' $S" "keep tooling venv apart from model venv"
chk "L16 no multi-value --include (hub 2.x needs one flag per pattern)" "! grep -E -- '--include \"[^\"]*\" \"' $S" "repeat --include per pattern"
chk "L12 nccl pin is filtered out of upstream requirements" "grep -q 'nvidia-nccl' $S" "drop the nvidia-nccl pin"
chk "L15 separate .built and .ready markers" "grep -q '\.built' $S && grep -q '\.ready' $S" "independent markers per step"
chk "L6 no mv in gpu_on.sh (same-file mv under set -e)" "! grep -E '^\s*(sudo )?mv ' deploy/gpu_on.sh" "use scp to /tmp then cp"
chk "L4 gcloud only via gc() wrapper" "! grep -En '^\\s*(\\$\\(|sudo )?gcloud ' deploy/gpu_on.sh deploy/gpu_off.sh deploy/preflight.sh" "call gc, never bare gcloud"
chk "L14 preflight checks the repo/branch" "grep -q '== REPO ==' deploy/preflight.sh" "restore the REPO check"
chk "L9 render service announces passthrough fallback" "grep -q 'passthrough' deploy/render_service.py && grep -q 'load_error' deploy/render_service.py" "fallbacks must announce themselves"
chk "L10 scorecard has no hard-coded metrics" "! grep -E '\"lipsync_offset_ms\": *[0-9]|identity_drift\": *0\.[0-9]' bakeoff/scorecard_runner.py" "unmeasured must stay None"

echo "== LOCAL PYTHON (the interpreter gpu_on.sh will use) =="
PY=python3; [[ -x .venv/bin/python ]] && PY=.venv/bin/python
chk "bakeoff deps importable with $PY (httpx, PIL)" "$PY -c 'import httpx, PIL'" "run: sudo apt install -y python3-venv && python3 -m venv .venv && .venv/bin/pip install httpx pillow"

echo "== SECRETS =="
PAT='(hf_[A-Za-z0-9]{30,}|nvapi-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|"private_key":)'
chk "no secrets in tracked files" "! git ls-files -z | xargs -0 grep -IEl '$PAT'" "remove it, rotate it, rewrite history if pushed"
chk ".env is gitignored" "git check-ignore -q .env" "add .env to .gitignore"

echo
if (( FAILS > 0 )); then echo "PREDEPLOY: $FAILS FAIL — do not fire."; exit 1; fi
echo "PREDEPLOY: GO"
