"""Durable evidence of useful work, independent of tool-call identity."""
from __future__ import annotations

import hashlib
import json
from .diagnostic_progress import stable_value, diagnostic_fingerprint


class ProgressState:
    def __init__(self, saved=None):
        saved = saved or {}
        self.fingerprints = set(saved.get("fingerprints", ()))
        self.stalled_turns = int(saved.get("stalled_turns", 0))
        self.last_progress_turn = int(saved.get("last_progress_turn", 0))

    def observe(self, observation):
        if observation.get("tool") in {"update_plan", "agent_progress_hint", "spawn_subagent"}:
            return False
        # IDs, timestamps and durations never make identical information new.
        value = stable_value({key: observation.get(key) for key in ("tool", "status", "output", "error")})
        if value.get("tool") == "build_firmware" and isinstance(value.get("output"), dict):
            output = value["output"]
            value["output"] = {"success": output.get("success"),
                               "workspace_revision": output.get("workspace_revision"),
                               "diagnostic": diagnostic_fingerprint(output)}
        digest = hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
        if digest in self.fingerprints:
            return False
        self.fingerprints.add(digest)
        return True

    def settle(self, turn, changed):
        self.stalled_turns = 0 if changed else self.stalled_turns + 1
        if changed:
            self.last_progress_turn = turn

    def snapshot(self):
        return {"fingerprints": sorted(self.fingerprints), "stalled_turns": self.stalled_turns,
                "last_progress_turn": self.last_progress_turn}


def continuation_summary(task, plan, observations, turn):
    return {"objective": str(task)[:4000], "turn": turn,
            "milestones": list(plan),
            "recent_evidence": [dict(item) for item in observations[-6:]],
            "next_action": "Continue the first unfinished milestone using the saved draft and evidence. Do not repeat completed work."}
