"""Versioned, evidence-driven scheduling shared by both workspace strategies.

This controller owns work hints and bounded recovery, not permissions or final
acceptance. The task contract, workspace and execution evidence remain truth.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from pathlib import PurePosixPath

from .diagnostic_progress import diagnostic_fingerprint


CONTROLLER_VERSION = 1
_WRITES = {"write_file", "edit_file_simple", "apply_patch", "move_file", "delete_file"}
_FOCUSED_TOOLS = _WRITES | {"list_files", "glob_files", "grep_search", "read_file", "inspect_symbols",
    "update_plan", "resolve_dependency", "build_firmware", "run_unit_tests", "verify_requirements", "verify_acceptance"}


def workspace_changed(observation):
    output = observation.get("output")
    return observation.get("status") == "completed" and isinstance(output, Mapping) and bool(
        output.get("created") or output.get("deleted") or output.get("modified") or output.get("moved")
        or output.get("change_type") in {"moved", "deleted"} or output.get("diff"))


def validate_plan_steps(steps, requirement_ids=None):
    if not isinstance(steps, (list, tuple)) or not 1 <= len(steps) <= 32:
        raise ValueError("PLAN_STEPS_INVALID")
    result, keys = [], set()
    allowed = {"text", "status", "verification", "key", "paths", "requirement_ids", "depends_on"}
    for raw in steps:
        if (not isinstance(raw, Mapping) or set(raw) - allowed or not isinstance(raw.get("text"), str)
                or not 1 <= len(raw["text"].strip()) <= 300 or raw.get("status") not in
                {"pending", "in_progress", "completed", "failed", "skipped", "cancelled"}):
            raise ValueError("PLAN_STEP_INVALID")
        step = dict(raw)
        key = step.get("key", hashlib.sha256(step["text"].encode()).hexdigest()[:16])
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key) or key in keys:
            raise ValueError("PLAN_STEP_KEY_INVALID")
        keys.add(key)
        if "key" in step:
            step["key"] = key
        if "verification" in step and step["verification"] not in {"inspection", "edit", "build", "simulation"}:
            raise ValueError("PLAN_VERIFICATION_INVALID")
        for field, limit in (("paths", 64), ("requirement_ids", 64), ("depends_on", 32)):
            values = step.get(field, [])
            if not isinstance(values, list) or len(values) > limit or any(not isinstance(item, str) or not item or len(item) > 512 for item in values):
                raise ValueError("PLAN_REFERENCES_INVALID")
            if len(set(values)) != len(values):
                raise ValueError("PLAN_REFERENCES_INVALID")
        for path in step.get("paths", []):
            if (PurePosixPath(path).is_absolute() or any(part in {"..", "."} or part.startswith(".") for part in path.split("/"))
                    or "\\" in path or ":" in path or "\x00" in path or "//" in path):
                raise ValueError("PLAN_PATH_UNSAFE")
        if requirement_ids is not None and set(step.get("requirement_ids", [])) - set(requirement_ids):
            raise ValueError("PLAN_REQUIREMENT_UNKNOWN")
        result.append(step)
    graph = {step.get("key", hashlib.sha256(step["text"].encode()).hexdigest()[:16]): step.get("depends_on", []) for step in result}
    visited, visiting = set(), set()

    def visit(key):
        if key in visiting:
            raise ValueError("PLAN_DEPENDENCY_CYCLE")
        if key not in graph:
            raise ValueError("PLAN_DEPENDENCY_UNKNOWN")
        if key in visited:
            return
        visiting.add(key)
        for dependency in graph[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in graph:
        visit(key)
    return tuple(result)


def controller_state_supported(saved):
    return saved is None or isinstance(saved, Mapping) and type(saved.get("version")) is int and saved["version"] == CONTROLLER_VERSION


class GenerationController:
    def __init__(self, contract=None, saved=None, *, enabled=True):
        if not controller_state_supported(saved):
            raise ValueError("AGENT_CONTROLLER_VERSION_UNSUPPORTED")
        saved = saved or {}
        self.enabled = bool(enabled and contract and os.getenv("FORGEX_ENABLE_STRUCTURED_GENERATION", "1") == "1")
        self.stalled_turns = min(5, max(0, int(saved.get("stalled_turns", 0))))
        self.dirty_paths = set(saved.get("dirty_paths", ()))
        self.verifications = set(saved.get("verifications", ()))
        self.resolved_packages = set(saved.get("resolved_packages", ()))
        self.revision = str(saved.get("source_revision", ""))
        self.task_revision = str(saved.get("task_revision", ""))
        self.completed = dict(saved.get("completed", {}))
        self.invalidated = dict(saved.get("invalidated", {}))
        self.active_key = saved.get("active_key")
        self.last_build_revision = str(saved.get("last_build_revision", ""))
        self.failed_edit = dict(saved.get("failed_edit") or {})
        self._material = False
        self._projection = {}
        self.reopened_keys = set()

    def observe(self, observation):
        output = observation.get("output")
        if (observation.get("status") == "failed" and observation.get("tool") == "edit_file_simple"
                and isinstance(output, Mapping) and output.get("error") == "edit_match_must_be_exactly_one"
                and isinstance(output.get("path"), str)
                and isinstance(output.get("current_excerpt"), Mapping)
                and output["current_excerpt"].get("truncated") is False):
            self.failed_edit = {"path": output["path"], "source_revision": self.revision}
        if workspace_changed(observation) and observation.get("tool") in _WRITES:
            self.failed_edit = {}
            self._material = True
            self.stalled_turns = 0
            for key in ("path", "destination"):
                path = observation["output"].get(key)
                if isinstance(path, str):
                    self.dirty_paths.add(path)
        output = observation.get("output")
        if observation.get("status") != "completed" or not isinstance(output, Mapping):
            return
        tool = observation.get("tool")
        if tool == "build_firmware" and not output.get("cached") and output.get("workspace_revision"):
            identity = str(output["workspace_revision"]) + ":" + diagnostic_fingerprint(output)
            if identity not in self.verifications:
                self._material = True
                self.verifications.add(identity)
            self.last_build_revision = str(output["workspace_revision"])
            self.dirty_paths.clear()
        elif tool == "resolve_dependency" and output.get("source") == "platformio_registry":
            # Different verified packages are real configuration work. Query
            # aliases, changing timestamps and repeated cache hits cannot renew
            # progress, and the host accepts at most 32 dependencies per task.
            for package in output.get("packages", ())[:5]:
                identity = package.get("id", "") if isinstance(package, Mapping) else ""
                if (isinstance(identity, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}/[A-Za-z0-9][A-Za-z0-9_. +()-]{0,99}", identity)
                        and package.get("metadata_verified") is True and package.get("available_versions")
                        and identity.casefold() not in self.resolved_packages and len(self.resolved_packages) < 32):
                    self.resolved_packages.add(identity.casefold())
                    self._material = True
        elif (tool in {"verify_requirements", "verify_acceptance"} and output.get("new_evidence_count", 0) > 0
              or tool in {"run_unit_tests", "run_simulation"} and output.get("success") is True):
            import json
            from .diagnostic_progress import stable_value
            identity = tool + ":" + self.revision + ":" + hashlib.sha256(json.dumps(stable_value(output), sort_keys=True, default=str).encode()).hexdigest()
            if identity not in self.verifications:
                self._material = True
                self.verifications.add(identity)

    def settle(self):
        self.stalled_turns = 0 if self._material else min(5, self.stalled_turns + 1)
        self._material = False

    def focus(self, *, contract, plan, files, source_revision, build=None, report=None):
        build, report = build or {}, report or {}
        task_revision = str(contract.get("revision", ""))
        task_changed = bool(self.task_revision and self.task_revision != task_revision)
        if task_changed:
            self.completed.clear()
            self.stalled_turns = 0
        if task_changed or self.failed_edit.get("source_revision") != source_revision:
            self.failed_edit = {}
        self.task_revision = task_revision
        self.revision = source_revision
        self.reopened_keys = set()
        steps = [dict(step) for step in plan][:32]
        for step in steps:
            key = step.get("key") or step.get("id") or hashlib.sha256(str(step.get("text", "")).encode()).hexdigest()[:16]
            step["key"] = key
            paths = step.get("paths", [])
            evidence = self.completed.get(key)
            current = {path: files.get(path) for path in paths}
            proof = tuple(step.get("evidence", ()))
            if task_changed or evidence and (evidence.get("files") != current
                    or (not paths or step.get("verification") in {"build", "simulation"}) and evidence.get("revision") != source_revision):
                self.completed.pop(key, None)
                self.invalidated[key] = list(proof)
                self.reopened_keys.add(key)
                step["status"] = "pending"
            elif key in self.invalidated and tuple(self.invalidated[key]) == proof:
                self.reopened_keys.add(key)
                step["status"] = "pending"
            elif step.get("status") == "completed" and all(current.values()) and proof:
                self.invalidated.pop(key, None)
                self.completed[key] = {"files": current, "revision": source_revision}
            if step.get("status") == "completed" and paths and (not all(current.values()) or not proof):
                step["status"] = "pending"
        done = {step["key"] for step in steps if step.get("status") == "completed"}
        pending = [step for step in steps if step.get("status") not in {"completed", "skipped", "cancelled"}
            and set(step.get("depends_on", [])) <= done]
        active = next((step for step in pending if step.get("status") == "in_progress"), pending[0] if pending else None)
        if not steps:
            from .deliverables import explicit_artifacts
            active = {"key": "task", "text": "Complete the requested deliverable", "paths": explicit_artifacts(str(contract.get("request", ""))),
                "requirement_ids": [str(item["id"]) for item in contract.get("requirements", ()) if "id" in item]}
        self.active_key = active.get("key") if active else None
        required = [str(item["id"]) for item in contract.get("requirements", ()) if "id" in item]
        current_report = (report.get("workspace_revision") == source_revision
            and report.get("task_revision") == task_revision)
        reviewed = {str(item.get("id")) for item in report.get("checks", ())
            if current_report and item.get("status") in {"referenced", "reviewed"}}
        missing = [path for path in (active or {}).get("paths", []) if path not in files]
        symbols = [str(item.get("symbol")) for item in build.get("linker_diagnostics", ())[:8] if item.get("symbol")]
        phase = ("configuration" if build.get("error_code") in {"BUILD_CONFIGURATION_MISSING", "BUILD_CONFIGURATION_INVALID", "DEPENDENCY_UNRESOLVED"}
            else "repair" if build and build.get("success") is False and not build.get("verification_stale")
            else "verification" if build.get("success") and build.get("workspace_revision") == source_revision else "generation")
        next_action = ("Implement and link the unresolved symbols: " + ", ".join(symbols) if symbols else
            "Create missing milestone files: " + ", ".join(missing) if missing else
            "Continue: " + str(active["text"]) if active else
            "Generate coherent interfaces, implementations and callers for the remaining requirements.")
        if phase == "verification":
            next_action = "Submit current evidence for the remaining requirement IDs and finish independent review."
            if current_report and report.get("references_valid") is True and not report.get("semantic_review"):
                next_action = ("All requirement references are accepted. Finish with no tool calls so the host "
                    "can perform independent review; do not resubmit unchanged citations.")
        elif self.failed_edit and self.stalled_turns >= 3:
            next_action = ("Exact edit matching failed for " + self.failed_edit["path"]
                + ". Use apply_patch or deliberately rewrite that file from the supplied complete current_excerpt, "
                "preserving its required behavior. The prior edit changed nothing. Do not repeat the unmatched text or reread the unchanged file.")
        self._projection = {"version": CONTROLLER_VERSION, "phase": phase,
            "active_milestone": {key: value for key, value in (active or {}).items() if key in {"key", "text", "paths", "requirement_ids", "depends_on"}} or None,
            "missing_artifacts": missing[:64], "blocking_symbols": symbols,
            "remaining_requirement_ids": [key for key in required if key not in reviewed],
            "edit_recovery": ({"path": self.failed_edit["path"], "source_revision": source_revision,
                "required_action": "rewrite_file"} if self.failed_edit and self.stalled_turns == 3 and phase != "verification" else None),
            "stalled_turns": self.stalled_turns, "next_action": next_action[:1800],
            "instruction": "Complete one coherent milestone using current declarations and actual tool results. "
                "Read relevant files once, then implement missing behavior. A read or plan update does not implement code. "
                "Do not drop requirements or accepted choices. Only current builds and independent reviews establish completion."}
        return dict(self._projection)

    def narrow_tools(self, tools):
        if not self.enabled or self.stalled_turns < 3 or self._projection.get("phase") == "verification":
            return tools
        # Keep already authorized editing/inspection functions stable. Removing
        # an edit function mid-conversation turned a repairable match failure
        # into a provider-level forbidden-tool failure. Execution guards remain.
        return tuple(name for name in tools if name in _FOCUSED_TOOLS)

    def should_build(self, plan, paths, remaining):
        if not self.enabled or not self.dirty_paths:
            return False
        if remaining <= 180:
            return True
        structured = [step for step in plan if step.get("paths")]
        if not structured:
            return False  # Legacy scheduling remains available to the adapter.
        active = next((step for step in structured if step.get("key") == self.active_key), None)
        return bool(active and active.get("status") == "completed" and all(path in paths for path in active["paths"]))

    @property
    def projection(self):
        return dict(self._projection)

    def snapshot(self):
        return {"version": CONTROLLER_VERSION, "stalled_turns": self.stalled_turns,
            "dirty_paths": sorted(self.dirty_paths)[:128], "verifications": sorted(self.verifications)[-128:],
            "resolved_packages": sorted(self.resolved_packages)[:32],
            "source_revision": self.revision, "task_revision": self.task_revision,
            "completed": dict(list(self.completed.items())[-32:]), "active_key": self.active_key,
            "failed_edit": dict(self.failed_edit),
            "invalidated": dict(list(self.invalidated.items())[-32:]),
            "last_build_revision": self.last_build_revision}
