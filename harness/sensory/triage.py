"""
Triage panel — aggregate per-instrument lights into a session-level report.

Light levels: gold > green > amber > orange > red
              (5)    (4)     (3)      (2)     (1)

Rules:
- Each instrument contributes one light.
- Session triage = worst individual light (never averages).
- JEV can only LOWER an instrument's light, never raise it.
- "amber" is the floor for any instrument that cannot measure (not-measured).

Usage:
    from harness.sensory.triage import Triage
    t = Triage()
    t.update_eye(eye_result)
    t.update_mouth(mouth_result)
    print(t.report())
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from harness.sensory.standards import triage_index, worst, TRIAGE_LEVELS

_LIGHT_EMOJI = {
    "gold": "🏆",
    "green": "🟢",
    "amber": "🟡",
    "orange": "🟠",
    "red": "🔴",
}


@dataclass
class InstrumentLight:
    name: str
    light: str = "amber"
    note: str = ""
    source: str = "not-measured"


@dataclass
class Triage:
    eye: InstrumentLight = field(default_factory=lambda: InstrumentLight("eye"))
    ear: InstrumentLight = field(default_factory=lambda: InstrumentLight("ear"))
    mouth: InstrumentLight = field(default_factory=lambda: InstrumentLight("mouth"))
    identity: InstrumentLight = field(default_factory=lambda: InstrumentLight("identity"))

    def update_eye(self, result: Any) -> None:
        self.eye = InstrumentLight(
            name="eye",
            light=result.triage,
            note=result.note,
            source=getattr(result, "source", "?"),
        )

    def update_ear(self, result: Any) -> None:
        self.ear = InstrumentLight(
            name="ear",
            light=result.triage,
            note=result.note,
            source=getattr(result, "source", "?"),
        )

    def update_mouth(self, result: Any) -> None:
        self.mouth = InstrumentLight(
            name="mouth",
            light=result.triage,
            note=result.note,
            source=getattr(result, "approach", "?"),
        )

    def update_identity(self, result: Any) -> None:
        self.identity = InstrumentLight(
            name="identity",
            light=result.triage,
            note=result.note,
            source=getattr(result, "source", "?"),
        )

    def jev_lower(self, instrument: str, level: str) -> None:
        """JEV can only lower a light, never raise it. Brand protection."""
        current = getattr(self, instrument, None)
        if current is None:
            return
        if triage_index(level) < triage_index(current.light):
            current.light = level
            current.note += f" [JEV→{level}]"

    def session_triage(self) -> str:
        instruments = [self.eye, self.ear, self.mouth, self.identity]
        return worst(*(i.light for i in instruments))

    def report(self) -> dict:
        instruments = [self.eye, self.ear, self.mouth, self.identity]
        return {
            "session": self.session_triage(),
            "instruments": {
                i.name: {
                    "light": i.light,
                    "emoji": _LIGHT_EMOJI.get(i.light, "?"),
                    "note": i.note,
                    "source": i.source,
                }
                for i in instruments
            },
        }

    def summary_line(self) -> str:
        r = self.report()
        parts = []
        for name, data in r["instruments"].items():
            parts.append(f"{data['emoji']} {name}: {data['note'][:60]}")
        session = r["session"]
        return f"session={_LIGHT_EMOJI.get(session,'?')}{session}  |  " + "  ".join(parts)


def measure_clip(path: str) -> Triage:
    """
    Run all four instruments on a single rendered mp4 and return a Triage panel.
    Each instrument fails independently; the panel still returns even if some can't measure.
    """
    from harness.sensory import eye as _eye
    from harness.sensory import ear as _ear
    from harness.sensory import mouth as _mouth
    from harness.sensory import identity as _ident

    t = Triage()

    try:
        t.update_eye(_eye.measure_file(path))
    except Exception as exc:
        t.eye = InstrumentLight("eye", "amber", f"exception: {exc!r}", "error")

    try:
        t.update_ear(_ear.measure_file(path))
    except Exception as exc:
        t.ear = InstrumentLight("ear", "amber", f"exception: {exc!r}", "error")

    try:
        t.update_mouth(_mouth.measure_file(path))
    except Exception as exc:
        t.mouth = InstrumentLight("mouth", "amber", f"exception: {exc!r}", "error")

    try:
        t.update_identity(_ident.measure_file(path))
    except Exception as exc:
        t.identity = InstrumentLight("identity", "amber", f"exception: {exc!r}", "error")

    return t
