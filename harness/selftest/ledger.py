"""
Run history, deltas and the ratchet. The ledger is local (gitignored); the baseline is committed and only
moves forward when a stage passes with a calibrated judge and a human says so (--update-baseline --i-reviewed).
"""
import json
import subprocess
import time
from pathlib import Path

from harness.selftest.spec import SPEC

ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER = ROOT / "bakeoff" / "results" / "selftest_ledger.jsonl"
BASELINE = ROOT / "bakeoff" / "selftest_baseline.json"


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def load(path: Path = LEDGER) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(entry: dict, path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def record(result, change_note: str = "", calibrated: bool | None = None, path: Path = LEDGER) -> dict:
    j = result.judge
    return _append({
        "type": "stage", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "git": _git_sha(),
        "stage": result.stage, "status": result.status, "blocking": result.blocking,
        "gates": {g.id: {"passed": g.passed, "value": g.value} for g in result.gates},
        "scores": j.scores if j else {}, "mean": j.mean if j else None, "agreement": j.agreement if j else None,
        "judge_model": j.model if j else None, "usage": j.usage if j else None,
        "rubric_version": SPEC["rubric_version"], "fixture_set_version": SPEC["fixture_set_version"],
        "calibrated": calibrated, "change_note": change_note, "fixes_top": (j.fixes[:3] if j else []),
    }, path)


def record_calibration(stage: str, outcome: dict, path: Path = LEDGER) -> dict:
    return _append({"type": "calibration", "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "git": _git_sha(),
                    "stage": stage, "judge_model": SPEC["judge"]["model"], "rubric_version": SPEC["rubric_version"],
                    "passed": outcome["passed"], "margins": outcome["margins"]}, path)


def is_calibrated(stage: str, entries: list[dict]) -> bool:
    last = [e for e in entries if e.get("type") == "calibration" and e["stage"] == stage
            and e["judge_model"] == SPEC["judge"]["model"] and e["rubric_version"] == SPEC["rubric_version"]]
    return bool(last) and last[-1]["passed"]


def previous(stage: str, entries: list[dict]) -> dict | None:
    same = [e for e in entries if e.get("type") == "stage" and e["stage"] == stage
            and e["rubric_version"] == SPEC["rubric_version"] and e["fixture_set_version"] == SPEC["fixture_set_version"]]
    return same[-1] if same else None


def deltas(prev: dict | None, cur: dict) -> dict:
    if not prev:
        return {}
    d = {c: round(cur["scores"][c] - prev["scores"][c], 2) for c in cur["scores"] if c in prev["scores"]}
    flips = {g: (prev["gates"][g]["passed"], v["passed"]) for g, v in cur["gates"].items()
             if g in prev["gates"] and prev["gates"][g]["passed"] != v["passed"]}
    return {"scores": d, "gate_flips": flips, "from_git": prev["git"], "from_note": prev.get("change_note", "")}


def load_baseline(path: Path = BASELINE) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def ratchet(entry: dict, baseline: dict, tolerance: float | None = None) -> list[str]:
    tol = SPEC["ratchet"]["tolerance"] if tolerance is None else tolerance
    base = baseline.get(entry["stage"])
    if not base:
        return []
    if base["rubric_version"] != entry["rubric_version"] or base["fixture_set_version"] != entry["fixture_set_version"]:
        return [f"baseline is for rubric v{base['rubric_version']} / fixtures v{base['fixture_set_version']}; re-baseline after review"]
    out = [f"{c}: {entry['scores'][c]} is more than {tol} below baseline {s}" for c, s in base["scores"].items()
           if c in entry["scores"] and entry["scores"][c] < s - tol]
    out += [f"gate {g} was passing in the baseline and now is not" for g in base["gates_passing"]
            if entry["gates"].get(g, {}).get("passed") is not True]
    return out


def update_baseline(entry: dict, reviewed: bool, path: Path = BASELINE) -> dict:
    if not reviewed:
        raise PermissionError("baseline moves only after a human review: pass --i-reviewed")
    if entry["status"] != "pass":
        raise ValueError(f"stage {entry['stage']} did not pass ({entry['status']}); baseline unchanged")
    if not entry.get("calibrated"):
        raise ValueError("judge is not calibrated for this model and rubric; baseline unchanged")
    data = load_baseline(path)
    data[entry["stage"]] = {"rubric_version": entry["rubric_version"], "fixture_set_version": entry["fixture_set_version"],
                            "scores": entry["scores"], "git": entry["git"],
                            "gates_passing": [g for g, v in entry["gates"].items() if v["passed"] is True]}
    path.write_text(json.dumps(data, indent=2) + "\n")
    return data
