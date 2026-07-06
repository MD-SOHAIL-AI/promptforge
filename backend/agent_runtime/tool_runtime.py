"""Orchestration for provider plan -> ForgeX tools -> exact diff -> review."""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from backend.bridges.diff_service import IGNORED_DIRS, BridgeDiffService, is_safe_relative_path
from backend.bridges.review_models import BridgeReviewSession, BridgeWorkspaceSnapshot

from .provider_contracts import ApiBackedProvider, ApiProviderError
from .api_planner_provider import ApiPlannerClassification, ApiPlannerError
from .tool_contracts import (
    RuntimeClassification,
    RuntimeEvent,
    RuntimeEventType,
    ProductToolPlan,
    ToolPlan,
)
from .tool_executor import ForgeXToolExecutor
from .tool_policy import ToolPermissionPolicy, ToolPolicyError, is_sensitive_path


MARKER_NAME = ".forgex-tool-runtime-marker.json"
MARKER_BYTES = b'{"owner":"forgex","schema":1}\n'
PostReviewPipeline = Callable[[str], Mapping[str, object]]
ProductEventCallback = Callable[[Mapping[str, object]], None]


@dataclass(frozen=True, slots=True)
class ProductRuntimeLimits:
    max_turns: int = 5
    max_tool_calls_per_turn: int = 5
    max_total_tool_calls: int = 20
    max_runtime_seconds: float = 120.0
    max_created_files: int = 10
    max_modified_files: int = 10
    max_bytes_per_file: int = 128_000


@dataclass(frozen=True, slots=True)
class ToolRuntimeResult:
    classification: RuntimeClassification
    provider_id: str
    review_id: str | None = None
    created_file_count: int = 0
    modified_file_count: int = 0
    deleted_file_count: int = 0
    review_created: bool = False
    active_workspace_unchanged: bool = False
    marker_unchanged: bool = False
    raw_prompt_persisted: bool = False
    raw_provider_output_persisted: bool = False
    apply_run: bool = False
    build_run: bool = False
    flash_run: bool = False
    patch_pipeline: Mapping[str, object] | None = None
    provider_classification: str | None = None
    provider_diagnostics: Mapping[str, object] = field(default_factory=dict)
    execution_attempted: bool = False
    tool_execution_count: int = 0
    events: tuple[RuntimeEvent, ...] = field(default_factory=tuple)

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "classification": self.classification.value,
            "provider_kind": "api_backed_design",
            "provider_id": self.provider_id,
            "runtime": "forgex_owned_tool_runtime",
            "review_id": self.review_id,
            "review_created": self.review_created,
            "created_file_count": self.created_file_count,
            "modified_file_count": self.modified_file_count,
            "deleted_file_count": self.deleted_file_count,
            "active_workspace_unchanged": self.active_workspace_unchanged,
            "marker_unchanged": self.marker_unchanged,
            "raw_prompt_persisted": self.raw_prompt_persisted,
            "raw_provider_output_persisted": self.raw_provider_output_persisted,
            "apply_run": self.apply_run,
            "build_run": self.build_run,
            "flash_run": self.flash_run,
            "patch_pipeline": dict(self.patch_pipeline or {"run": False}),
            "provider_classification": self.provider_classification,
            "provider_diagnostics": dict(self.provider_diagnostics),
            "execution_attempted": self.execution_attempted,
            "tool_execution_count": self.tool_execution_count,
            "events": [event.to_safe_dict() for event in self.events],
        }


