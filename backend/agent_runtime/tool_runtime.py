"""Forge Agent V3 continuous model -> tool -> observation loop."""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from backend.changes import ChangeSet, ChangeSetError, ChangeSetService, IGNORED_DIRS

from .api_planner_provider import ApiPlannerError
from .capabilities import CapabilityGrant, ToolPolicyEngine
from .context_assembler import ContextAssembler
from .skills import SkillRegistry
from .tool_contracts import (
    ProductToolPlan,
    RuntimeClassification,
    RuntimeEvent,
    RuntimeEventType,
    ToolName,
    WRITE_TOOLS,
)
from .tool_executor import ForgeXToolExecutor, ToolHandler
from .tool_policy import ToolPermissionPolicy, ToolPolicyError, is_sensitive_path


@dataclass(frozen=True, slots=True)
class ProductRuntimeLimits:
    max_turns: int = 12
    max_tool_calls_per_turn: int = 6
    max_total_tool_calls: int = 48
    max_runtime_seconds: float = 300.0
    max_created_files: int = 20
    max_modified_files: int = 30
    max_bytes_per_file: int = 128_000
    max_observation_chars: int = 16_000


@dataclass(frozen=True, slots=True)
class ToolRuntimeResult:
    classification: RuntimeClassification
    provider_id: str
    change_set_id: str | None = None
    created_file_count: int = 0
    modified_file_count: int = 0
    deleted_file_count: int = 0
    active_workspace_unchanged: bool = False
    provider_classification: str | None = None
    provider_diagnostics: Mapping[str, object] = field(default_factory=dict)
    execution_attempted: bool = False
    tool_execution_count: int = 0
    plan: tuple[Mapping[str, str], ...] = field(default_factory=tuple)
    events: tuple[RuntimeEvent, ...] = field(default_factory=tuple)

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification.value,
            "provider_kind": "continuous_tool_agent",
            "provider_id": self.provider_id,
            "runtime": "forgex_agent_v3_loop",
            "change_set_id": self.change_set_id,
            "created_file_count": self.created_file_count,
            "modified_file_count": self.modified_file_count,
            "deleted_file_count": self.deleted_file_count,
            "active_workspace_unchanged": self.active_workspace_unchanged,
            "provider_classification": self.provider_classification,
            "provider_diagnostics": dict(self.provider_diagnostics),
            "execution_attempted": self.execution_attempted,
            "tool_execution_count": self.tool_execution_count,
            "plan": [dict(item) for item in self.plan],
            "events": [event.to_safe_dict() for event in self.events],
        }


