"""Versioned, bounded task projection derived from the authoritative event journal."""
from __future__ import annotations

import hashlib
import json
from typing import Mapping, Sequence, TypedDict

from .deliverables import explicit_artifacts, project_deliverables


class TaskStateV1(TypedDict):
    schema_version: int
    request: str
    revisions: list[str]
    requirements: list[dict[str, object]]
    superseded_requirements: list[dict[str, object]]
    exclusions: list[str]
    decisions: list[str]
    milestone: dict[str, object] | None
    expected_artifacts: list[str]
    artifact_status: list[dict[str, object]]
    deliverable_manifest: list[dict[str, object]]
    evidence: list[dict[str, str]]
    blockers: list[str]
    failed_approaches: list[dict[str, object]]
    next_action: str
    recovery_state: dict[str, object]


def task_state_v1(*, contract: Mapping[str, object], plan: Sequence[Mapping[str, object]],
                  observations: Sequence[Mapping[str, object]], build: Mapping[str, object],
                  report: Mapping[str, object], repair_history: Sequence[Mapping[str, object]],
                  source_revision: str, source_paths: Sequence[str] | None = None, stalled_turns: int = 0,
                  reorientation_started_at: int | None = None) -> TaskStateV1:
    """Project durable state without making a second source of execution truth."""
    evidence = []
    artifacts = []
    known_paths: set[str] = set()
    for item in observations:
        output = item.get("output")
        if isinstance(output, Mapping) and item.get("status") == "completed":
            path = str(output.get("path") or "")
            if path and item.get("tool") == "write_file":
                known_paths.add(path)
            elif path and item.get("tool") == "delete_file":
                known_paths.discard(path)
    for item in observations[-32:]:
        call_id = str(item.get("call_id") or "")
        if not call_id:
            continue
        digest = hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()
        evidence.append({"kind": "tool_result", "ref": f"tool://{call_id}/result#sha256/{digest}",
                         "status": str(item.get("status") or "unknown")})
        output = item.get("output")
        if item.get("tool") in {"write_file", "delete_file"} and isinstance(output, Mapping):
            path = str(output.get("path") or "")
            if path and source_revision:
                artifacts.append({"path": path, "source_ref": f"source://{path}@{source_revision}",
                                  "status": str(item.get("status") or "unknown")})
    build_id = str(build.get("build_id") or "")
    if build_id:
        evidence.append({"kind": "firmware_build", "ref": f"build://{build_id}",
                         "status": "passed" if build.get("success") else "failed"})
    blockers = []
    if build and not build.get("success"):
        blockers.append(str(build.get("error_code") or build.get("message") or "Build failed")[:500])
    if report.get("missing"):
        blockers.append("Requirements still lack current source evidence")
    history = contract.get("revision_history")
    revisions = ([str(value) for value in history if value][-64:] if isinstance(history, (list, tuple)) else
                 [str(value) for value in (contract.get("parent_revision"), contract.get("revision")) if value])
    steps = [dict(item) for item in plan[-32:] if isinstance(item, Mapping)]
    milestone = next((item for item in steps if str(item.get("status") or "") not in {"completed", "done"}), None)
    next_action = ("Resolve the recorded build or dependency blocker." if blockers else
                   "Continue the first unfinished milestone using current source evidence." if milestone else
                   "Verify the current source revision and finish the task.")
    if build.get("error_code") == "AGENT_PROJECT_TRUST_REQUIRED":
        next_action = "Allow local builds for this project, then verify the saved draft."
    request = str(contract.get("request") or "")[:64000]
    manifest = project_deliverables(contract, paths=tuple(known_paths) if source_paths is None else source_paths,
                                    build=build, report=report, source_revision=source_revision)
    expected_artifacts = [str(item["path"]) for item in manifest if item.get("kind") == "file"][:64]
    if "deliverables" not in contract:
        # Preserve the old display field when reading checkpoints created before
        # the manifest existed. The manifest owns continuation decisions.
        expected_artifacts = explicit_artifacts(request)
    missing = [item for item in manifest if item["status"] == "missing"]
    source_missing = any(item["kind"] == "firmware_source" for item in missing)
    if missing and not blockers and (milestone is None or source_missing):
        next_action = "Generate missing deliverables before verification: " + ", ".join(
            str(item.get("path") or item["kind"]).replace("_", " ") for item in missing) + "."
    return {"schema_version": 1, "request": request,
            "revisions": revisions, "requirements": [dict(item) for item in contract.get("requirements", ()) if isinstance(item, Mapping)][:64],
            "superseded_requirements": [dict(item) for item in contract.get("superseded_requirements", ()) if isinstance(item, Mapping)][:64],
            "exclusions": [str(item) for item in contract.get("exclusions", ())][:64],
            "decisions": [str(item) for item in contract.get("accepted_choices", ())][:64],
            "milestone": milestone, "expected_artifacts": expected_artifacts, "artifact_status": artifacts[-32:],
            "deliverable_manifest": manifest,
            "evidence": evidence[-33:], "blockers": blockers,
            "failed_approaches": [dict(item) for item in repair_history[-16:] if isinstance(item, Mapping)],
            "next_action": next_action,
            "recovery_state": {"stalled_turns": stalled_turns,
                               "reorientation_started_at": reorientation_started_at}}