class ForgeXToolRuntime:
    def __init__(
        self,
        *,
        managed_sandbox_root: str | Path,
        active_workspace_root: str | Path,
        review_service: BridgeDiffService | None = None,
        post_review_pipeline: PostReviewPipeline | None = None,
    ) -> None:
        self.managed_sandbox_root = Path(managed_sandbox_root).resolve()
        self.active_workspace_root = Path(active_workspace_root).resolve(strict=True)
        self.review_service = review_service or BridgeDiffService()
        self.post_review_pipeline = post_review_pipeline
        self._events: list[RuntimeEvent] = []

    def run_product(
        self,
        *,
        task: str,
        planner: object,
        policy: ToolPermissionPolicy,
        limits: ProductRuntimeLimits | None = None,
        cancel_event: threading.Event | None = None,
        event_callback: ProductEventCallback | None = None,
    ) -> ToolRuntimeResult:
        """Run a bounded multi-turn planner loop inside one ForgeX sandbox."""

        bounds = limits or ProductRuntimeLimits()
        cancelled = cancel_event or threading.Event()
        self._events = []
        self._event(RuntimeEventType.RUNTIME_STARTED)
        emit = event_callback or (lambda event: None)
        provider_id = str(getattr(planner, "provider_id", "unknown_planner"))
        model_id = str(getattr(planner, "model_id", "unknown_model"))
        request_turn = getattr(planner, "request_turn", None)
        if not isinstance(task, str) or not task.strip() or not callable(request_turn):
            return self._failure(RuntimeClassification.PROVIDER_INVALID, provider_id)

        active_before = _active_integrity_snapshot(self.active_workspace_root)
        started = time.monotonic()
        run_id = f"agent-runtime-{uuid.uuid4().hex}"
        total_calls = 0
        denied_seen = False
        tool_results: list[Mapping[str, object]] = []
        executed_plans: list[ToolPlan] = []
        authorized_paths: set[str] = set()
        final_received = False
        try:
            sandbox_root = self._create_sandbox(run_id)
            marker = sandbox_root / MARKER_NAME
            marker.write_bytes(MARKER_BYTES)
            marker_hash = _hash_file(marker)
            baseline = self.review_service.inspect_workspace(sandbox_root)
            self._event(RuntimeEventType.SANDBOX_SNAPSHOT_CREATED, count=len(baseline.files))
            emit({"event_type": "sandbox.created", "status": "running"})
            executor = ForgeXToolExecutor(sandbox_root=sandbox_root, policy=policy)
            provider_task = (
                _task_with_project_context(task, baseline)
                if getattr(planner, "provider_kind", "") == "api_planner"
                else task
            )

            for turn in range(1, bounds.max_turns + 1):
                if cancelled.is_set():
                    return self._failure(RuntimeClassification.CANCELLED, provider_id, active_unchanged=active_before == _active_integrity_snapshot(self.active_workspace_root), marker_unchanged=True, tool_execution_count=total_calls)
                if time.monotonic() - started > bounds.max_runtime_seconds:
                    return self._failure(RuntimeClassification.LIMIT_EXCEEDED, provider_id, tool_execution_count=total_calls)
                emit({"event_type": "planner.turn.started", "status": "running", "turn": turn})
                raw_plan = request_turn(
                    task=provider_task,
                    tool_results=tuple(tool_results),
                    allowed_tools=("write_file",),
                    run_id=run_id,
                    turn=turn,
                )
                plan = raw_plan if isinstance(raw_plan, ProductToolPlan) else ProductToolPlan.parse(raw_plan, max_calls=bounds.max_tool_calls_per_turn)
                if time.monotonic() - started > bounds.max_runtime_seconds:
                    return self._failure(RuntimeClassification.LIMIT_EXCEEDED, provider_id, tool_execution_count=total_calls)
                if len(plan.tool_calls) > bounds.max_tool_calls_per_turn:
                    return self._failure(RuntimeClassification.LIMIT_EXCEEDED, provider_id, tool_execution_count=total_calls)
                emit({"event_type": "planner.turn.completed", "status": "running", "turn": turn, "tool_count": len(plan.tool_calls)})
                if plan.final:
                    final_received = True
                    break
                if total_calls + len(plan.tool_calls) > bounds.max_total_tool_calls:
                    return self._failure(RuntimeClassification.LIMIT_EXCEEDED, provider_id, tool_execution_count=total_calls)

                legacy_calls = []
                for product_call in plan.tool_calls:
                    if cancelled.is_set():
                        return self._failure(RuntimeClassification.CANCELLED, provider_id, tool_execution_count=total_calls)
                    try:
                        call = product_call.as_runtime_call()
                        executor.validate(call)
                    except (ToolPolicyError, ValueError) as exc:
                        denied_seen = True
                        classification = getattr(exc, "classification", RuntimeClassification.POLICY_DENIED)
                        tool_results.append({"call_id": product_call.call_id, "status": "denied", "classification": classification.value})
                        emit({"event_type": "tool.denied", "status": "running", "classification": classification.value})
                        continue
                    result = executor.execute(call)
                    legacy_calls.append(call)
                    authorized_paths.add(product_call.path)
                    total_calls += 1
                    safe_result = {"call_id": product_call.call_id, "status": "completed", "tool": call.tool.value}
                    if isinstance(result, Mapping):
                        for key in ("created", "bytes", "modified"):
                            if key in result:
                                safe_result[key] = result[key]
                    tool_results.append(safe_result)
                    emit({"event_type": "tool.completed", "status": "running", "tool": call.tool.value, "tool_execution_count": total_calls})
                if legacy_calls:
                    executed_plans.append(ToolPlan(tuple(legacy_calls)))

            if not final_received:
                return self._failure(RuntimeClassification.MAX_TURNS, provider_id, tool_execution_count=total_calls)

            changed = self.review_service.diff_snapshot(baseline, sandbox_root)
            created = tuple(item for item in changed if item.change_type == "created")
            modified = tuple(item for item in changed if item.change_type == "modified")
            deleted = tuple(item for item in changed if item.change_type == "deleted")
            active_unchanged = active_before == _active_integrity_snapshot(self.active_workspace_root)
            marker_unchanged = marker.is_file() and not marker.is_symlink() and _hash_file(marker) == marker_hash
            if denied_seen or not active_unchanged or not marker_unchanged or any(not item.safe for item in changed):
                classification = RuntimeClassification.POLICY_DENIED if denied_seen else RuntimeClassification.UNSAFE_ABORTED
                return self._failure(classification, provider_id, created=len(created), modified=len(modified), deleted=len(deleted), active_unchanged=active_unchanged, marker_unchanged=marker_unchanged, tool_execution_count=total_calls)
            if not changed:
                # A provider may deterministically write content that already
                # exists. Hash-equivalent required output is a successful
                # generation no-op, not a provider or generation failure.
                self._event(RuntimeEventType.RUNTIME_COMPLETED, classification=RuntimeClassification.NO_CHANGES.value)
                return ToolRuntimeResult(
                    classification=RuntimeClassification.PASS,
                    provider_id=provider_id,
                    review_created=False,
                    active_workspace_unchanged=active_unchanged,
                    marker_unchanged=marker_unchanged,
                    tool_execution_count=total_calls,
                    events=tuple(self._events),
                    provider_classification=(
                        ApiPlannerClassification.SANDBOX_NO_CHANGES.value
                        if getattr(planner, "provider_kind", "") == "api_planner"
                        else RuntimeClassification.NO_CHANGES.value
                    ),
                )
            if not {item.path for item in changed}.issubset(authorized_paths):
                return self._failure(RuntimeClassification.EXTRA_CHANGES, provider_id, created=len(created), modified=len(modified), deleted=len(deleted), active_unchanged=active_unchanged, marker_unchanged=marker_unchanged, tool_execution_count=total_calls)
            if deleted or len(created) > bounds.max_created_files or len(modified) > bounds.max_modified_files:
                return self._failure(RuntimeClassification.EXTRA_CHANGES, provider_id, created=len(created), modified=len(modified), deleted=len(deleted), active_unchanged=active_unchanged, marker_unchanged=marker_unchanged, tool_execution_count=total_calls)
            for item in (*created, *modified):
                target = policy.resolve_path(sandbox_root, item.path, must_exist=True)
                if target.stat().st_size > bounds.max_bytes_per_file:
                    return self._failure(RuntimeClassification.LIMIT_EXCEEDED, provider_id, created=len(created), modified=len(modified), active_unchanged=active_unchanged, marker_unchanged=marker_unchanged, tool_execution_count=total_calls)

            review = self.review_service.create_review(
                provider_id=provider_id,
                workspace_root=sandbox_root,
                snapshot=baseline,
                artifact_source="forgex_product_agent_runtime",
                artifact_type="tool_runtime_diff",
                artifact_metadata={
                    "provider_id": provider_id, "model_id": model_id,
                    "provider_kind": getattr(planner, "provider_kind", "fake"),
                    "transport": getattr(planner, "transport_name", "in_process"),
                    "execution_mode": "forge_owned_tools", "workspace_mode": "managed_sandbox",
                    "classification": ApiPlannerClassification.SANDBOX_WRITE_PASS.value if getattr(planner, "provider_kind", "") == "api_planner" else RuntimeClassification.PASS.value,
                    "tool_count": total_calls, "created_files": len(created),
                    "modified_files": len(modified), "deleted_files": len(deleted),
                    "active_workspace_unchanged": True, "auto_apply": False,
                    "auto_build": False, "auto_flash": False, "apply_authority": "none",
                    "build_authority": "none", "flash_authority": "none",
                    "raw_prompt_persisted": False, "raw_response_persisted": False,
                },
            )
            emit({"event_type": "review.created", "status": "completed", "changed_file_count": len(changed)})
            self._event(RuntimeEventType.REVIEW_CANDIDATE_CREATED, count=len(changed))
            self._event(RuntimeEventType.RUNTIME_COMPLETED, classification=RuntimeClassification.PASS.value)
            return ToolRuntimeResult(
                classification=RuntimeClassification.PASS, provider_id=provider_id,
                review_id=review.review_id, created_file_count=len(created),
                modified_file_count=len(modified), deleted_file_count=len(deleted),
                review_created=True, active_workspace_unchanged=True, marker_unchanged=True,
                tool_execution_count=total_calls, events=tuple(self._events),
                provider_classification=ApiPlannerClassification.SANDBOX_WRITE_PASS.value if getattr(planner, "provider_kind", "") == "api_planner" else None,
            )
        except ApiPlannerError as exc:
            return self._failure(
                RuntimeClassification.PROVIDER_INVALID,
                provider_id,
                provider_classification=exc.classification,
                execution_attempted=exc.classification not in {
                    ApiPlannerClassification.DISABLED.value,
                    ApiPlannerClassification.KEY_MISSING.value,
                    ApiPlannerClassification.MODEL_MISSING.value,
                    ApiPlannerClassification.CONFIRMATION_REQUIRED.value,
                },
                tool_execution_count=total_calls,
                provider_diagnostics=_safe_provider_diagnostics(planner),
            )
        except ToolPolicyError as exc:
            return self._failure(exc.classification, provider_id, tool_execution_count=total_calls)
        except (TypeError, ValueError):
            return self._failure(RuntimeClassification.PROVIDER_INVALID, provider_id, tool_execution_count=total_calls)
        except OSError:
            return self._failure(RuntimeClassification.UNSAFE_ABORTED, provider_id, tool_execution_count=total_calls)
        except Exception:
            return self._failure(RuntimeClassification.UNKNOWN_SAFE_FAILURE, provider_id, tool_execution_count=total_calls)

    def run(self, *, task: str, provider: ApiBackedProvider, policy: ToolPermissionPolicy) -> ToolRuntimeResult:
        self._events = []
        self._event(RuntimeEventType.RUNTIME_STARTED)
        if not isinstance(task, str) or not task or len(task) > provider.max_input_tokens * 8:
            return self._failure(RuntimeClassification.PROVIDER_INVALID, provider.provider_id)

        active_before = _active_integrity_snapshot(self.active_workspace_root)
        run_id = f"tool-runtime-{uuid.uuid4().hex}"
        is_real_api = provider.provider_id == "openai_api"
        try:
            sandbox_root = self._create_sandbox(run_id)
            marker = sandbox_root / MARKER_NAME
            marker.write_bytes(MARKER_BYTES)
            marker_hash = _hash_file(marker)
            baseline = self.review_service.inspect_workspace(sandbox_root)
            self._event(RuntimeEventType.SANDBOX_SNAPSHOT_CREATED, count=len(baseline.files))

            self._event(RuntimeEventType.PROVIDER_PLAN_REQUESTED)
            if is_real_api:
                self._event(RuntimeEventType.API_PROVIDER_REQUEST_STARTED)
            raw_plan = provider.request_plan(
                task=task,
                sandbox_manifest={"files": tuple(path for path in sorted(baseline.files) if path != MARKER_NAME)},
                allowed_tools=list(policy.provider_allowed_tools),
                policy=policy.to_provider_dict(),
                run_id=run_id,
            )
            plan = raw_plan if isinstance(raw_plan, ToolPlan) else ToolPlan.parse(raw_plan)
            self._event(RuntimeEventType.PROVIDER_PLAN_RECEIVED, count=len(plan.calls))
            if is_real_api:
                self._event(RuntimeEventType.API_PROVIDER_REQUEST_COMPLETED)
                self._event(RuntimeEventType.API_PROVIDER_PLAN_PARSED, count=len(plan.calls))

            executor = ForgeXToolExecutor(sandbox_root=sandbox_root, policy=policy)
            self._event(RuntimeEventType.TOOL_VALIDATION_STARTED, count=len(plan.calls))
            if is_real_api:
                self._event(RuntimeEventType.TOOL_RUNTIME_VALIDATION_STARTED, count=len(plan.calls))
            for call in plan.calls:
                executor.validate(call)
            if is_real_api:
                self._event(RuntimeEventType.TOOL_RUNTIME_VALIDATION_COMPLETED, count=len(plan.calls))
            for call in plan.calls:
                self._event(RuntimeEventType.TOOL_EXECUTION_STARTED)
                executor.execute(call)
                self._event(RuntimeEventType.TOOL_EXECUTION_COMPLETED)
            if is_real_api:
                self._event(RuntimeEventType.TOOL_RUNTIME_EXECUTION_COMPLETED, count=len(plan.calls))

            changed = self.review_service.diff_snapshot(baseline, sandbox_root)
            self._event(RuntimeEventType.SANDBOX_DIFF_CREATED, count=len(changed))
            active_unchanged = active_before == _active_integrity_snapshot(self.active_workspace_root)
            marker_unchanged = marker.is_file() and not marker.is_symlink() and _hash_file(marker) == marker_hash
            created = tuple(item for item in changed if item.change_type == "created")
            modified = tuple(item for item in changed if item.change_type == "modified")
            deleted = tuple(item for item in changed if item.change_type == "deleted")

            classification = self._validate_exact_diff(
                policy=policy,
                sandbox_root=sandbox_root,
                changed=changed,
                active_unchanged=active_unchanged,
                marker_unchanged=marker_unchanged,
            )
            if classification is not RuntimeClassification.PASS:
                return self._failure(
                    classification,
                    provider.provider_id,
                    created=len(created),
                    modified=len(modified),
                    deleted=len(deleted),
                    active_unchanged=active_unchanged,
                    marker_unchanged=marker_unchanged,
                )

            review = self._create_review(provider.provider_id, provider.model_id, sandbox_root, baseline, plan)
            self._event(RuntimeEventType.REVIEW_CANDIDATE_CREATED, count=len(review.changed_files))
            patch_result: Mapping[str, object] | None = None
            if self.post_review_pipeline is not None:
                patch_result = dict(self.post_review_pipeline(review.review_id))
            self._event(RuntimeEventType.RUNTIME_COMPLETED, classification=RuntimeClassification.PASS.value)
            if is_real_api:
                self._event(RuntimeEventType.API_PROVIDER_SMOKE_COMPLETED, classification="API_PROVIDER_PASS")
            return ToolRuntimeResult(
                classification=RuntimeClassification.PASS,
                provider_id=provider.provider_id,
                review_id=review.review_id,
                created_file_count=len(created),
                modified_file_count=len(modified),
                deleted_file_count=len(deleted),
                review_created=True,
                active_workspace_unchanged=True,
                marker_unchanged=True,
                patch_pipeline=patch_result,
                provider_classification="API_PROVIDER_PASS" if is_real_api else None,
                execution_attempted=is_real_api,
                tool_execution_count=len(plan.calls),
                events=tuple(self._events),
            )
        except ApiProviderError as exc:
            if is_real_api:
                event = RuntimeEventType.API_PROVIDER_PLAN_INVALID if exc.classification.value in {
                    "API_PROVIDER_INVALID_JSON", "API_PROVIDER_SCHEMA_INVALID"
                } else RuntimeEventType.API_PROVIDER_REQUEST_FAILED
                self._event(event, classification=exc.classification.value)
            return self._failure(
                RuntimeClassification.PROVIDER_INVALID,
                provider.provider_id,
                provider_classification=exc.classification.value,
                execution_attempted=exc.classification.value not in {"API_PROVIDER_DISABLED", "API_PROVIDER_KEY_MISSING"},
            )
        except ToolPolicyError as exc:
            self._event(RuntimeEventType.TOOL_VALIDATION_DENIED, classification=exc.classification.value)
            return self._failure(exc.classification, provider.provider_id)
        except (PermissionError, ValueError, TypeError):
            return self._failure(RuntimeClassification.PROVIDER_INVALID, provider.provider_id)
        except OSError:
            return self._failure(RuntimeClassification.UNSAFE_ABORTED, provider.provider_id)
        except Exception:
            return self._failure(RuntimeClassification.UNKNOWN_SAFE_FAILURE, provider.provider_id)

    def _create_sandbox(self, run_id: str) -> Path:
        managed = self.managed_sandbox_root
        managed.mkdir(parents=True, exist_ok=True)
        active = self.active_workspace_root
        try:
            managed.relative_to(active)
            raise ValueError("managed_sandbox_overlaps_active_workspace")
        except ValueError as exc:
            if str(exc) == "managed_sandbox_overlaps_active_workspace":
                raise
        try:
            active.relative_to(managed)
            raise ValueError("active_workspace_overlaps_managed_sandbox")
        except ValueError as exc:
            if str(exc) == "active_workspace_overlaps_managed_sandbox":
                raise
        destination = managed / run_id
        destination.mkdir(exist_ok=False)
        for current_root, dirnames, filenames in os.walk(active, followlinks=False):
            current = Path(current_root)
            dirnames[:] = [
                name for name in dirnames
                if name.casefold() not in IGNORED_DIRS and not (current / name).is_symlink()
            ]
            for filename in filenames:
                source = current / filename
                if source.is_symlink():
                    continue
                relative = source.relative_to(active).as_posix()
                if not is_safe_relative_path(relative) or is_sensitive_path(relative):
                    continue
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        return destination

    def _validate_exact_diff(
        self,
        *,
        policy: ToolPermissionPolicy,
        sandbox_root: Path,
        changed: tuple,
        active_unchanged: bool,
        marker_unchanged: bool,
    ) -> RuntimeClassification:
        if not active_unchanged or not marker_unchanged:
            return RuntimeClassification.UNSAFE_ABORTED
        if not changed:
            return RuntimeClassification.NO_CHANGES
        if len(changed) != 1:
            return RuntimeClassification.EXTRA_CHANGES
        item = changed[0]
        if item.change_type != "created" or not item.safe or policy.expected_path is None:
            return RuntimeClassification.EXTRA_CHANGES
        if item.path != policy.expected_path:
            return RuntimeClassification.EXTRA_CHANGES
        target = policy.resolve_path(sandbox_root, item.path, must_exist=True)
        try:
            content = target.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return RuntimeClassification.CONTENT_INVALID
        if policy.expected_content is not None and content != policy.expected_content:
            return RuntimeClassification.CONTENT_INVALID
        return RuntimeClassification.PASS

    def _create_review(
        self,
        provider_id: str,
        model_id: str,
        sandbox_root: Path,
        baseline: BridgeWorkspaceSnapshot,
        plan: ToolPlan,
    ) -> BridgeReviewSession:
        return self.review_service.create_review(
            provider_id=provider_id,
            workspace_root=sandbox_root,
            snapshot=baseline,
            artifact_source="forgex_tool_runtime",
            artifact_type="tool_runtime_diff",
            artifact_metadata={
                "provider_kind": "api" if provider_id == "openai_api" else "api_backed_design",
                "provider_id": provider_id,
                "model_id": model_id,
                "runtime": "forgex_owned_tool_runtime",
                "tool_count": len(plan.calls),
                "tools_executed": ",".join(call.tool.value for call in plan.calls),
                "created_files": 1,
                "modified_files": 0,
                "deleted_files": 0,
                "apply_authority": "none",
                "build_authority": "none",
                "flash_authority": "none",
                "raw_prompt_persisted": False,
                "raw_response_persisted": False,
                "api_key_persisted": False,
            },
        )

    def _failure(
        self,
        classification: RuntimeClassification,
        provider_id: str,
        *,
        created: int = 0,
        modified: int = 0,
        deleted: int = 0,
        active_unchanged: bool = False,
        marker_unchanged: bool = False,
        provider_classification: str | None = None,
        provider_diagnostics: Mapping[str, object] | None = None,
        execution_attempted: bool = False,
        tool_execution_count: int = 0,
    ) -> ToolRuntimeResult:
        self._event(RuntimeEventType.RUNTIME_FAILED, classification=classification.value)
        return ToolRuntimeResult(
            classification=classification,
            provider_id=provider_id,
            created_file_count=created,
            modified_file_count=modified,
            deleted_file_count=deleted,
            active_workspace_unchanged=active_unchanged,
            marker_unchanged=marker_unchanged,
            provider_classification=provider_classification,
            provider_diagnostics=dict(provider_diagnostics or {}),
            execution_attempted=execution_attempted,
            tool_execution_count=tool_execution_count,
            events=tuple(self._events),
        )

    def _event(self, event_type: RuntimeEventType, *, classification: str | None = None, count: int | None = None) -> None:
        self._events.append(RuntimeEvent(event_type, len(self._events) + 1, classification, count))


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
        "outbound_request_count",
        "request_reached_provider",
        "http_status",
        "provider_request_id",
        "safe_error_code",
    }
    return {
        key: value
        for key, value in metadata.items()
        if key in allowed and isinstance(value, (str, int, bool, type(None)))
    }


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
                # Integrity is checked without opening credential-like files.
                snapshot[relative] = ("sensitive", info.st_size, info.st_mtime_ns)
            else:
                snapshot[relative] = ("file", info.st_size, info.st_mtime_ns, _hash_file(path))
    return snapshot


def _task_with_project_context(task: str, snapshot: BridgeWorkspaceSnapshot) -> str:
    """Build bounded, relative-path-only context for an API generator."""

    sections = [task.strip(), "\nWorkspace files (untrusted project context):"]
    used = sum(len(section.encode("utf-8")) for section in sections)
    limit = 96 * 1024
    for path in sorted(snapshot.files):
        if path == MARKER_NAME or is_sensitive_path(path):
            continue
        item = snapshot.files[path]
        content = item.content
        if content is None:
            sections.append(f"\n--- {path} (binary or unavailable) ---")
            continue
        block = f"\n--- {path} ---\n{content}"
        size = len(block.encode("utf-8"))
        if used + size > limit:
            sections.append("\n[Additional workspace files omitted by ForgeX context limit]")
            break
        sections.append(block)
        used += size
    sections.append("\nReturn only the complete files that must be created or replaced for the requested task.")
    return "".join(sections)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
