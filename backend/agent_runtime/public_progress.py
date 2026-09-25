"""Public progress text and evidence-backed plan state, never model scratchpads."""

from __future__ import annotations

import re
import uuid
from typing import Mapping


def public_text(text: str, limit: int = 16000) -> str:
    text = re.sub(
        r"<(think|thinking|analysis|reasoning|internal_analysis)\b[^>]*>.*?(?:</\1\s*>|$)",
        "",
        text,
        flags=re.I | re.S,
    )
    # A provider may split a hidden opening delimiter over multiple chunks.
    start = text.rfind("<")
    if start >= 0:
        suffix = text[start:].lower()
        tags = ("think", "thinking", "analysis", "reasoning", "internal_analysis")
        if any(
            ("<" + tag + ">").startswith(suffix)
            or (suffix.startswith("<" + tag) and ">" not in suffix)
            for tag in tags
        ):
            text = text[:start]
    return text[:limit]


class LivePlan:
    """Stable step identities with explicit completion evidence from real tools."""

    def __init__(self) -> None:
        self.plan_id = f"plan-{uuid.uuid4().hex}"
        self.revision = 0
        self.steps: list[dict[str, object]] = []
        self.evidence: dict[str, list[str]] = {}
        self.evidence_tools: dict[str, set[str]] = {}
        self.evidence_labels: dict[str, list[str]] = {}

    @property
    def active_step(self) -> str | None:
        return next(
            (str(s["id"]) for s in self.steps if s["status"] == "in_progress"), None
        )

    def observe(self, call_id: str, success: bool, tool: str = "read_file") -> None:
        if success and self.active_step:
            calls = self.evidence.setdefault(self.active_step, [])
            if call_id in calls:
                return
            calls.append(call_id)
            self.evidence_tools.setdefault(self.active_step, set()).add(tool)
            labels = {
                "read_file": "Read file",
                "build_firmware": "Built firmware",
                "run_simulation": "Ran simulation",
                "write_file": "Wrote file",
                "edit_file_simple": "Edited file",
                "apply_patch": "Applied patch",
                "grep_search": "Searched project",
                "list_files": "Listed files",
                "glob_files": "Found files",
            }
            self.evidence_labels.setdefault(self.active_step, []).append(
                labels.get(tool, tool.replace("_", " ").capitalize())
            )

    def update(self, steps: object) -> dict[str, object]:
        previous = {str(s["text"]): s for s in self.steps}
        next_steps = []
        for raw in steps if isinstance(steps, (tuple, list)) else []:
            if not isinstance(raw, Mapping):
                continue
            text = public_text(str(raw.get("text", "")), 300).strip()
            if not text:
                continue
            if any(step["text"] == text for step in next_steps):
                continue
            old = previous.get(text)
            identity = str(old["id"]) if old else f"step-{uuid.uuid4().hex}"
            status = str(raw.get("status", "pending"))
            if status not in (
                "pending",
                "in_progress",
                "completed",
                "failed",
                "skipped",
                "cancelled",
            ):
                status = "pending"
            evidence = self.evidence.get(identity, [])[-32:]
            verification = str(
                raw.get("verification")
                or (old or {}).get("verification")
                or "inspection"
            )
            required = {
                "build": {"build_firmware"},
                "simulation": {"run_simulation"},
                "edit": {"write_file", "edit_file_simple", "apply_patch"},
            }.get(verification)
            verified = bool(evidence) and (
                required is None
                or bool(required & self.evidence_tools.get(identity, set()))
            )
            if (
                status == "completed"
                and not verified
                and (not old or old["status"] != "completed")
            ):
                status = (
                    "in_progress"
                    if old and old["status"] == "in_progress"
                    else "pending"
                )
            if old and old["status"] == "completed":
                status = "completed"
            if status == "in_progress" and any(
                step["status"] == "in_progress" for step in next_steps
            ):
                status = "pending"
            next_steps.append(
                {
                    "id": identity,
                    "text": text,
                    "status": status,
                    "evidence": evidence,
                    "evidence_labels": self.evidence_labels.get(identity, [])[-32:],
                    "verification": verification,
                }
            )
        for old in self.steps:
            if not any(s["id"] == old["id"] for s in next_steps):
                next_steps.append(
                    {
                        **old,
                        "status": (
                            old["status"] if old["status"] == "completed" else "skipped"
                        ),
                    }
                )
        self.steps = next_steps[:64]
        self.revision += 1
        return {"plan_id": self.plan_id, "revision": self.revision, "steps": self.steps}