class ForgeXToolRuntime:
    def __init__(
        self,
        *,
        active_workspace_root: str | Path,
        change_service: ChangeSetService | None = None,
        managed_stage_root: str | Path | None = None,
    ) -> None:
        self.active_workspace_root = Path(active_workspace_root).resolve(strict=True)
        if change_service is None:
            stage_root = Path(managed_stage_root or (self.active_workspace_root.parent / ".forgex-changes"))
            change_service = ChangeSetService(state_root=stage_root / "state", staging_root=stage_root / "staging")
        self.change_service = change_service
        self._events: list[RuntimeEvent] = []

    def run_product(
        self,
        *,
        task: str,
        planner: object,
        policy: ToolPermissionPolicy,
        limits: ProductRuntimeLimits | None = None,
        cancel_event: threading.Event | None = None,
        event_callback=None,
        policy_engine: ToolPolicyEngine | None = None,
        capability_grant: CapabilityGrant | None = None,
        capability_authorization_source: str = "explicit_edit_request",
        capability_run_id: str | None = None,
        capability_node_id: str = "firmware_implementer",
        capability_session_id: str | None = None,
        service_handlers: Mapping[ToolName, ToolHandler] | None = None,
        memory_entries: Sequence[Mapping[str, object]] = (),
        steering_callback=None,
        allow_no_changes_success: bool = False,
    ) -> ToolRuntimeResult:
        bounds = limits or ProductRuntimeLimits()
        cancelled = cancel_event or threading.Event()
        emit = event_callback or (lambda event: None)
        self._events = []
        self._event(RuntimeEventType.RUNTIME_STARTED)
        provider_id = str(getattr(planner, "provider_id", "unknown_planner"))
        request_turn = getattr(planner, "request_turn", None)
        if not isinstance(task, str) or not task.strip() or not callable(request_turn):
            return self._failure(RuntimeClassification.PROVIDER_INVALID, provider_id)

        active_before = _active_integrity_snapshot(self.active_workspace_root)
        baseline = self.change_service.inspect_workspace(self.active_workspace_root)
        started = time.monotonic()
        stage = self.change_service.create_stage(f"agent-v3-{uuid.uuid4().hex}")
        skill_registry = SkillRegistry(self.active_workspace_root)
        skill_labels = tuple(item.label() for item in skill_registry.discover())
        # Deterministic in-process template providers match the literal user task.
        # API/model providers receive the richer tool-first project context.
        provider_kind = str(getattr(planner, "provider_kind", ""))
        provider_task = (
            task
            if provider_kind == "template_provider"
            else ContextAssembler(self.active_workspace_root).assemble(
                task=task,
                snapshot=baseline,
                memories=memory_entries,
                skills=skill_labels,
            ).render()
        )

        handlers = dict(service_handlers or {})
        allowed = [tool.value for tool in ToolName if policy.decisions.get(tool, "deny") != "deny"]
        # Host-backed tools are never advertised unless a trusted handler exists.
        for tool in (ToolName.MEMORY_SEARCH, ToolName.SPAWN_SUBAGENT, ToolName.BUILD_FIRMWARE, ToolName.RUN_COMMAND):
            if tool not in handlers and tool.value in allowed:
                allowed.remove(tool.value)
        allowed_tools = tuple(allowed)

        if policy_engine is not None and capability_grant is None:
            capability_grant = policy_engine.issue_grant(
                run_id=capability_run_id or stage.name,
                node_id=capability_node_id,
                # Capabilities bind to the active project; writes still physically land in stage.
                workspace_root=self.active_workspace_root,
                allowed_tools=allowed_tools,
                authorization_source=capability_authorization_source,
                session_id=capability_session_id,
                max_calls=bounds.max_total_tool_calls,
                allow_build=ToolName.BUILD_FIRMWARE.value in allowed_tools,
            )
        executor = ForgeXToolExecutor(
            write_root=stage,
            read_root=self.active_workspace_root,
            policy=policy,
            policy_engine=policy_engine,
            capability_grant=capability_grant,
            service_handlers=handlers,
            skill_registry=skill_registry,
        )
        self._event(RuntimeEventType.STAGE_CREATED, count=len(baseline.files))
        emit({"event_type": "changes.stage.created", "status": "running", "message": "Forge V3 overlay workspace created"})

        total_calls = 0
        denied_seen = False
        denied_classification: RuntimeClassification | None = None
        observations: list[Mapping[str, object]] = []
        authorized_paths: set[str] = set()
        final_received = False
        # Planner instances are per-run, so the listener lifetime matches the run.
        _wire_delta_listener(planner, emit)
        try:
            for turn in range(1, bounds.max_turns + 1):
                if cancelled.is_set():
                    return self._abort_stage(stage, RuntimeClassification.CANCELLED, provider_id, total_calls)
                if time.monotonic() - started > bounds.max_runtime_seconds:
                    return self._abort_stage(stage, RuntimeClassification.LIMIT_EXCEEDED, provider_id, total_calls)
                steering = tuple(steering_callback() if callable(steering_callback) else ())
                if steering:
                    for message in steering:
                        observations.append({
                            "call_id": f"steer_{turn}",
                            "status": "completed",
                            "tool": "user_steer",
                            "output": str(message)[:4000],
                        })
                    emit({"event_type": "agent.steered", "status": "running", "turn": turn, "message": str(steering[-1])[:500]})
                compacted = _compact_tool_observations(observations)
                emit({"event_type": "agent.turn.started", "status": "running", "turn": turn, "message": "Forge is deciding the next tool action"})
                raw_plan = request_turn(
                    task=provider_task,
                    tool_results=compacted,
                    allowed_tools=allowed_tools,
                    run_id=capability_run_id or stage.name,
                    turn=turn,
                )
                plan = raw_plan if isinstance(raw_plan, ProductToolPlan) else ProductToolPlan.parse(raw_plan, max_calls=bounds.max_tool_calls_per_turn)
                if len(plan.tool_calls) > bounds.max_tool_calls_per_turn:
                    return self._abort_stage(stage, RuntimeClassification.LIMIT_EXCEEDED, provider_id, total_calls)
                emit({"event_type": "agent.turn.completed", "status": "running", "turn": turn, "tool_count": len(plan.tool_calls), "message": plan.summary})
                if plan.final:
                    final_received = True
                    break
                if total_calls + len(plan.tool_calls) > bounds.max_total_tool_calls:
                    return self._abort_stage(stage, RuntimeClassification.LIMIT_EXCEEDED, provider_id, total_calls)

                for product_call in plan.tool_calls:
                    if cancelled.is_set():
                        return self._abort_stage(stage, RuntimeClassification.CANCELLED, provider_id, total_calls)
                    try:
                        call = product_call.as_runtime_call()
                        executor.validate(call)
                    except (ToolPolicyError, ValueError) as exc:
                        denied_seen = True
                        classification = getattr(exc, "classification", RuntimeClassification.POLICY_DENIED)
                        denied_classification = denied_classification or classification
                        observations.append({
                            "call_id": product_call.call_id,
                            "status": "denied",
                            "tool": product_call.tool_type,
                            "classification": classification.value,
                            "error": str(exc)[:300],
                        })
                        emit({"event_type": "tool.denied", "status": "running", "tool": product_call.tool_type, "classification": classification.value})
                        continue
                    emit({"event_type": "tool.started", "status": "running", "tool": call.tool.value, "message": _tool_activity(call)})
                    result = executor.execute(call)
                    if call.tool in WRITE_TOOLS and product_call.path:
                        authorized_paths.add(product_call.path)
                    total_calls += 1
                    observations.append(_safe_tool_observation(product_call.call_id, call.tool, result, bounds.max_observation_chars))
                    emit({"event_type": "tool.completed", "status": "running", "tool": call.tool.value, "tool_execution_count": total_calls, "message": _tool_completion(call)})

            if not final_received:
                return self._abort_stage(stage, RuntimeClassification.MAX_TURNS, provider_id, total_calls)
            if denied_seen and total_calls == 0:
                return self._abort_stage(stage, denied_classification or RuntimeClassification.POLICY_DENIED, provider_id, total_calls)
            if active_before != _active_integrity_snapshot(self.active_workspace_root):
                return self._abort_stage(stage, RuntimeClassification.UNSAFE_ABORTED, provider_id, total_calls)

            try:
                change_set = self.change_service.create_from_stage(
                    provider_id=provider_id,
                    workspace_root=self.active_workspace_root,
                    stage_root=stage,
                    baseline=baseline,
                    authorized_paths=authorized_paths,
                )
            except ChangeSetError as exc:
                classification = _classification_for_change_error(exc)
                # A pure inspection/build turn is valid even if no workspace diff exists.
                if classification is RuntimeClassification.NO_CHANGES and not authorized_paths and (total_calls > 0 or allow_no_changes_success):
                    shutil.rmtree(stage, ignore_errors=True)
                    self._event(RuntimeEventType.RUNTIME_COMPLETED, classification=RuntimeClassification.PASS.value)
                    return ToolRuntimeResult(
                        classification=RuntimeClassification.PASS,
                        provider_id=provider_id,
                        active_workspace_unchanged=True,
                        provider_classification=_planner_classification(planner),
                        provider_diagnostics=_safe_provider_diagnostics(planner),
                        execution_attempted=True,
                        tool_execution_count=total_calls,
                        plan=tuple(executor.current_plan),
                        events=tuple(self._events),
                    )
                return self._abort_stage(stage, classification, provider_id, total_calls)

            created, modified, deleted = _change_counts(change_set)
            if created > bounds.max_created_files or modified > bounds.max_modified_files:
                self.change_service.discard(change_set.change_set_id)
                return self._failure(RuntimeClassification.LIMIT_EXCEEDED, provider_id, tool_execution_count=total_calls)
            self._event(RuntimeEventType.CHANGESET_CREATED, count=len(change_set.changed_files))
            self._event(RuntimeEventType.RUNTIME_COMPLETED, classification=RuntimeClassification.PASS.value)
            return ToolRuntimeResult(
                classification=RuntimeClassification.PASS,
                provider_id=provider_id,
                change_set_id=change_set.change_set_id,
                created_file_count=created,
                modified_file_count=modified,
                deleted_file_count=deleted,
                active_workspace_unchanged=True,
                provider_classification=_planner_classification(planner),
                provider_diagnostics=_safe_provider_diagnostics(planner),
                execution_attempted=True,
                tool_execution_count=total_calls,
                plan=tuple(executor.current_plan),
                events=tuple(self._events),
            )
        except ApiPlannerError as exc:
            shutil.rmtree(stage, ignore_errors=True)
            return self._failure(RuntimeClassification.PROVIDER_INVALID, provider_id, provider_classification=exc.classification, provider_diagnostics=_safe_provider_diagnostics(planner), execution_attempted=True, tool_execution_count=total_calls)
        except ToolPolicyError as exc:
            shutil.rmtree(stage, ignore_errors=True)
            return self._failure(exc.classification, provider_id, tool_execution_count=total_calls)
        except (PermissionError, ValueError, TypeError):
            shutil.rmtree(stage, ignore_errors=True)
            return self._failure(RuntimeClassification.PROVIDER_INVALID, provider_id, tool_execution_count=total_calls)
        except OSError:
            shutil.rmtree(stage, ignore_errors=True)
            return self._failure(RuntimeClassification.UNSAFE_ABORTED, provider_id, tool_execution_count=total_calls)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            return self._failure(RuntimeClassification.UNKNOWN_SAFE_FAILURE, provider_id, tool_execution_count=total_calls)

    def _abort_stage(self, stage: Path, classification: RuntimeClassification, provider_id: str, tool_execution_count: int, *, remove: bool = True) -> ToolRuntimeResult:
        if remove:
            shutil.rmtree(stage, ignore_errors=True)
        return self._failure(classification, provider_id, active_unchanged=classification is not RuntimeClassification.UNSAFE_ABORTED, tool_execution_count=tool_execution_count)

    def _failure(self, classification: RuntimeClassification, provider_id: str, *, created: int = 0, modified: int = 0, deleted: int = 0, active_unchanged: bool = False, provider_classification: str | None = None, provider_diagnostics: Mapping[str, object] | None = None, execution_attempted: bool = False, tool_execution_count: int = 0) -> ToolRuntimeResult:
        self._event(RuntimeEventType.RUNTIME_FAILED, classification=classification.value)
        return ToolRuntimeResult(classification=classification, provider_id=provider_id, created_file_count=created, modified_file_count=modified, deleted_file_count=deleted, active_workspace_unchanged=active_unchanged, provider_classification=provider_classification, provider_diagnostics=dict(provider_diagnostics or {}), execution_attempted=execution_attempted, tool_execution_count=tool_execution_count, events=tuple(self._events))

    def _event(self, event_type: RuntimeEventType, *, classification: str | None = None, count: int | None = None) -> None:
        self._events.append(RuntimeEvent(event_type, len(self._events) + 1, classification, count))


