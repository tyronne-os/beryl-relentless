"""Loads and validates selftest.yaml. A malformed spec fails at import, not mid-run."""
from pathlib import Path

import yaml

STAGES = ("voice", "face", "cluster")


def _validate(spec: dict) -> dict:
    lo, hi = spec["scale"]["min"], spec["scale"]["max"]
    for name in STAGES:
        st = spec["stages"][name]
        for key in ("title", "what_judge_sees", "gates", "criteria", "knobs"):
            if key not in st:
                raise ValueError(f"selftest.yaml: stage {name!r} missing {key!r}")
        for cid, c in st["criteria"].items():
            if not isinstance(c.get("weight"), int) or c["weight"] < 1:
                raise ValueError(f"selftest.yaml: {name}.{cid} needs an integer weight >= 1")
            if not c.get("question"):
                raise ValueError(f"selftest.yaml: {name}.{cid} needs a question")
            if set(c["anchors"]) != {1, 3, 5} and set(c["anchors"]) != {lo, 3, hi}:
                raise ValueError(f"selftest.yaml: {name}.{cid} anchors must be exactly 1, 3 and 5")
            if cid not in st["knobs"]:
                raise ValueError(f"selftest.yaml: {name}.{cid} has no knob (every criterion must route to a fix)")
        extra = set(st["knobs"]) - set(st["criteria"])
        if extra:
            raise ValueError(f"selftest.yaml: {name} knobs for unknown criteria {sorted(extra)}")
    for name, d in spec["calibration"]["face"]["degradations"].items():
        bad = [t for t in d["targets"] if t not in spec["stages"]["face"]["criteria"]]
        if bad:
            raise ValueError(f"selftest.yaml: calibration degradation {name} targets unknown criteria {bad}")
    if spec["pass"]["criterion_min"] > spec["pass"]["mean_min"]:
        raise ValueError("selftest.yaml: pass.criterion_min cannot exceed pass.mean_min")
    return spec


SPEC = _validate(yaml.safe_load(Path(__file__).with_name("selftest.yaml").read_text()))


def criteria(stage: str) -> dict:
    return SPEC["stages"][stage]["criteria"]


def rubric_text(stage: str) -> str:
    lines = []
    for cid, c in criteria(stage).items():
        tag = " (judge from the measured numbers; you cannot hear audio)" if c.get("evidence") == "metrics" else ""
        lines.append(f"- {cid}{tag}: {c['question']}")
        for score in sorted(c["anchors"]):
            lines.append(f"    {score} = {c['anchors'][score]}")
    return "\n".join(lines)


def verdict_schema(stage: str) -> dict:
    ids = list(criteria(stage))
    return {
        "type": "object",
        "properties": {
            "criteria": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": ids},
                        "assessable": {"type": "boolean"},
                        "score": {"type": "integer"},
                        "evidence": {"type": "string"},
                        "issue": {"type": "string"},
                    },
                    "required": ["id", "assessable", "score", "evidence", "issue"],
                    "additionalProperties": False,
                },
            },
            "fixes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"criterion": {"type": "string", "enum": ids}, "change": {"type": "string"}},
                    "required": ["criterion", "change"],
                    "additionalProperties": False,
                },
            },
            "summary": {"type": "string"},
        },
        "required": ["criteria", "fixes", "summary"],
        "additionalProperties": False,
    }
