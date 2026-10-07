"""
Session controller — owns the pipeline, health bus, and stage-ladder FSM.
Pipecat-style: each node is a frame processor; frames flow through the chain.
Reference: github.com/pipecat-ai/pipecat
"""
import asyncio
import json
import logging
import time
import yaml
from pathlib import Path
from typing import Any

log = logging.getLogger("controller")

NODES_DIR = Path(__file__).parent.parent / "nodes"
PIPELINES_FILE = Path(__file__).parent.parent / "pipelines.yaml"


def load_pipeline(pipeline_id: str) -> dict:
    pipelines = yaml.safe_load(PIPELINES_FILE.read_text())
    if pipeline_id not in pipelines["pipelines"]:
        raise ValueError(f"Unknown pipeline: {pipeline_id}")
    return pipelines["pipelines"][pipeline_id]


def load_node(node_id: str) -> dict:
    node_file = NODES_DIR / node_id / "node.yaml"
    if not node_file.exists():
        raise FileNotFoundError(f"No node.yaml for: {node_id}")
    return yaml.safe_load(node_file.read_text())


class HealthBus:
    """Broadcasts node health events to all connected WebSocket clients."""

    def __init__(self):
        self._subscribers: list[Any] = []
        self._node_states: dict[str, dict] = {}

    def subscribe(self, ws) -> None:
        self._subscribers.append(ws)

    def unsubscribe(self, ws) -> None:
        self._subscribers = [s for s in self._subscribers if s is not ws]

    async def emit(self, node_id: str, status: str, detail: str = "") -> None:
        event = {
            "type": "node_health",
            "node": node_id,
            "status": status,
            "detail": detail,
            "ts": time.time(),
        }
        self._node_states[node_id] = event
        payload = json.dumps(event)
        dead = []
        for ws in self._subscribers:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unsubscribe(ws)

    def snapshot(self) -> dict:
        return dict(self._node_states)


class StageLadder:
    """
    FSM: L0 (idle) → L1 (CPU+API) → L2 (GPU cinematic).
    Degrades on node failure; upgrades when GPU warm probe passes.
    """

    STAGES = ["L0", "L1", "L2"]

    def __init__(self, bus: HealthBus):
        self._stage = "L0"
        self._bus = bus

    @property
    def stage(self) -> str:
        return self._stage

    async def upgrade(self, target: str) -> bool:
        if self.STAGES.index(target) <= self.STAGES.index(self._stage):
            return False
        prev = self._stage
        self._stage = target
        await self._bus.emit("controller", "stage_change", f"{prev}→{target}")
        log.info("Stage %s → %s", prev, target)
        return True

    async def degrade(self, reason: str) -> None:
        idx = self.STAGES.index(self._stage)
        if idx == 0:
            return
        prev = self._stage
        self._stage = self.STAGES[idx - 1]
        await self._bus.emit("controller", "stage_degrade", f"{prev}→{self._stage}: {reason}")
        log.warning("Stage degrade %s → %s: %s", prev, self._stage, reason)


class NodeFailureTracker:
    """
    Three-strikes rule: 3rd distinct error on a node → stop patching,
    swap to known-good reference (vendor fallback).
    JEV is excluded from repair decisions.
    """

    def __init__(self, max_failures: int = 3):
        self._max = max_failures
        self._errors: dict[str, set] = {}

    def record(self, node_id: str, error_signature: str) -> bool:
        """Returns True if the node has hit max distinct failures."""
        if node_id not in self._errors:
            self._errors[node_id] = set()
        self._errors[node_id].add(error_signature)
        return len(self._errors[node_id]) >= self._max

    def reset(self, node_id: str) -> None:
        self._errors.pop(node_id, None)

    def failure_count(self, node_id: str) -> int:
        return len(self._errors.get(node_id, set()))


# Module-level singletons — imported by adapter.py files and WebSocket handlers
bus = HealthBus()
stage = StageLadder(bus)
failures = NodeFailureTracker()