def _wire_delta_listener(planner: object, emit) -> None:
    """Forward streamed planner tokens onto the run/session event channels."""
    attach = getattr(planner, "set_delta_listener", None)
    if not callable(attach):
        return
    state = {"index": 0}

    def listener(turn: int, delta: str) -> None:
        state["index"] += 1
        emit({
            "event_type": "message.delta",
            "status": "running",
            "turn": int(turn),
            "delta": delta,
            "index": state["index"],
        })

    try:
        attach(listener)
    except Exception:
        pass


def _compact_tool_observations(observations: Sequence[Mapping[str, object]], *, keep: int = 24) -> tuple[Mapping[str, object], ...]:
    if len(observations) <= keep:
        return tuple(observations)
    older = observations[:-keep]
    checkpoint = {
        "call_id": "context_checkpoint",
        "status": "completed",
        "tool": "context_compaction",
        "output": {
            "compacted_observations": len(older),
            "tools": [str(item.get("tool") or "unknown") for item in older[-30:]],
            "failures": sum(item.get("status") in {"failed", "denied"} for item in older),
        },
    }
    return (checkpoint, *observations[-keep:])


def _safe_tool_observation(call_id: str, tool: ToolName, result: object, max_chars: int) -> dict[str, object]:
    observation: dict[str, object] = {"call_id": call_id, "status": "completed", "tool": tool.value}
    if isinstance(result, str):
        observation["output"] = result[:max_chars]
    elif isinstance(result, tuple):
        observation["output"] = list(result[:200])
    elif isinstance(result, Mapping):
        safe: dict[str, object] = {}
        for key, value in result.items():
            if isinstance(value, str):
                safe[str(key)] = value[:max_chars]
            elif isinstance(value, (int, float, bool, type(None))):
                safe[str(key)] = value
            elif isinstance(value, (list, tuple)):
                safe[str(key)] = list(value[:100])
        observation["output"] = safe
    else:
        observation["output"] = str(result)[:max_chars]
    return observation


def _tool_activity(call) -> str:
    labels = {
        ToolName.LIST_FILES: "Listing project files",
        ToolName.GLOB_FILES: "Finding matching project files",
        ToolName.GREP_SEARCH: "Searching project source",
        ToolName.READ_FILE: "Reading project source",
        ToolName.WRITE_FILE: "Writing staged file",
        ToolName.EDIT_FILE_SIMPLE: "Applying staged edit",
        ToolName.UPDATE_PLAN: "Updating task plan",
        ToolName.LOAD_SKILL: "Loading Forge skill",
        ToolName.MEMORY_SEARCH: "Searching verified project memory",
        ToolName.BUILD_FIRMWARE: "Building staged firmware",
        ToolName.RUN_COMMAND: "Running sandboxed verification command",
    }
    return labels.get(call.tool, call.tool.value)


def _tool_completion(call) -> str:
    return _tool_activity(call) + " completed"


def _classification_for_change_error(exc: ChangeSetError) -> RuntimeClassification:
    if exc.code == "CHANGE_NO_CHANGES":
        return RuntimeClassification.NO_CHANGES
    if exc.code == "CHANGE_EXTRA_FILES":
        return RuntimeClassification.EXTRA_CHANGES
    if "PATH" in exc.code:
        return RuntimeClassification.PATH_UNSAFE
    return RuntimeClassification.UNSAFE_ABORTED


def _change_counts(change_set: ChangeSet) -> tuple[int, int, int]:
    created = sum(item.change_type == "created" for item in change_set.changed_files)
    modified = sum(item.change_type == "modified" for item in change_set.changed_files)
    deleted = sum(item.change_type == "deleted" for item in change_set.changed_files)
    return created, modified, deleted


def _planner_classification(planner: object) -> str | None:
    metadata = _safe_provider_diagnostics(planner)
    value = metadata.get("safe_error_code")
    return None if value is None else str(value)


def _safe_provider_diagnostics(planner: object) -> dict[str, object]:
    getter = getattr(planner, "to_safe_metadata", None)
    if not callable(getter):
        return {}
    try:
        metadata = getter()
    except Exception:
        return {}
    if not isinstance(metadata, Mapping):
        return {}
    allowed = {
        "provider_id", "model_id", "requested_model_id", "classification",
        "outbound_request_count", "request_reached_provider", "http_status",
        "provider_request_id", "safe_error_code", "toolplan_normalized",
        "toolplan_repair_attempted", "toolplan_mode", "fallback_used", "fallback_reason",
    }
    return {key: value for key, value in metadata.items() if key in allowed and isinstance(value, (str, int, bool, type(None)))}


def _active_integrity_snapshot(root: Path) -> dict[str, tuple[object, ...]]:
    snapshot: dict[str, tuple[object, ...]] = {}
    for current_root, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(current_root)
        dirnames[:] = [name for name in dirnames if name.casefold() not in IGNORED_DIRS]
        for filename in filenames:
            path = current / filename
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            if path.is_symlink():
                snapshot[relative] = ("link", info.st_size, info.st_mtime_ns)
            elif is_sensitive_path(relative):
                snapshot[relative] = ("sensitive", info.st_size, info.st_mtime_ns)
            else:
                snapshot[relative] = ("file", info.st_size, info.st_mtime_ns, _hash_file(path))
    return snapshot


def _task_with_project_context(task: str, snapshot) -> str:
    """Backward-compatible helper now returning lean tool-first context."""
    root = getattr(snapshot, "workspace_root", None)
    if root:
        try:
            return ContextAssembler(root).assemble(task=task, snapshot=snapshot).render()
        except Exception:
            pass
    paths = sorted(getattr(snapshot, "files", {}))[:120]
    return task.strip() + "\n\nWorkspace inventory (use read/search tools for contents):\n" + "\n".join(paths)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
