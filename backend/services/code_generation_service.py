"""Framework-aware firmware source generation for PromptForge AI.

The service translates an immutable ``ExecutionPlan`` into a provider-neutral
``LLMRequest``, delegates generation to an injected ``LLMService``, extracts a
strict file manifest, and validates the resulting in-memory project. It does
not write files, build firmware, execute tools, or call runtime components.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

from ..agent.planner import Framework, TargetBoard
from ..contracts.execution_context import ExecutionContext
from ..contracts.execution_plan import ExecutionPlan
from ..contracts.generated_project import (
    GeneratedFile,
    GeneratedProject as CanonicalGeneratedProject,
)
from ..core.config import (
    DEFAULT_LLM_MAX_ATTEMPTS,
    DEFAULT_LLM_RETRY_BACKOFF_SECONDS,
    DEFAULT_LLM_TIMEOUT_SECONDS,
)
from .llm_service import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMService,
    LLMTimeoutError,
)
from .generation_strategy import select_generation_strategy
from .generation_validators import strip_file_content, validate_chunk_file
from .chunked_generation_writer import ChunkedGenerationWriter, WorkspaceGenerationTransaction
from .generation_diagnostics_store import GenerationDiagnosticsStore

_NON_FALLBACK_ERROR_CODES = frozenset({
    "authentication_required",
    "authentication_error",
    "auth_error",
    "invalid_credentials",
    "missing_credentials",
    "credential_missing",
    "configuration_error",
    "policy_denied",
    "recipient_not_approved",
    "consent_required",
    "disclosure_denied",
    "unapproved_external_disclosure",
    "fallback_not_authorized",
})

__all__ = [
    "CodeGenerationError",
    "CodeGenerationRequest",
    "CodeGenerationService",
    "GenerationAttempt",
    "GeneratedFile",
    "GeneratedOutputError",
    "GeneratedProject",
    "ProjectValidationError",
    "UnsupportedGenerationTargetError",
]


class CodeGenerationError(RuntimeError):
    """Base error for deterministic code-generation failures."""

    code: ClassVar[str] = "CODE_GENERATION_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = MappingProxyType(dict(details or {}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": type(self).__name__,
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


class UnsupportedGenerationTargetError(CodeGenerationError):
    """The plan requests an unsupported board/framework combination."""

    code = "UNSUPPORTED_GENERATION_TARGET"


class GeneratedOutputError(CodeGenerationError):
    """The LLM response cannot be extracted into a file manifest."""

    code = "INVALID_GENERATED_OUTPUT"


class ProjectValidationError(CodeGenerationError):
    """An extracted project violates deterministic project rules."""

    code = "PROJECT_VALIDATION_ERROR"


@dataclass(frozen=True, slots=True)
class GenerationAttempt:
    attempt_id: str
    execution_id: str
    task_id: str
    provider_id: str
    model_id: str
    route_id: str
    prompt_hash: str
    attempt_number: int
    attempt_type: str
    started_at: str
    completed_at: str
    success: bool
    error_code: str | None = None
    error_message: str | None = None
    raw_output_preview: str | None = None
    parsed_file_count: int = 0
    required_files_present: bool = False
    validation_errors: tuple[str, ...] = ()
    fallback_used: bool = False
    repair_used: bool = False
    latency_ms: int = 0
    parse_success: bool = False
    validation_success: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "execution_id": self.execution_id,
            "task_id": self.task_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "route_id": self.route_id,
            "prompt_hash": self.prompt_hash,
            "attempt_number": self.attempt_number,
            "attempt_type": self.attempt_type,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "success": self.success,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "raw_output_preview": self.raw_output_preview,
            "parsed_file_count": self.parsed_file_count,
            "required_files_present": self.required_files_present,
            "validation_errors": list(self.validation_errors),
            "fallback_used": self.fallback_used,
            "repair_used": self.repair_used,
            "latency_ms": self.latency_ms,
            "parse_success": self.parse_success,
            "validation_success": self.validation_success,
        }


@dataclass(frozen=True, slots=True, init=False)
class GeneratedProject(CanonicalGeneratedProject):
    """Compatibility export for the canonical generated-project contract.

    New code should import from ``backend.contracts.generated_project``.
    Existing service callers may omit identity, timestamp, and metadata while
    migrating; the service supplies those canonical fields automatically.
    """

    def __init__(
        self,
        project_name: str,
        framework: str,
        target_board: str,
        files: tuple[GeneratedFile, ...],
        *,
        project_id: str | None = None,
        created_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        _validate_project_name(project_name)
        CanonicalGeneratedProject.__init__(
            self,
            project_id=project_id or f"project-{uuid.uuid4().hex}",
            project_name=project_name,
            target_board=target_board,
            framework=framework,
            files=files,
            created_at=created_at or datetime.now(timezone.utc),
            metadata={} if metadata is None else metadata,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> GeneratedProject:
        canonical = CanonicalGeneratedProject.from_dict(data)
        return cls(
            project_name=canonical.project_name,
            framework=canonical.framework,
            target_board=canonical.target_board,
            files=canonical.files,
            project_id=canonical.project_id,
            created_at=canonical.created_at,
            metadata=canonical.metadata,
        )


@dataclass(frozen=True, slots=True)
class CodeGenerationRequest:
    """Validated handoff from planning into firmware source generation."""

    plan: ExecutionPlan
    context: ExecutionContext
    generation_event_callback: Callable[[Mapping[str, Any]], object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.plan, ExecutionPlan):
            raise ValueError("plan must be an ExecutionPlan")
        if not isinstance(self.context, ExecutionContext):
            raise ValueError("context must be an ExecutionContext")
        if self.plan.task_id != self.context.task_id:
            raise ValueError("plan and context task_id values must match")
        if self.context.target_board != "UNKNOWN" and (
            self.context.target_board != _identifier_value(self.plan.target_board)
        ):
            raise ValueError("context target_board must match the plan")
        if self.context.framework != "UNKNOWN" and (
            self.context.framework != _identifier_value(self.plan.framework)
        ):
            raise ValueError("context framework must match the plan")
        if self.generation_event_callback is not None and not callable(self.generation_event_callback):
            raise ValueError("generation_event_callback must be callable")


_SUPPORTED_FRAMEWORKS: Mapping[TargetBoard, frozenset[Framework]] = (
    MappingProxyType(
        {
            TargetBoard.ESP32: frozenset(
                {Framework.ARDUINO, Framework.ESP_IDF, Framework.PLATFORMIO}
            ),
            TargetBoard.ESP32_S3: frozenset(
                {Framework.ARDUINO, Framework.ESP_IDF, Framework.PLATFORMIO}
            ),
            TargetBoard.ESP32_C3: frozenset(
                {Framework.ARDUINO, Framework.ESP_IDF, Framework.PLATFORMIO}
            ),
            TargetBoard.STM32: frozenset(
                {Framework.STM32_CUBE, Framework.PLATFORMIO}
            ),
            TargetBoard.ARDUINO_UNO: frozenset(
                {Framework.ARDUINO, Framework.PLATFORMIO}
            ),
        }
    )
)

_FRAMEWORK_GUIDANCE: Mapping[Framework, str] = MappingProxyType(
    {
        Framework.ARDUINO: (
            "Produce an Arduino sketch with exactly one primary .ino file. "
            "Use setup() and loop(), and use APIs compatible with the target."
        ),
        Framework.PLATFORMIO: (
            "Produce a PlatformIO project containing platformio.ini and "
            "src/main.cpp. Select a board environment compatible with the "
            "target and declare any required libraries."
        ),
        Framework.ESP_IDF: (
            "Produce an ESP-IDF project containing root CMakeLists.txt, "
            "main/CMakeLists.txt, and a main C or C++ source file that defines "
            "app_main()."
        ),
        Framework.STM32_CUBE: (
            "Produce STM32Cube-compatible sources containing Core/Src/main.c "
            "and Core/Inc/main.h. Preserve standard USER CODE sections where "
            "generated initialization code would normally be extended."
        ),
    }
)

_BOARD_GUIDANCE: Mapping[TargetBoard, str] = MappingProxyType(
    {
        TargetBoard.ESP32: "Target a generic ESP32 development board.",
        TargetBoard.ESP32_S3: "Target an ESP32-S3 development board.",
        TargetBoard.ESP32_C3: "Target an ESP32-C3 development board.",
        TargetBoard.STM32: (
            "Target STM32 without inventing an exact MCU or pinout unless the "
            "requirements or context metadata provide one."
        ),
        TargetBoard.ARDUINO_UNO: (
            "Target Arduino Uno with ATmega328P resource constraints."
        ),
    }
)

_PROJECT_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*(?P<body>\{.*?\})\s*```",
    re.IGNORECASE | re.DOTALL,
)
_FILE_FENCE_RE = re.compile(
    r"```file:(?P<path>[^\r\n`]+)\r?\n(?P<content>.*?)\r?\n```",
    re.IGNORECASE | re.DOTALL,
)
_FILE_MARKER_RE = re.compile(
    r"(?m)^\s*(?:File|Path):\s*(?P<path>[^\r\n]+)\r?\n(?P<content>.*?)(?=^\s*(?:File|Path):\s*[^\r\n]+\r?$|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_HEADING_RE = re.compile(r"^\s*#{2,6}\s+(?P<path>\S[^\r\n]*)\s*$")
_FILE_LIKE_HEADING_RE = re.compile(
    r"^(?:platformio\.ini|(?:src|include|lib|test|main|Core|core)/[^<>:\"|?*\r\n]+|CMakeLists\.txt)$",
    re.IGNORECASE,
)
_PLATFORMIO_BOARD_RE = re.compile(
    r"(?mi)^\s*board\s*=\s*(?P<board>[^\s;#]+)"
)
_PLATFORMIO_BOARD_RULES: Mapping[TargetBoard, re.Pattern[str]] = (
    MappingProxyType(
        {
            TargetBoard.ESP32: re.compile(
                r"^(?!.*(?:s3|c3)).*(?:esp32|wroom|wrover)", re.I
            ),
            TargetBoard.ESP32_S3: re.compile(r"(?:esp32[-_]?s3|s3)", re.I),
            TargetBoard.ESP32_C3: re.compile(r"(?:esp32[-_]?c3|c3)", re.I),
            TargetBoard.ARDUINO_UNO: re.compile(r"(?:^|[-_])uno(?:$|[-_])", re.I),
        }
    )
)

_BUILTIN_PLATFORMIO_BOARDS: Mapping[TargetBoard, tuple[str, str, int]] = MappingProxyType(
    {
        TargetBoard.ESP32: ("espressif32", "esp32dev", 2),
        TargetBoard.ESP32_S3: ("espressif32", "esp32-s3-devkitc-1", 2),
        TargetBoard.ESP32_C3: ("espressif32", "esp32-c3-devkitm-1", 8),
        TargetBoard.ARDUINO_UNO: ("atmelavr", "uno", 13),
    }
)

logger = logging.getLogger(__name__)


class CodeGenerationService:
    """Convert execution plans into validated generated firmware projects."""

    MAX_FILES = 64
    MAX_FILE_BYTES = 512_000
    MAX_PROJECT_BYTES = 2_000_000

    def __init__(
        self,
        llm_service: LLMService,
        *,
        temperature: float = 0.2,
        max_tokens: int = 8192,
        timeout_s: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_LLM_MAX_ATTEMPTS,
        retry_backoff_s: float = DEFAULT_LLM_RETRY_BACKOFF_SECONDS,
        max_repair_attempts: int = 1,
        fallback_llm_service: LLMService | None = None,
        fallback_enabled: bool = False,
        diagnostics_store: GenerationDiagnosticsStore | None = None,
    ) -> None:
        if not isinstance(llm_service, LLMService):
            raise ValueError("llm_service must implement LLMService")
        if (
            not isinstance(temperature, (int, float))
            or isinstance(temperature, bool)
            or not 0.0 <= float(temperature) <= 2.0
        ):
            raise ValueError("temperature must be between 0.0 and 2.0")
        if (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or max_tokens <= 0
        ):
            raise ValueError("max_tokens must be a positive integer")
        if (
            not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or not math.isfinite(float(timeout_s))
            or timeout_s <= 0
        ):
            raise ValueError("timeout_s must be a positive finite number")
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or not 1 <= max_attempts <= 5
        ):
            raise ValueError("max_attempts must be an integer between 1 and 5")
        if (
            not isinstance(retry_backoff_s, (int, float))
            or isinstance(retry_backoff_s, bool)
            or not math.isfinite(float(retry_backoff_s))
            or not 0 <= float(retry_backoff_s) <= 60
        ):
            raise ValueError("retry_backoff_s must be between 0 and 60 seconds")
        if (
            not isinstance(max_repair_attempts, int)
            or isinstance(max_repair_attempts, bool)
            or not 0 <= max_repair_attempts <= 2
        ):
            raise ValueError("max_repair_attempts must be an integer between 0 and 2")
        if fallback_llm_service is not None and not isinstance(fallback_llm_service, LLMService):
            raise ValueError("fallback_llm_service must implement LLMService")
        self._llm_service = llm_service
        self._fallback_llm_service = fallback_llm_service
        self._fallback_enabled = bool(fallback_enabled)
        self.max_repair_attempts = max_repair_attempts
        self._temperature = float(temperature)
        self._max_tokens = max_tokens
        self.timeout_s = float(timeout_s)
        self.max_attempts = max_attempts
        self.retry_backoff_s = float(retry_backoff_s)
        self._attempt_records: list[GenerationAttempt] = []
        self._diagnostics_store = diagnostics_store

    def list_attempts(
        self,
        *,
        task_id: str | None = None,
        execution_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if self._diagnostics_store is not None:
            return self._diagnostics_store.list_attempts(
                task_id=task_id,
                execution_id=execution_id,
                limit=limit,
            )
        records = self._attempt_records
        if task_id is not None:
            records = [item for item in records if item.task_id == task_id]
        if execution_id is not None:
            records = [item for item in records if item.execution_id == execution_id]
        return [item.to_dict() for item in records[-max(1, min(limit, 1000)):]]

    def list_chunked_runs(
        self,
        *,
        execution_id: str | None = None,
        project_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if self._diagnostics_store is None:
            return []
        return self._diagnostics_store.list_chunked_runs(
            execution_id=execution_id,
            project_id=project_id,
            limit=limit,
        )

    def clear_generation_diagnostics(self) -> None:
        if self._diagnostics_store is not None:
            self._diagnostics_store.clear()

    def _record_attempt(self, attempts: list[GenerationAttempt], record: GenerationAttempt) -> None:
        attempts.append(record)
        self._attempt_records.append(record)
        if self._diagnostics_store is not None:
            self._diagnostics_store.record_attempt(record.to_dict())

    async def generate_project(
        self,
        request: CodeGenerationRequest,
    ) -> GeneratedProject:
        """Generate and validate an in-memory project from ``request``."""

        if not isinstance(request, CodeGenerationRequest):
            raise TypeError("request must be a CodeGenerationRequest")
        self._validate_supported_target(request.plan)
        target_board = _target_board(request.plan.target_board)
        framework = _framework(request.plan.framework)

        project_name = _derive_project_name(request)
        attempts: list[GenerationAttempt] = []
        base_prompt = self._build_prompt(request, project_name=project_name)
        system_prompt = self._build_system_prompt(request.plan)
        strategy = select_generation_strategy(
            prompt_text=_request_text_for_strategy(request),
            model_id=str(getattr(self._llm_service, "model", "")),
            task_type=request.plan.task_type.value,
            expected_file_count=_expected_file_count(request),
            previous_failure_reason=None,
        )
        forced_strategy = request.context.metadata.get("generation_strategy")
        selected_strategy = forced_strategy if forced_strategy in {"one_shot", "chunked"} else strategy.strategy
        await _emit_generation_event(
            request,
            "generation_strategy_selected",
            generation_mode=selected_strategy,
            strategy=selected_strategy,
            status="selected",
            provider_id=_provider_id(self._llm_service),
            model_id=str(getattr(self._llm_service, "model", "")),
            message=f"Strategy selected: {selected_strategy}",
            strategy_reasons=list(getattr(strategy, "reasons", ())),
        )
        if _should_use_builtin_blink(
            request,
            target_board,
            framework,
            self._llm_service,
        ):
            started_at = _utc_now()
            project = _build_builtin_blink_project(
                request,
                project_name=project_name,
                target_board=target_board,
                framework=framework,
            )
            self.validate_project(project)
            _validate_prompt_specific_project(project, request)
            completed_at = _utc_now()
            attempt = GenerationAttempt(
                attempt_id=f"attempt-{uuid.uuid4().hex}",
                execution_id=_execution_id(request),
                task_id=request.plan.task_id,
                provider_id="forgex_builtin",
                model_id="deterministic-blink-v1",
                route_id=str(request.context.metadata.get("route_id", "code_generation")),
                prompt_hash=_hash_prompt(base_prompt, system_prompt),
                attempt_number=1,
                attempt_type="builtin_template",
                started_at=started_at,
                completed_at=completed_at,
                success=True,
                parsed_file_count=len(project.files),
                required_files_present=True,
                fallback_used=True,
                latency_ms=0,
                parse_success=True,
                validation_success=True,
            )
            self._record_attempt(attempts, attempt)
            await _emit_generation_event(
                request,
                "generation_started",
                generation_mode="builtin_template",
                strategy="builtin_template",
                status="generating",
                provider_id="forgex_builtin",
                model_id="deterministic-blink-v1",
                message="Generating verified PlatformIO blink scaffold",
            )
            result = self._with_generation_report(project, attempts, request)
            await _emit_generation_event(
                request,
                "generation_completed",
                generation_mode="builtin_template",
                strategy="builtin_template",
                status="success",
                provider_id="forgex_builtin",
                model_id="deterministic-blink-v1",
                fallback_used=True,
                message=f"Generation completed from verified scaffold: {len(project.files)} files",
                files_written=len(project.files),
                files_total=len(project.files),
            )
            return result
        if selected_strategy == "chunked":
            workspace_root = _chunked_workspace_root(request)
            transaction = (
                WorkspaceGenerationTransaction(workspace_root)
                if workspace_root is not None and workspace_root.is_dir()
                else None
            )
            try:
                project = await self._generate_chunked_project(
                    request,
                    project_name=project_name,
                    target_board=target_board,
                    framework=framework,
                    system_prompt=system_prompt,
                    attempts=attempts,
                    strategy=strategy,
                    transaction=transaction,
                )
            except Exception as exc:
                rolled_back = transaction.rollback() if transaction is not None else False
                await _emit_generation_event(
                    request,
                    "workspace_rolled_back",
                    generation_mode="chunked",
                    strategy="chunked",
                    status="rolled_back",
                    provider_id=_provider_id(self._llm_service),
                    model_id=str(getattr(self._llm_service, "model", "")),
                    rollback_performed=rolled_back,
                    message="Restored workspace after failed generation",
                )
                if (
                    isinstance(exc, LLMError)
                    and self._builtin_fallback_allowed(request, exc)
                    and _supports_builtin_wifi_monitor(request, target_board, framework)
                ):
                    return await self._generate_builtin_wifi_monitor_fallback(
                        request,
                        project_name=project_name,
                        target_board=target_board,
                        framework=framework,
                        attempts=attempts,
                        cause=exc,
                    )
                raise
            if transaction is not None:
                transaction.commit()
            result = self._with_generation_report(project, attempts, request)
            await _emit_generation_event(
                request,
                "generation_completed",
                generation_mode="chunked",
                strategy="chunked",
                status="success",
                provider_id=_provider_id(self._llm_service),
                model_id=str(getattr(self._llm_service, "model", "")),
                message=f"Generation completed: {len(project.files)}/{len(project.files)} files",
                files_written=len(project.files),
                files_total=len(project.files),
            )
            return result
        await _emit_generation_event(
            request,
            "generation_started",
            generation_mode="one_shot",
            strategy="one_shot",
            status="generating",
            provider_id=_provider_id(self._llm_service),
            model_id=str(getattr(self._llm_service, "model", "")),
            message="Generating project",
        )
        try:
            project = await self._generate_project_attempt(
                request,
                project_name=project_name,
                target_board=target_board,
                framework=framework,
                prompt=base_prompt,
                system_prompt=system_prompt,
                attempt_number=1,
                attempt_type="initial",
                attempts=attempts,
                repair_used=False,
                fallback_used=False,
                llm_service=self._llm_service,
            )
            result = self._with_generation_report(project, attempts, request)
            await _emit_generation_event(
                request,
                "generation_completed",
                generation_mode="one_shot",
                strategy="one_shot",
                status="success",
                provider_id=_provider_id(self._llm_service),
                model_id=str(getattr(self._llm_service, "model", "")),
                message=f"Generation completed: {len(project.files)} files",
                files_written=len(project.files),
                files_total=len(project.files),
            )
            return result
        except LLMError as provider_error:
            if (
                self._builtin_fallback_allowed(request, provider_error)
                and _supports_builtin_wifi_monitor(request, target_board, framework)
            ):
                return await self._generate_builtin_wifi_monitor_fallback(
                    request,
                    project_name=project_name,
                    target_board=target_board,
                    framework=framework,
                    attempts=attempts,
                    cause=provider_error,
                )
            raise
        except (GeneratedOutputError, ProjectValidationError) as initial_error:
            last_error: CodeGenerationError = initial_error

        if self.max_repair_attempts > 0:
            repair_prompt = self._build_repair_prompt(
                request,
                project_name=project_name,
                previous_error=last_error,
            )
            try:
                project = await self._generate_project_attempt(
                    request,
                    project_name=project_name,
                    target_board=target_board,
                    framework=framework,
                    prompt=repair_prompt,
                    system_prompt=system_prompt,
                    attempt_number=len(attempts) + 1,
                    attempt_type="repair",
                    attempts=attempts,
                    repair_used=True,
                    fallback_used=False,
                    llm_service=self._llm_service,
                )
                result = self._with_generation_report(project, attempts, request)
                await _emit_generation_event(
                    request,
                    "generation_completed",
                    generation_mode="one_shot",
                    strategy="repair",
                    status="success",
                    provider_id=_provider_id(self._llm_service),
                    model_id=str(getattr(self._llm_service, "model", "")),
                    repair_used=True,
                    message=f"Generation completed after repair: {len(project.files)} files",
                    files_written=len(project.files),
                    files_total=len(project.files),
                )
                return result
            except (GeneratedOutputError, ProjectValidationError) as repair_error:
                last_error = repair_error

        if self._fallback_allowed(request):
            try:
                project = await self._generate_project_attempt(
                    request,
                    project_name=project_name,
                    target_board=target_board,
                    framework=framework,
                    prompt=self._build_fallback_prompt(
                        request,
                        project_name=project_name,
                        previous_error=last_error,
                    ),
                    system_prompt=system_prompt,
                    attempt_number=len(attempts) + 1,
                    attempt_type="fallback",
                    attempts=attempts,
                    repair_used=self.max_repair_attempts > 0,
                    fallback_used=True,
                    llm_service=self._fallback_llm_service or self._llm_service,
                )
                result = self._with_generation_report(project, attempts, request)
                await _emit_generation_event(
                    request,
                    "generation_completed",
                    generation_mode="one_shot",
                    strategy="fallback",
                    status="success",
                    provider_id=_provider_id(self._fallback_llm_service or self._llm_service),
                    model_id=str(getattr(self._fallback_llm_service or self._llm_service, "model", "")),
                    repair_used=self.max_repair_attempts > 0,
                    fallback_used=True,
                    message=f"Generation completed with fallback: {len(project.files)} files",
                    files_written=len(project.files),
                    files_total=len(project.files),
                )
                return result
            except (GeneratedOutputError, ProjectValidationError) as fallback_error:
                last_error = fallback_error

        details = dict(getattr(last_error, "details", {}))
        details["generation_attempts"] = [attempt.to_dict() for attempt in attempts]
        details["suggestion"] = "Try a stronger model or enable fallback"
        await _emit_generation_event(
            request,
            "generation_failed",
            generation_mode="one_shot",
            strategy="one_shot",
            status="failed",
            provider_id=_provider_id(self._llm_service),
            model_id=str(getattr(self._llm_service, "model", "")),
            message=last_error.message,
            error_code=last_error.code,
        )
        raise type(last_error)(
            f"{last_error.message} Suggestion: Try a stronger model or enable fallback.",
            details=details,
        ) from last_error

    async def _generate_builtin_wifi_monitor_fallback(
        self,
        request: CodeGenerationRequest,
        *,
        project_name: str,
        target_board: TargetBoard,
        framework: Framework,
        attempts: list[GenerationAttempt],
        cause: LLMError,
    ) -> GeneratedProject:
        await _emit_generation_event(
            request,
            "fallback_started",
            generation_mode="builtin_template",
            strategy="deterministic_fallback",
            status="generating",
            provider_id="forgex_builtin",
            model_id="deterministic-esp32-wifi-monitor-v1",
            fallback_used=True,
            fallback_kind="deterministic_template",
            error_code=cause.code,
            message="Provider unavailable; generating verified ESP32 Wi-Fi monitor fallback",
        )
        started_at = _utc_now()
        project = _build_builtin_wifi_monitor_project(
            request,
            project_name=project_name,
            target_board=target_board,
            framework=framework,
        )
        self.validate_project(project)
        _validate_prompt_specific_project(project, request)
        self._record_attempt(
            attempts,
            GenerationAttempt(
                attempt_id=f"attempt-{uuid.uuid4().hex}",
                execution_id=_execution_id(request),
                task_id=request.plan.task_id,
                provider_id="forgex_builtin",
                model_id="deterministic-esp32-wifi-monitor-v1",
                route_id=str(request.context.metadata.get("route_id", "code_generation")),
                prompt_hash=_hash_prompt(_request_text(request), "deterministic_fallback"),
                attempt_number=len(attempts) + 1,
                attempt_type="builtin_fallback",
                started_at=started_at,
                completed_at=_utc_now(),
                success=True,
                parsed_file_count=len(project.files),
                required_files_present=True,
                fallback_used=True,
                latency_ms=0,
                parse_success=True,
                validation_success=True,
            ),
        )
        result = self._with_generation_report(project, attempts, request)
        await _emit_generation_event(
            request,
            "fallback_completed",
            generation_mode="builtin_template",
            strategy="deterministic_fallback",
            status="success",
            provider_id="forgex_builtin",
            model_id="deterministic-esp32-wifi-monitor-v1",
            fallback_used=True,
            fallback_kind="deterministic_template",
            message=f"Verified fallback generated: {len(project.files)} files",
            files_written=len(project.files),
            files_total=len(project.files),
        )
        return result

    async def _generate_project_attempt(
        self,
        request: CodeGenerationRequest,
        *,
        project_name: str,
        target_board: TargetBoard,
        framework: Framework,
        prompt: str,
        system_prompt: str,
        attempt_number: int,
        attempt_type: str,
        attempts: list[GenerationAttempt],
        repair_used: bool,
        fallback_used: bool,
        llm_service: LLMService,
    ) -> GeneratedProject:
        started = time.monotonic()
        started_at = _utc_now()
        metadata = {
            "task_id": request.plan.task_id,
            "project_name": project_name,
            "target_board": target_board.value,
            "framework": framework.value,
            "task_type": "code_generation",
            "attempt_type": attempt_type,
            "attempt_number": attempt_number,
            "allow_fallback": _metadata_bool(request.context.metadata, "fallback_enabled", False) and not fallback_used,
            "local_only": _metadata_bool(request.context.metadata, "local_only", False),
            **_fallback_authorization_metadata(request.context.metadata),
        }
        skip_provider = request.context.metadata.get("skip_provider_id")
        if isinstance(skip_provider, str) and skip_provider.strip():
            metadata["skip_provider_id"] = skip_provider
        if fallback_used:
            if attempts:
                metadata["skip_provider_id"] = attempts[0].provider_id
            metadata["force_fallback"] = True
        llm_request = LLMRequest(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            metadata=metadata,
        )
        response: LLMResponse | None = None
        parsed_file_count = 0
        required_files_present = False
        parse_success = False
        validation_success = False
        error_code: str | None = None
        error_message: str | None = None
        validation_errors: tuple[str, ...] = ()
        try:
            response = await self._generate_with_retries(llm_request, llm_service=llm_service)
            files = self.extract_files(response.content)
            parsed_file_count = len(files)
            parse_success = True
            required_files_present = _required_files_present(framework, files, request)
            project = GeneratedProject(
                project_name=project_name,
                framework=framework,
                target_board=target_board,
                files=files,
                metadata={"task_id": request.plan.task_id},
            )
            self.validate_project(project)
            _validate_prompt_specific_project(project, request)
            validation_success = True
            return project
        except (GeneratedOutputError, ProjectValidationError) as exc:
            error_code = exc.code
            error_message = exc.message
            validation_errors = (exc.message,)
            raise
        except LLMError as exc:
            error_code = exc.code
            error_message = exc.message
            validation_errors = (exc.message,)
            raise
        finally:
            completed_at = _utc_now()
            provider_id = (
                _provider_id_from_response(response)
                if response is not None
                else _provider_id(llm_service)
            )
            model_id = (
                response.model
                if response is not None and response.model
                else getattr(llm_service, "model", "unknown")
            )
            record = GenerationAttempt(
                attempt_id=f"attempt-{uuid.uuid4().hex}",
                execution_id=_execution_id(request),
                task_id=request.plan.task_id,
                provider_id=provider_id,
                model_id=str(model_id),
                route_id=str(request.context.metadata.get("route_id", "code_generation")),
                prompt_hash=_hash_prompt(prompt, system_prompt),
                attempt_number=attempt_number,
                attempt_type=attempt_type,
                started_at=started_at,
                completed_at=completed_at,
                success=validation_success,
                error_code=error_code,
                error_message=error_message,
                raw_output_preview=_preview(response.content) if response is not None else None,
                parsed_file_count=parsed_file_count,
                required_files_present=required_files_present,
                validation_errors=validation_errors,
                fallback_used=fallback_used,
                repair_used=repair_used,
                latency_ms=max(0, int((time.monotonic() - started) * 1000)),
                parse_success=parse_success,
                validation_success=validation_success,
            )
            self._record_attempt(attempts, record)

    async def _generate_chunked_project(
        self,
        request: CodeGenerationRequest,
        *,
        project_name: str,
        target_board: TargetBoard,
        framework: Framework,
        system_prompt: str,
        attempts: list[GenerationAttempt],
        strategy: object,
        transaction: WorkspaceGenerationTransaction | None = None,
    ) -> GeneratedProject:
        run_id = uuid.uuid4().hex
        run_started_at = _utc_now()
        workspace_root = _chunked_workspace_root(request)
        writer: ChunkedGenerationWriter | None = None
        if workspace_root is not None and workspace_root.exists() and workspace_root.is_dir():
            writer = ChunkedGenerationWriter(workspace_root, transaction=transaction)

        await _emit_generation_event(
            request,
            "requirements_extraction_started",
            generation_mode="chunked",
            strategy="chunked",
            status="extracting",
            provider_id=_provider_id(self._llm_service),
            model_id=str(getattr(self._llm_service, "model", "")),
            message="Extracting requirements",
        )
        summary = await self._generate_chunk_json(
            request,
            prompt=_chunk_requirements_prompt(request, project_name),
            system_prompt="Return only compact JSON. No markdown. No explanation.",
            attempt_type="requirements",
            attempt_number=len(attempts) + 1,
            attempts=attempts,
            llm_service=self._llm_service,
        )
        await _emit_generation_event(
            request,
            "requirements_extracted",
            generation_mode="chunked",
            strategy="chunked",
            status="extracted",
            provider_id=_provider_id(self._llm_service),
            model_id=str(getattr(self._llm_service, "model", "")),
            message="Requirements extracted",
            required_files=summary.get("required_files", []),
        )
        await _emit_generation_event(
            request,
            "manifest_generation_started",
            generation_mode="chunked",
            strategy="chunked",
            status="generating_manifest",
            provider_id=_provider_id(self._llm_service),
            model_id=str(getattr(self._llm_service, "model", "")),
            message="Creating manifest",
        )
        manifest = await self._generate_chunk_json(
            request,
            prompt=_chunk_manifest_prompt(request, project_name, summary),
            system_prompt="Return only compact JSON. No markdown. Do not include file contents.",
            attempt_type="manifest",
            attempt_number=len(attempts) + 1,
            attempts=attempts,
            llm_service=self._llm_service,
        )
        file_entries = _manifest_files(manifest, request)
        await _emit_generation_event(
            request,
            "manifest_created",
            generation_mode="chunked",
            strategy="chunked",
            status="manifest_created",
            provider_id=_provider_id(self._llm_service),
            model_id=str(getattr(self._llm_service, "model", "")),
            message=f"Manifest created: {len(file_entries)} files",
            files_total=len(file_entries),
            file_paths=[entry["path"] for entry in file_entries],
        )
        files: list[GeneratedFile] = []
        file_statuses: list[dict[str, Any]] = []
        failed_files: list[str] = []
        pending_files = [entry["path"] for entry in file_entries]

        for entry in file_entries:
            path = entry["path"]
            pending_files = [item for item in pending_files if item != path]
            attempts_before_file = len(attempts)
            try:
                generated = await self._generate_chunk_file(
                    request,
                    summary=summary,
                    manifest=manifest,
                    file_entry=entry,
                    existing_files=tuple(files),
                    attempt_number=len(attempts) + 1,
                    attempts=attempts,
                )
            except ProjectValidationError as exc:
                failed_files.append(path)
                file_statuses.append(
                    {
                        "path": path,
                        "status": "failed",
                        "errors": list(exc.details.get("validation_errors", ())),
                    }
                )
                await _emit_generation_event(
                    request,
                    "file_failed",
                    generation_mode="chunked",
                    strategy="chunked",
                    file_path=path,
                    status="failed",
                    provider_id=_provider_id(self._llm_service),
                    model_id=str(getattr(self._llm_service, "model", "")),
                    message=f"Failed {path}: {exc.message}",
                    errors=list(exc.details.get("validation_errors", (exc.message,))),
                    files_written=len(files),
                    files_total=len(file_entries),
                )
                details = {
                    "failed_files": failed_files,
                    "pending_files": pending_files,
                    "file_statuses": file_statuses,
                    "generation_attempts": [attempt.to_dict() for attempt in attempts],
                }
                self._record_chunked_run(
                    {
                        "run_id": run_id,
                        "execution_id": _execution_id(request),
                        "task_id": request.plan.task_id,
                        "project_id": None,
                        "workspace_root": str(workspace_root) if workspace_root is not None else "",
                        "generation_mode": "chunked",
                        "strategy": "chunked",
                        "provider_id": _provider_id(self._llm_service),
                        "model_id": str(getattr(self._llm_service, "model", "")),
                        "started_at": run_started_at,
                        "completed_at": _utc_now(),
                        "status": "incomplete",
                        "required_files": [entry["path"] for entry in file_entries],
                        "generated_files": [item.path for item in files],
                        "failed_files": list(failed_files),
                        "pending_files": list(pending_files),
                        "file_statuses": list(file_statuses),
                        "repair_count": _attempt_count(attempts, "file_repair", success_only=True),
                        "fallback_count": _attempt_count(attempts, "file_fallback", success_only=True),
                        "warning_count": 0,
                        "build_started": False,
                        "build_success": False,
                    }
                )
                await _emit_generation_event(
                    request,
                    "generation_incomplete",
                    generation_mode="chunked",
                    strategy="chunked",
                    status="incomplete",
                    provider_id=_provider_id(self._llm_service),
                    model_id=str(getattr(self._llm_service, "model", "")),
                    message=f"Generation incomplete: failed {path}",
                    failed_files=list(failed_files),
                    pending_files=list(pending_files),
                    files_written=len(files),
                    files_total=len(file_entries),
                )
                raise ProjectValidationError(
                    f"Chunked generation failed while generating {path}: {exc.message}",
                    details=details,
                ) from exc
            latest = attempts[-1]
            write_data: dict[str, Any] = {
                "path": path,
                "status": "written",
                "provider_id": latest.provider_id,
                "model_id": latest.model_id,
                "repair_used": latest.repair_used,
                "fallback_used": latest.fallback_used,
                "attempt_count": max(1, len(attempts) - attempts_before_file),
            }
            try:
                if writer is not None:
                    write_data.update(writer.write_file(generated).to_dict())
                    await _emit_generation_event(
                        request,
                        "file_written",
                        generation_mode="chunked",
                        strategy="chunked",
                        file_path=path,
                        status="written",
                        provider_id=latest.provider_id,
                        model_id=latest.model_id,
                        attempt_number=latest.attempt_number,
                        repair_used=latest.repair_used,
                        fallback_used=latest.fallback_used,
                        message=f"Written {path}",
                        files_written=len(files) + 1,
                        files_total=len(file_entries),
                        bytes_written=write_data.get("bytes_written", write_data.get("bytes")),
                    )
            except ValueError as exc:
                failed_files.append(path)
                file_statuses.append(
                    {
                        "path": path,
                        "status": "failed",
                        "errors": [str(exc)],
                    }
                )
                await _emit_generation_event(
                    request,
                    "file_failed",
                    generation_mode="chunked",
                    strategy="chunked",
                    file_path=path,
                    status="failed",
                    provider_id=latest.provider_id,
                    model_id=latest.model_id,
                    attempt_number=latest.attempt_number,
                    repair_used=latest.repair_used,
                    fallback_used=latest.fallback_used,
                    message=f"Failed writing {path}: {exc}",
                    errors=[str(exc)],
                    files_written=len(files),
                    files_total=len(file_entries),
                )
                self._record_chunked_run(
                    {
                        "run_id": run_id,
                        "execution_id": _execution_id(request),
                        "task_id": request.plan.task_id,
                        "project_id": None,
                        "workspace_root": str(workspace_root) if workspace_root is not None else "",
                        "generation_mode": "chunked",
                        "strategy": "chunked",
                        "provider_id": _provider_id(self._llm_service),
                        "model_id": str(getattr(self._llm_service, "model", "")),
                        "started_at": run_started_at,
                        "completed_at": _utc_now(),
                        "status": "incomplete",
                        "required_files": [entry["path"] for entry in file_entries],
                        "generated_files": [item.path for item in files],
                        "failed_files": list(failed_files),
                        "pending_files": list(pending_files),
                        "file_statuses": list(file_statuses),
                        "repair_count": _attempt_count(attempts, "file_repair", success_only=True),
                        "fallback_count": _attempt_count(attempts, "file_fallback", success_only=True),
                        "warning_count": 0,
                        "build_started": False,
                        "build_success": False,
                    }
                )
                await _emit_generation_event(
                    request,
                    "generation_incomplete",
                    generation_mode="chunked",
                    strategy="chunked",
                    status="incomplete",
                    provider_id=latest.provider_id,
                    model_id=latest.model_id,
                    message=f"Generation incomplete: failed writing {path}",
                    failed_files=list(failed_files),
                    pending_files=list(pending_files),
                    files_written=len(files),
                    files_total=len(file_entries),
                )
                raise ProjectValidationError(
                    f"Chunked generation failed while writing {path}: {exc}",
                    details={
                        "failed_files": failed_files,
                        "pending_files": pending_files,
                        "file_statuses": file_statuses,
                    },
                ) from exc
            file_statuses.append(write_data)
            files.append(generated)

        project = GeneratedProject(
            project_name=project_name,
            framework=framework,
            target_board=target_board,
            files=tuple(files),
            metadata={
                "task_id": request.plan.task_id,
                "chunked_generation": {
                    "run_id": run_id,
                    "strategy": "chunked",
                    "strategy_reasons": list(getattr(strategy, "reasons", ())),
                    "incremental_writes": writer is not None,
                    "workspace_root": str(workspace_root) if workspace_root is not None else "",
                    "requirements": summary,
                    "manifest": manifest,
                    "file_statuses": file_statuses,
                    "failed_files": failed_files,
                    "pending_files": pending_files,
                },
            },
        )
        self.validate_project(project)
        _validate_prompt_specific_project(project, request)
        self._record_chunked_run(
            {
                "run_id": run_id,
                "execution_id": _execution_id(request),
                "task_id": request.plan.task_id,
                "project_id": project.project_id,
                "workspace_root": str(workspace_root) if workspace_root is not None else "",
                "generation_mode": "chunked",
                "strategy": "chunked",
                "provider_id": _provider_id(self._llm_service),
                "model_id": str(getattr(self._llm_service, "model", "")),
                "started_at": run_started_at,
                "completed_at": _utc_now(),
                "status": "success",
                "required_files": [entry["path"] for entry in file_entries],
                "generated_files": [item.path for item in files],
                "failed_files": [],
                "pending_files": [],
                "file_statuses": list(file_statuses),
                "repair_count": _attempt_count(attempts, "file_repair", success_only=True),
                "fallback_count": _attempt_count(attempts, "file_fallback", success_only=True),
                "warning_count": 0,
                "build_started": False,
                "build_success": False,
            }
        )
        return project

    def _record_chunked_run(self, run: Mapping[str, Any]) -> None:
        if self._diagnostics_store is not None:
            self._diagnostics_store.record_chunked_run(run)

    async def _generate_chunk_json(
        self,
        request: CodeGenerationRequest,
        *,
        prompt: str,
        system_prompt: str,
        attempt_type: str,
        attempt_number: int,
        attempts: list[GenerationAttempt],
        llm_service: LLMService,
    ) -> Mapping[str, Any]:
        started = time.monotonic()
        started_at = _utc_now()
        response: LLMResponse | None = None
        success = False
        error_code: str | None = None
        error_message: str | None = None
        try:
            response = await self._generate_with_retries(
                LLMRequest(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=self._temperature,
                    max_tokens=min(self._max_tokens, 2048),
                    metadata={
                        "task_id": request.plan.task_id,
                        "task_type": "code_generation",
                        "attempt_type": attempt_type,
                        "attempt_number": attempt_number,
                        "allow_fallback": False,
                        "local_only": _metadata_bool(request.context.metadata, "local_only", False),
                        **_fallback_authorization_metadata(request.context.metadata),
                    },
                ),
                llm_service=llm_service,
            )
            payload = _json_mapping_from_text(response.content)
            success = True
            return payload
        except (GeneratedOutputError, LLMError) as exc:
            error_code = getattr(exc, "code", type(exc).__name__)
            error_message = getattr(exc, "message", str(exc))
            raise
        finally:
            self._record_attempt(
                attempts,
                _attempt_record(
                    request,
                    llm_service,
                    response,
                    prompt,
                    system_prompt,
                    attempt_number=attempt_number,
                    attempt_type=attempt_type,
                    started=started,
                    started_at=started_at,
                    success=success,
                    error_code=error_code,
                    error_message=error_message,
                    parsed_file_count=0,
                    required_files_present=False,
                    repair_used=False,
                    fallback_used=False,
                    parse_success=success,
                    validation_success=success,
                )
            )

    async def _generate_chunk_file(
        self,
        request: CodeGenerationRequest,
        *,
        summary: Mapping[str, Any],
        manifest: Mapping[str, Any],
        file_entry: Mapping[str, str],
        existing_files: tuple[GeneratedFile, ...],
        attempt_number: int,
        attempts: list[GenerationAttempt],
    ) -> GeneratedFile:
        path = file_entry["path"]
        advanced = _advanced_prompt(request)
        last_error: ProjectValidationError | None = None
        for attempt_type, repair_used, fallback_used, service in (
            ("file", False, False, self._llm_service),
            ("file_repair", True, False, self._llm_service),
            ("file_fallback", True, True, self._fallback_llm_service or self._llm_service),
        ):
            if fallback_used and not self._fallback_allowed(request):
                continue
            prompt = _chunk_file_prompt(
                request,
                summary=summary,
                manifest=manifest,
                path=path,
                purpose=file_entry.get("purpose", ""),
                existing_files=existing_files,
                repair_error=last_error.message if last_error else None,
            )
            event_type = {
                "file": "file_generation_started",
                "file_repair": "file_generation_repair_started",
                "file_fallback": "file_generation_fallback_started",
            }[attempt_type]
            event_message = {
                "file": f"Generating {path}",
                "file_repair": f"Repairing {path}" + (f": {last_error.message}" if last_error else ""),
                "file_fallback": f"Fallback used for {path}",
            }[attempt_type]
            await _emit_generation_event(
                request,
                event_type,
                generation_mode="chunked",
                strategy="chunked",
                file_path=path,
                status="generating" if attempt_type == "file" else "repairing" if attempt_type == "file_repair" else "fallback",
                provider_id=_provider_id(service),
                model_id=str(getattr(service, "model", "")),
                attempt_number=attempt_number,
                repair_used=repair_used,
                fallback_used=fallback_used,
                message=event_message,
            )
            started = time.monotonic()
            started_at = _utc_now()
            response: LLMResponse | None = None
            success = False
            error_code: str | None = None
            error_message: str | None = None
            validation_errors: tuple[str, ...] = ()
            try:
                response = await self._generate_with_retries(
                    LLMRequest(
                        prompt=prompt,
                        system_prompt=f"Generate only {path}. Return only file content. No markdown. No explanation.",
                        temperature=self._temperature,
                        max_tokens=self._max_tokens,
                        metadata={
                            "task_id": request.plan.task_id,
                            "task_type": "code_generation",
                            "attempt_type": attempt_type,
                            "attempt_number": attempt_number,
                            "target_file": path,
                            "allow_fallback": _metadata_bool(request.context.metadata, "fallback_enabled", False) and not fallback_used,
                            "local_only": _metadata_bool(request.context.metadata, "local_only", False),
                            **_fallback_authorization_metadata(request.context.metadata),
                            **({"skip_provider_id": attempts[0].provider_id} if fallback_used and attempts else {}),
                        },
                    ),
                    llm_service=service,
                )
                content = strip_file_content(response.content, path)
                validation_errors = validate_chunk_file(path, content, advanced=advanced)
                if validation_errors:
                    raise ProjectValidationError(
                        f"{path} failed validation: " + "; ".join(validation_errors),
                        details={"validation_errors": validation_errors, "path": path},
                    )
                success = True
                await _emit_generation_event(
                    request,
                    "file_generation_validated",
                    generation_mode="chunked",
                    strategy="chunked",
                    file_path=path,
                    status="validated",
                    provider_id=_provider_id_from_response(response),
                    model_id=response.model,
                    attempt_number=attempt_number,
                    repair_used=repair_used,
                    fallback_used=fallback_used,
                    message=f"Validated {path}",
                )
                return GeneratedFile(path=path, content=content, file_type=_infer_file_type(path))
            except ProjectValidationError as exc:
                last_error = exc
                error_code = exc.code
                error_message = exc.message
                validation_errors = tuple(exc.details.get("validation_errors", (exc.message,)))
            finally:
                self._record_attempt(
                    attempts,
                    _attempt_record(
                        request,
                        service,
                        response,
                        prompt,
                        f"Generate only {path}",
                        attempt_number=attempt_number,
                        attempt_type=attempt_type,
                        started=started,
                        started_at=started_at,
                        success=success,
                        error_code=error_code,
                        error_message=error_message,
                        parsed_file_count=1 if response is not None else 0,
                        required_files_present=success,
                        repair_used=repair_used,
                        fallback_used=fallback_used,
                        parse_success=response is not None,
                        validation_success=success,
                        validation_errors=validation_errors,
                    )
                )
                attempt_number += 1
            if success:
                break
        assert last_error is not None
        raise last_error

    async def _generate_with_retries(
        self,
        request: LLMRequest,
        *,
        llm_service: LLMService | None = None,
    ) -> LLMResponse:
        service = llm_service or self._llm_service
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await asyncio.wait_for(
                    service.generate(request),
                    timeout=self.timeout_s,
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                error = LLMTimeoutError(
                    f"{service.provider.value} generation timed out after "
                    f"{self.timeout_s:g} seconds",
                    provider=service.provider,
                    retryable=True,
                    details={
                        "timeout_s": self.timeout_s,
                        "operation": "generation",
                    },
                )
                if attempt == self.max_attempts:
                    raise error from exc
                await self._wait_before_retry(error, attempt)
            except LLMError as exc:
                if not exc.retryable or attempt == self.max_attempts:
                    raise
                await self._wait_before_retry(exc, attempt)
        raise AssertionError("unreachable LLM retry state")

    async def _wait_before_retry(self, error: LLMError, attempt: int) -> None:
        delay_s = self.retry_backoff_s * (2 ** (attempt - 1))
        logger.warning(
            "Retrying %s generation after %s on attempt %d/%d in %.2f seconds",
            error.provider.value,
            type(error).__name__,
            attempt,
            self.max_attempts,
            delay_s,
        )
        if delay_s:
            await asyncio.sleep(delay_s)

    def extract_files(self, content: str) -> tuple[GeneratedFile, ...]:
        """Extract files from an LLM response with safe recovery.

        Preferred output is raw JSON:
        ``{"files": [{"path": ..., "content": ...}]}``. Smaller models often
        return fenced JSON, prose plus JSON, or markdown file blocks, so those
        are recovered before failing with a clear parser error.
        """

        if not isinstance(content, str) or not content.strip():
            raise GeneratedOutputError("generated output must be non-empty")
        document = content.strip()

        errors: list[str] = []
        for candidate in _json_candidates(document):
            try:
                return _files_from_payload(json.loads(candidate))
            except (GeneratedOutputError, json.JSONDecodeError) as exc:
                errors.append(str(exc))

        for extractor in (_extract_file_fences, _extract_file_markers, _extract_heading_blocks):
            try:
                files = extractor(document)
            except GeneratedOutputError as exc:
                errors.append(str(exc))
                continue
            if files:
                return files

        detail = errors[-1] if errors else "no supported file manifest format found"
        raise GeneratedOutputError(
            "Selected model did not return a valid project. "
            f"Try another model or enable fallback. Details: {detail}",
            details={"reason": detail},
        )

    def _extract_files_from_json(self, document: str) -> tuple[GeneratedFile, ...]:
        """Compatibility wrapper for tests and older callers."""

        try:
            return _files_from_payload(json.loads(document))
        except json.JSONDecodeError as exc:
            raise GeneratedOutputError(
                "generated output must be valid JSON",
                details={"line": exc.lineno, "column": exc.colno},
            ) from exc

    def validate_project(self, project: CanonicalGeneratedProject) -> None:
        """Raise ``ProjectValidationError`` if ``project`` is invalid."""

        if not isinstance(project, CanonicalGeneratedProject):
            raise TypeError("project must be a GeneratedProject")
        target_board = _target_board(project.target_board)
        framework = _framework(project.framework)
        self._validate_supported_values(target_board, framework)

        normalized_paths: set[str] = set()
        total_bytes = 0
        for generated_file in project.files:
            normalized = generated_file.path.casefold()
            if normalized in normalized_paths:
                raise ProjectValidationError(
                    f"duplicate generated file path: {generated_file.path}"
                )
            normalized_paths.add(normalized)
            file_bytes = len(generated_file.content.encode("utf-8"))
            if file_bytes > self.MAX_FILE_BYTES:
                raise ProjectValidationError(
                    f"generated file exceeds size limit: {generated_file.path}"
                )
            total_bytes += file_bytes
        if len(project.files) > self.MAX_FILES:
            raise ProjectValidationError(
                f"project exceeds the {self.MAX_FILES}-file limit"
            )
        if total_bytes > self.MAX_PROJECT_BYTES:
            raise ProjectValidationError("generated project exceeds size limit")

        paths = {item.path.casefold(): item for item in project.files}
        if framework is Framework.ARDUINO:
            self._validate_arduino(paths)
        elif framework is Framework.PLATFORMIO:
            self._validate_platformio(paths, target_board)
        elif framework is Framework.ESP_IDF:
            self._validate_esp_idf(paths)
        elif framework is Framework.STM32_CUBE:
            self._validate_stm32_cube(paths)

    def _build_system_prompt(self, plan: ExecutionPlan) -> str:
        framework = _framework(plan.framework)
        target_board = _target_board(plan.target_board)
        return (
            "You are a senior embedded firmware engineer. Generate a complete, "
            "buildable firmware project from the supplied specification. "
            "Return only project files as strict JSON. Do not include explanations outside file content. "
            "Every file must have path and content. Return this schema: "
            '{"project_name":"project-name","files":[{"path":"relative/posix/path","content":"file text"}]}. '
            "All paths must be relative. No absolute paths. No ../ paths. "
            "Required files for PlatformIO projects are platformio.ini and src/main.cpp. "
            "For advanced or complete PlatformIO project requests, also generate README.md "
            "and the complete implementation needed by the prompt. "
            "Do not return only explanation. Do not return a tiny blink sketch unless "
            "the user explicitly asks for blink. "
            "When a platformio_board is supplied, use it as the platformio.ini board value. "
            "For ESP32 PlatformIO Arduino projects, use platform = espressif32 and framework = arduino unless ESP-IDF is explicitly requested. "
            "For simple prompts only, keep the project concise but complete. "
            "Do not return binary files, absolute paths, "
            "parent-directory paths, or omitted placeholder content. "
            f"{_FRAMEWORK_GUIDANCE[framework]} "
            f"{_BOARD_GUIDANCE[target_board]}"
        )

    def _build_prompt(
        self,
        request: CodeGenerationRequest,
        *,
        project_name: str,
    ) -> str:
        plan = request.plan
        specification = {
            "project_name": project_name,
            "output_contract": {
                "format": "json",
                "required_top_level_fields": ["files"],
                "allowed_file_fields": ["path", "content"],
                "path_rules": ["relative", "inside_project_root", "no_parent_segments", "no_absolute_paths"],
            },
            "task_id": plan.task_id,
            "task_type": plan.task_type.value,
            "target_board": _identifier_value(plan.target_board),
            "framework": _identifier_value(plan.framework),
            "board": "esp32dev"
            if _identifier_value(plan.target_board) == TargetBoard.ESP32.value
            else _identifier_value(plan.target_board),
            "generation_mode": request.context.metadata.get("active_workspace", {}).get("generation_mode")
            if isinstance(request.context.metadata.get("active_workspace"), Mapping)
            else None,
            "workspace_root": request.context.project_path,
            "platformio_board": request.context.metadata.get("active_workspace", {}).get("selected_board")
            if isinstance(request.context.metadata.get("active_workspace"), Mapping)
            else None,
            "platformio_framework": request.context.metadata.get("active_workspace", {}).get("selected_framework")
            if isinstance(request.context.metadata.get("active_workspace"), Mapping)
            else None,
            "requirements": list(plan.requirements),
            "simulation_enabled": request.context.simulation_enabled,
            "context_metadata": _json_value(request.context.metadata),
            "plan_metadata": _json_value(plan.metadata),
        }
        return (
            "Generate the firmware project described by this canonical "
            "specification:\n"
            + json.dumps(
                specification,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
        )

    def _build_repair_prompt(
        self,
        request: CodeGenerationRequest,
        *,
        project_name: str,
        previous_error: CodeGenerationError,
    ) -> str:
        base = self._build_prompt(request, project_name=project_name)
        reason = previous_error.message
        advanced = _advanced_prompt(request)
        advanced_note = (
            "\nThe output is too minimal for the requested advanced ESP32 control hub. "
            "Generate the full requested control hub with WiFi AP, WebServer, Preferences, "
            "FreeRTOS tasks, serial commands, REST endpoints, embedded dashboard, and README."
            if advanced and _mentions_minimal(reason)
            else ""
        )
        return (
            "Your previous response was invalid and cannot be used.\n"
            f"Validation error: {reason}\n"
            "Return a corrected complete PlatformIO project with platformio.ini and src/main.cpp. "
            "Return only valid project files. All file paths must be relative and inside the project root."
            f"{advanced_note}\n\n"
            + base
        )

    def _build_fallback_prompt(
        self,
        request: CodeGenerationRequest,
        *,
        project_name: str,
        previous_error: CodeGenerationError,
    ) -> str:
        return (
            "The primary model failed to produce a valid project after repair.\n"
            f"Last validation error: {previous_error.message}\n"
            "Generate a complete corrected project now. Return only valid project files.\n\n"
            + self._build_prompt(request, project_name=project_name)
        )

    def _builtin_fallback_allowed(
        self,
        request: CodeGenerationRequest,
        error: LLMError,
    ) -> bool:
        if not self._fallback_enabled:
            return False
        if not _metadata_bool(request.context.metadata, "fallback_enabled", False):
            return False
        return error.code.casefold() not in _NON_FALLBACK_ERROR_CODES

    def _fallback_allowed(self, request: CodeGenerationRequest) -> bool:
        if not self._fallback_enabled:
            return False
        metadata = request.context.metadata
        if _metadata_bool(metadata, "fallback_enabled", False) is False:
            return False
        if _metadata_bool(metadata, "local_only", False):
            return False
        primary_provider = _provider_id(self._llm_service)
        if self._fallback_llm_service is not None:
            fallback_provider = _provider_id(self._fallback_llm_service)
        elif _router_like(self._llm_service):
            try:
                route = self._llm_service.registry.route_for_task("code_generation")  # type: ignore[attr-defined]
                fallback_provider = route.fallback_provider_id
            except Exception:
                return False
        else:
            return False
        return _fallback_evidence_valid(metadata, primary_provider, fallback_provider)

    def _with_generation_report(
        self,
        project: GeneratedProject,
        attempts: Sequence[GenerationAttempt],
        request: CodeGenerationRequest,
    ) -> GeneratedProject:
        warnings = _quality_warnings(project, request)
        report = {
            "provider_id": attempts[-1].provider_id if attempts else _provider_id(self._llm_service),
            "model_id": attempts[-1].model_id if attempts else str(getattr(self._llm_service, "model", "unknown")),
            "attempt_count": len(attempts),
            "generation_mode": "chunked" if "chunked_generation" in project.metadata else "one_shot",
            "repair_used": any(item.repair_used for item in attempts),
            "fallback_used": any(item.fallback_used for item in attempts),
            "attempts": [item.to_dict() for item in attempts],
            "warnings": list(warnings),
        }
        if isinstance(project.metadata.get("chunked_generation"), Mapping):
            report["chunked_generation"] = dict(project.metadata["chunked_generation"])  # type: ignore[index]
        metadata = {
            **dict(project.metadata),
            "generation_report": report,
            "generation_attempts": report["attempts"],
            "warnings": tuple(dict.fromkeys((*project.metadata.get("warnings", ()), *warnings))),
        }
        return GeneratedProject(
            project_name=project.project_name,
            framework=project.framework,
            target_board=project.target_board,
            files=project.files,
            project_id=project.project_id,
            created_at=project.created_at,
            metadata=metadata,
        )

    def _validate_supported_target(self, plan: ExecutionPlan) -> None:
        try:
            target_board = _target_board(plan.target_board)
            framework = _framework(plan.framework)
        except ValueError as exc:
            raise UnsupportedGenerationTargetError(
                "execution plan contains an unknown board or framework",
                details={
                    "target_board": _identifier_value(plan.target_board),
                    "framework": _identifier_value(plan.framework),
                },
            ) from exc
        self._validate_supported_values(target_board, framework)

    def _validate_supported_values(
        self,
        target_board: TargetBoard,
        framework: Framework,
    ) -> None:
        supported = _SUPPORTED_FRAMEWORKS.get(target_board, frozenset())
        if framework not in supported:
            raise UnsupportedGenerationTargetError(
                f"unsupported generation target: {target_board.value} with "
                f"{framework.value}",
                details={
                    "target_board": target_board.value,
                    "framework": framework.value,
                },
            )

    def _validate_arduino(self, paths: Mapping[str, GeneratedFile]) -> None:
        sketches = [item for path, item in paths.items() if path.endswith(".ino")]
        if len(sketches) != 1:
            raise ProjectValidationError(
                "Arduino projects must contain exactly one .ino file"
            )
        content = sketches[0].content
        if not re.search(r"\bvoid\s+setup\s*\(", content):
            raise ProjectValidationError("Arduino sketch must define setup()")
        if not re.search(r"\bvoid\s+loop\s*\(", content):
            raise ProjectValidationError("Arduino sketch must define loop()")

    def _validate_platformio(
        self,
        paths: Mapping[str, GeneratedFile],
        target_board: TargetBoard,
    ) -> None:
        required = {"platformio.ini", "src/main.cpp"}
        missing = sorted(required - paths.keys())
        if missing:
            raise ProjectValidationError(
                "PlatformIO project is missing required files: "
                + ", ".join(missing)
            )
        ini = paths["platformio.ini"].content
        if not re.search(r"(?mi)^\s*\[env:[^\]]+\]\s*$", ini):
            raise ProjectValidationError(
                "platformio.ini must define at least one environment"
            )
        board_match = _PLATFORMIO_BOARD_RE.search(ini)
        if board_match is None:
            raise ProjectValidationError(
                "platformio.ini must select a board"
            )
        board_rule = _PLATFORMIO_BOARD_RULES.get(target_board)
        board_id = board_match.group("board")
        if board_rule is not None and board_rule.search(board_id) is None:
            raise ProjectValidationError(
                f"PlatformIO board {board_id!r} is incompatible with "
                f"{target_board.value}"
            )

    def _validate_esp_idf(self, paths: Mapping[str, GeneratedFile]) -> None:
        required = {"cmakelists.txt", "main/cmakelists.txt"}
        missing = sorted(required - paths.keys())
        if missing:
            raise ProjectValidationError(
                "ESP-IDF project is missing required files: "
                + ", ".join(missing)
            )
        source_files = [
            item
            for path, item in paths.items()
            if path.startswith("main/") and path.endswith((".c", ".cpp", ".cc"))
        ]
        if not source_files:
            raise ProjectValidationError(
                "ESP-IDF project must contain a source file under main/"
            )
        if not any(
            re.search(r"\bapp_main\s*\(", item.content)
            for item in source_files
        ):
            raise ProjectValidationError(
                "ESP-IDF project must define app_main()"
            )

    def _validate_stm32_cube(self, paths: Mapping[str, GeneratedFile]) -> None:
        required = {"core/src/main.c", "core/inc/main.h"}
        missing = sorted(required - paths.keys())
        if missing:
            raise ProjectValidationError(
                "STM32Cube project is missing required files: "
                + ", ".join(missing)
            )
        if not re.search(r"\bint\s+main\s*\(", paths["core/src/main.c"].content):
            raise ProjectValidationError(
                "STM32Cube Core/Src/main.c must define main()"
            )


def _derive_project_name(request: CodeGenerationRequest) -> str:
    if request.context.project_path:
        candidate = request.context.project_path.replace("\\", "/").rstrip("/")
        candidate = candidate.rsplit("/", 1)[-1]
    else:
        candidate = request.plan.task_id
    slug = re.sub(r"[^a-z0-9_-]+", "-", candidate.casefold()).strip("-_")
    if not slug:
        slug = "promptforge-project"
    return slug[:64].rstrip("-_")


def _identifier_value(value: str) -> str:
    if isinstance(value, (TargetBoard, Framework)):
        return value.value
    return value


def _target_board(value: str) -> TargetBoard:
    return TargetBoard(_identifier_value(value))


def _framework(value: str) -> Framework:
    return Framework(_identifier_value(value))


def _validate_project_name(value: object) -> None:
    if not isinstance(value, str) or not _PROJECT_NAME_RE.fullmatch(value):
        raise ValueError(
            "project_name must be a lowercase filesystem-safe name"
        )


def _infer_file_type(path: object) -> str:
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    name = path.rsplit("/", 1)[-1]
    if name.casefold() == "cmakelists.txt":
        return "cmake"
    if "." not in name:
        return "text"
    return name.rsplit(".", 1)[-1].casefold() or "text"


def _json_candidates(document: str) -> tuple[str, ...]:
    candidates: list[str] = [document]
    candidates.extend(match.group("body").strip() for match in _JSON_FENCE_RE.finditer(document))
    extracted = _extract_first_json_object(document)
    if extracted is not None:
        candidates.append(extracted)
    unique: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def _extract_first_json_object(document: str) -> str | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(document):
        if character != "{":
            continue
        try:
            _, end = decoder.raw_decode(document[index:])
        except json.JSONDecodeError:
            continue
        return document[index : index + end]
    return None


def _files_from_payload(payload: object) -> tuple[GeneratedFile, ...]:
    if not isinstance(payload, Mapping):
        raise GeneratedOutputError("generated output must be a mapping")
    if "files" not in payload:
        raise GeneratedOutputError("generated output is missing required fields: files")
    raw_files = payload["files"]
    if not isinstance(raw_files, Sequence) or isinstance(
        raw_files, (str, bytes, bytearray)
    ):
        raise GeneratedOutputError("generated output files must be a list")
    if not raw_files:
        raise GeneratedOutputError("generated output files cannot be empty")
    if len(raw_files) > CodeGenerationService.MAX_FILES:
        raise GeneratedOutputError(
            f"generated output exceeds the {CodeGenerationService.MAX_FILES}-file limit"
        )
    return _build_generated_files(raw_files)


def _build_generated_files(raw_files: Sequence[object]) -> tuple[GeneratedFile, ...]:
    files: list[GeneratedFile] = []
    for index, raw_file in enumerate(raw_files):
        try:
            file_data = _exact_schema(
                raw_file,
                expected={"path", "content"},
                label="generated file",
            )
            files.append(
                GeneratedFile(
                    path=file_data["path"],
                    content=file_data["content"],
                    file_type=_infer_file_type(file_data["path"]),
                )
            )
        except (TypeError, ValueError) as exc:
            raise GeneratedOutputError(
                f"generated file at index {index} is invalid: {exc}"
            ) from exc
    return tuple(files)


def _extract_file_fences(document: str) -> tuple[GeneratedFile, ...]:
    raw_files = [
        {"path": match.group("path").strip(), "content": match.group("content").strip("\r\n")}
        for match in _FILE_FENCE_RE.finditer(document)
    ]
    return _build_generated_files(raw_files) if raw_files else ()


def _extract_file_markers(document: str) -> tuple[GeneratedFile, ...]:
    raw_files = [
        {"path": match.group("path").strip(), "content": match.group("content").strip("\r\n")}
        for match in _FILE_MARKER_RE.finditer(document)
    ]
    return _build_generated_files(raw_files) if raw_files else ()


def _extract_heading_blocks(document: str) -> tuple[GeneratedFile, ...]:
    lines = document.splitlines()
    blocks: list[dict[str, str]] = []
    current_path: str | None = None
    current_content: list[str] = []
    for line in lines:
        heading = _HEADING_RE.match(line)
        if heading is not None:
            candidate = heading.group("path").strip().strip("`")
            if _FILE_LIKE_HEADING_RE.match(candidate):
                if current_path is not None:
                    blocks.append(
                        {"path": current_path, "content": "\n".join(current_content).strip("\n")}
                    )
                current_path = candidate
                current_content = []
                continue
        if current_path is not None:
            current_content.append(line)
    if current_path is not None:
        blocks.append({"path": current_path, "content": "\n".join(current_content).strip("\n")})
    return _build_generated_files(blocks) if blocks else ()


def _exact_schema(
    data: Mapping[str, Any],
    *,
    expected: set[str],
    label: str,
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a mapping")
    supplied = set(data)
    missing = expected - supplied
    unknown = supplied - expected
    if missing:
        raise ValueError(
            f"{label} is missing required fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise ValueError(
            f"{label} contains unknown fields: {', '.join(sorted(unknown))}"
        )
    return dict(data)


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _json_mapping_from_text(text: str) -> Mapping[str, Any]:
    for candidate in _json_candidates(text.strip()):
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            return payload
    raise GeneratedOutputError("model did not return valid JSON for chunked generation")


def _request_text_for_strategy(request: CodeGenerationRequest) -> str:
    prompt = request.context.metadata.get("prompt")
    values = [
        prompt if isinstance(prompt, str) else "",
        " ".join(request.plan.requirements),
        str(request.plan.metadata.get("normalized_prompt", "")),
    ]
    return "\n".join(item for item in values if item)


def _expected_file_count(request: CodeGenerationRequest) -> int:
    if _advanced_prompt(request):
        return 4 if _readme_requested(request) else 3
    if _readme_requested(request):
        return 3
    return 2 if _framework(request.plan.framework) is Framework.PLATFORMIO else 1


def _chunk_requirements_prompt(request: CodeGenerationRequest, project_name: str) -> str:
    required_files = ["platformio.ini", "src/main.cpp"]
    if _readme_requested(request):
        required_files.append("README.md")
    return (
        "Extract a compact firmware requirement summary as JSON only.\n"
        "Required JSON fields: project_name, board, framework, platform, required_features, forbidden_features, required_files.\n"
        f"Project name fallback: {project_name}\n"
        f"Prompt and requirements:\n{_request_text_for_strategy(request)}\n"
        f"Required files: {', '.join(required_files)}. Add other files only when the requested implementation needs them."
    )


def _chunk_manifest_prompt(
    request: CodeGenerationRequest,
    project_name: str,
    summary: Mapping[str, Any],
) -> str:
    return (
        "Create a project manifest as JSON only. Do not include file contents.\n"
        "Required JSON fields: project_name, files, build_target.\n"
        "Each files item must have path, purpose, required.\n"
        f"Project name: {project_name}\n"
        f"Requirement summary:\n{json.dumps(_json_value(summary), ensure_ascii=True, sort_keys=True)}\n"
        f"Original requirements:\n{_request_text_for_strategy(request)}"
    )


def _manifest_files(
    manifest: Mapping[str, Any],
    request: CodeGenerationRequest,
) -> tuple[dict[str, str], ...]:
    raw_files = manifest.get("files")
    entries: list[dict[str, str]] = []
    if isinstance(raw_files, Sequence) and not isinstance(raw_files, (str, bytes, bytearray)):
        for item in raw_files:
            if not isinstance(item, Mapping):
                continue
            path = item.get("path")
            if not isinstance(path, str) or not path.strip():
                continue
            entries.append(
                {
                    "path": path,
                    "purpose": str(item.get("purpose", "")),
                }
            )
    required = ["platformio.ini", "src/main.cpp"]
    if _readme_requested(request):
        required.append("README.md")
    known = {entry["path"] for entry in entries}
    for path in required:
        if path not in known:
            entries.append({"path": path, "purpose": _default_file_purpose(path)})
    return tuple(entries)


def _default_file_purpose(path: str) -> str:
    if path == "platformio.ini":
        return "PlatformIO build configuration"
    if path == "include/config.h":
        return "Project constants and configuration"
    if path == "src/main.cpp":
        return "Complete ESP32 firmware implementation"
    if path == "README.md":
        return "Usage documentation"
    return "Project file"


def _chunk_file_prompt(
    request: CodeGenerationRequest,
    *,
    summary: Mapping[str, Any],
    manifest: Mapping[str, Any],
    path: str,
    purpose: str,
    existing_files: tuple[GeneratedFile, ...],
    repair_error: str | None,
) -> str:
    existing = {
        item.path: item.content[:1600]
        for item in existing_files
        if item.path in {"platformio.ini", "include/config.h"}
    }
    instructions = {
        "platformio.ini": (
            "Generate only platformio.ini. It must contain [env:esp32dev], "
            "platform = espressif32, board = esp32dev, framework = arduino."
        ),
        "include/config.h": (
            "Generate only include/config.h. Define only constants required by the supplied requirements and manifest."
        ),
        "src/main.cpp": (
            "Generate only src/main.cpp. Implement every requested capability from the requirement summary, including setup() and loop(), "
            "without adding unrelated hardware or cloud services. Prefer libraries included with the Arduino ESP32 core unless platformio.ini declares a dependency."
        ),
        "README.md": (
            "Generate only README.md. Include overview, features, build, upload, dashboard URL, serial commands, API endpoints, and troubleshooting."
        ),
    }
    repair = f"\nPrevious file validation error: {repair_error}\nRegenerate the complete file." if repair_error else ""
    return (
        f"{instructions.get(path, f'Generate only {path}.')}\n"
        "Return only the requested file content. No markdown wrapper. No explanation.\n"
        f"Purpose: {purpose}\n"
        f"Requirement summary:\n{json.dumps(_json_value(summary), ensure_ascii=True, sort_keys=True)}\n"
        f"Project manifest:\n{json.dumps(_json_value(manifest), ensure_ascii=True, sort_keys=True)}\n"
        f"Existing relevant files:\n{json.dumps(existing, ensure_ascii=True, sort_keys=True)}"
        f"{repair}"
    )


def _attempt_record(
    request: CodeGenerationRequest,
    service: LLMService,
    response: LLMResponse | None,
    prompt: str,
    system_prompt: str | None,
    *,
    attempt_number: int,
    attempt_type: str,
    started: float,
    started_at: str,
    success: bool,
    error_code: str | None,
    error_message: str | None,
    parsed_file_count: int,
    required_files_present: bool,
    repair_used: bool,
    fallback_used: bool,
    parse_success: bool,
    validation_success: bool,
    validation_errors: tuple[str, ...] = (),
) -> GenerationAttempt:
    return GenerationAttempt(
        attempt_id=f"attempt-{uuid.uuid4().hex}",
        execution_id=_execution_id(request),
        task_id=request.plan.task_id,
        provider_id=_provider_id_from_response(response) if response is not None else _provider_id(service),
        model_id=response.model if response is not None else str(getattr(service, "model", "unknown")),
        route_id=str(request.context.metadata.get("route_id", "code_generation")),
        prompt_hash=_hash_prompt(prompt, system_prompt),
        attempt_number=attempt_number,
        attempt_type=attempt_type,
        started_at=started_at,
        completed_at=_utc_now(),
        success=success,
        error_code=error_code,
        error_message=error_message,
        raw_output_preview=_preview(response.content) if response is not None else None,
        parsed_file_count=parsed_file_count,
        required_files_present=required_files_present,
        validation_errors=validation_errors,
        fallback_used=fallback_used,
        repair_used=repair_used,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        parse_success=parse_success,
        validation_success=validation_success,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def _emit_generation_event(
    request: CodeGenerationRequest,
    event_type: str,
    *,
    generation_mode: str = "one_shot",
    strategy: str | None = None,
    message: str | None = None,
    file_path: str | None = None,
    status: str | None = None,
    provider_id: str | None = None,
    model_id: str | None = None,
    attempt_number: int | None = None,
    repair_used: bool = False,
    fallback_used: bool = False,
    **extra: Any,
) -> None:
    callback = request.generation_event_callback
    if callback is None:
        return
    payload: dict[str, Any] = {
        "event_type": event_type,
        "task_id": request.plan.task_id,
        "execution_id": _execution_id(request),
        "generation_mode": generation_mode,
        "strategy": strategy or generation_mode,
        "repair_used": repair_used,
        "fallback_used": fallback_used,
        "timestamp": _utc_now(),
    }
    if message:
        payload["message"] = message
    if file_path:
        payload["file_path"] = file_path
    if status:
        payload["status"] = status
    if provider_id:
        payload["provider_id"] = provider_id
    if model_id:
        payload["model_id"] = model_id
    if attempt_number is not None:
        payload["attempt_number"] = attempt_number
    payload.update(extra)
    result = callback(payload)
    if inspect.isawaitable(result):
        await result


def _execution_id(request: CodeGenerationRequest) -> str:
    value = request.context.metadata.get("execution_id")
    return value if isinstance(value, str) and value.strip() else request.plan.task_id


def _chunked_workspace_root(request: CodeGenerationRequest) -> Path | None:
    workspace = request.context.metadata.get("active_workspace")
    if isinstance(workspace, Mapping):
        value = workspace.get("rootPath") or workspace.get("root_path")
        if isinstance(value, str) and value.strip():
            return Path(value).expanduser().resolve()
    if request.context.project_path:
        return Path(request.context.project_path).expanduser().resolve()
    return None


def _attempt_count(
    attempts: Sequence[GenerationAttempt],
    attempt_type: str,
    *,
    success_only: bool = False,
) -> int:
    return sum(
        1
        for attempt in attempts
        if attempt.attempt_type == attempt_type and (attempt.success or not success_only)
    )


def _provider_id(service: LLMService) -> str:
    provider = getattr(service, "provider", None)
    if isinstance(provider, LLMProvider):
        return provider.value.casefold()
    if isinstance(provider, str) and provider.strip():
        return provider.casefold()
    return type(service).__name__.casefold()


def _provider_id_from_response(response: LLMResponse) -> str:
    return (response.provider_id or response.provider.value).casefold()


def _hash_prompt(prompt: str, system_prompt: str | None) -> str:
    digest = hashlib.sha256()
    digest.update((system_prompt or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(prompt.encode("utf-8"))
    return digest.hexdigest()


def _preview(content: str, limit: int = 500) -> str:
    compact = re.sub(r"\s+", " ", content).strip()
    return compact[:limit]


def _metadata_bool(metadata: Mapping[str, Any], key: str, default: bool) -> bool:
    value = metadata.get(key)
    return value if isinstance(value, bool) else default


def _fallback_authorization_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    allowed = (
        "fallback_policy",
        "consent_granted",
        "approved_recipients",
        "approved_provider_groups",
        "provider_group_membership",
    )
    return {key: metadata[key] for key in allowed if key in metadata}


def _fallback_evidence_valid(
    metadata: Mapping[str, Any],
    primary_provider: str,
    fallback_provider: str | None,
) -> bool:
    if not fallback_provider:
        return False
    fallback_provider = fallback_provider.casefold()
    primary_provider = primary_provider.casefold()
    policy = metadata.get("fallback_policy")
    if primary_provider == fallback_provider:
        return policy == "same_provider"
    if not _metadata_bool(metadata, "consent_granted", False):
        return False
    recipients = metadata.get("approved_recipients")
    approved_recipients = {
        value.casefold() for value in recipients if isinstance(value, str)
    } if isinstance(recipients, Sequence) and not isinstance(recipients, str) else set()
    if not {primary_provider, fallback_provider}.issubset(approved_recipients):
        return False
    if policy == "ask_before_cross_provider":
        return True
    if policy != "approved_provider_group":
        return False
    groups = metadata.get("approved_provider_groups")
    approved_groups = {
        value for value in groups if isinstance(value, str) and value
    } if isinstance(groups, Sequence) and not isinstance(groups, str) else set()
    membership = metadata.get("provider_group_membership")
    if not isinstance(membership, Mapping):
        return False
    primary_group = membership.get(primary_provider)
    fallback_group = membership.get(fallback_provider)
    return isinstance(primary_group, str) and primary_group == fallback_group and primary_group in approved_groups

def _router_like(service: LLMService) -> bool:
    return hasattr(service, "registry") and hasattr(service, "generate_model")


def _required_files_present(
    framework: Framework,
    files: Sequence[GeneratedFile],
    request: CodeGenerationRequest,
) -> bool:
    paths = {item.path.casefold() for item in files}
    if framework is Framework.PLATFORMIO:
        required = {"platformio.ini", "src/main.cpp"}
        if _readme_requested(request):
            required.add("readme.md")
        return required.issubset(paths)
    return bool(paths)


def _readme_requested(request: CodeGenerationRequest) -> bool:
    text = _request_text(request)
    return "readme" in text or "documentation" in text or "document the project" in text


def _advanced_prompt(request: CodeGenerationRequest) -> bool:
    text = _request_text(request)
    return any(term in text for term in (
        "advanced", "control hub", "web dashboard", "webserver", "web server",
        "browser", "http server", "rest api", "freertos", "multiple files",
    ))


def _request_text(request: CodeGenerationRequest) -> str:
    prompt = request.context.metadata.get("prompt")
    values = [
        prompt if isinstance(prompt, str) else "",
        " ".join(request.plan.requirements),
        str(request.plan.metadata.get("normalized_prompt", "")),
    ]
    return " ".join(values).casefold()


def _should_use_builtin_blink(
    request: CodeGenerationRequest,
    target_board: TargetBoard,
    framework: Framework,
    llm_service: LLMService,
) -> bool:
    if framework is not Framework.PLATFORMIO or target_board not in _BUILTIN_PLATFORMIO_BOARDS:
        return False
    text = _request_text(request)
    if "blink" not in text or "led" not in text:
        return False
    advanced_terms = (
        "bluetooth", "display", "freertos", "http", "mqtt", "motor", "sensor",
        "servo", "webserver", "web server", "wifi", "wi-fi",
    )
    if any(term in text for term in advanced_terms):
        return False
    if not _router_like(llm_service):
        return False
    registry = getattr(llm_service, "registry", None)
    connections = getattr(registry, "connections", None)
    if connections is None:
        return False
    try:
        records = connections.list()
    except Exception:
        return False
    return not any(
        record.provider_type.value == "remote_api" and record.connection_ready
        for record in records
    )


def _build_builtin_blink_project(
    request: CodeGenerationRequest,
    *,
    project_name: str,
    target_board: TargetBoard,
    framework: Framework,
) -> GeneratedProject:
    platform, board, fallback_led_pin = _BUILTIN_PLATFORMIO_BOARDS[target_board]
    files = [
        GeneratedFile(
            path="platformio.ini",
            file_type="ini",
            content=(
                f"[env:{board}]\n"
                f"platform = {platform}\n"
                f"board = {board}\n"
                "framework = arduino\n"
                "monitor_speed = 115200\n"
            ),
        ),
        GeneratedFile(
            path="src/main.cpp",
            file_type="cpp",
            content=(
                "#include <Arduino.h>\n\n"
                "#ifndef LED_BUILTIN\n"
                f"#define LED_BUILTIN {fallback_led_pin}\n"
                "#endif\n\n"
                "constexpr unsigned long BLINK_INTERVAL_MS = 500;\n\n"
                "void setup() {\n"
                "  Serial.begin(115200);\n"
                "  pinMode(LED_BUILTIN, OUTPUT);\n"
                "  digitalWrite(LED_BUILTIN, LOW);\n"
                "  Serial.println(\"ForgeX blink project started\");\n"
                "}\n\n"
                "void loop() {\n"
                "  digitalWrite(LED_BUILTIN, HIGH);\n"
                "  delay(BLINK_INTERVAL_MS);\n"
                "  digitalWrite(LED_BUILTIN, LOW);\n"
                "  delay(BLINK_INTERVAL_MS);\n"
                "}\n"
            ),
        ),
    ]
    if _readme_requested(request):
        files.append(
            GeneratedFile(
                path="README.md",
                file_type="md",
                content=(
                    f"# {project_name}\n\n"
                    "Verified ForgeX PlatformIO blink scaffold. The onboard LED toggles every 500 ms.\n\n"
                    "Build with `pio run` and upload with `pio run --target upload`.\n"
                ),
            )
        )
    return GeneratedProject(
        project_name=project_name,
        framework=framework,
        target_board=target_board,
        files=tuple(files),
        metadata={"task_id": request.plan.task_id, "generation_source": "forgex_builtin"},
    )


def _supports_builtin_wifi_monitor(
    request: CodeGenerationRequest,
    target_board: TargetBoard,
    framework: Framework,
) -> bool:
    if framework is not Framework.PLATFORMIO or target_board not in _BUILTIN_PLATFORMIO_BOARDS:
        return False
    text = _request_text(request)
    return ("wifi" in text or "wi-fi" in text) and any(
        term in text for term in ("monitor", "device", "status", "webserver", "web server")
    )


def _build_builtin_wifi_monitor_project(
    request: CodeGenerationRequest,
    *,
    project_name: str,
    target_board: TargetBoard,
    framework: Framework,
) -> GeneratedProject:
    platform, board, fallback_led_pin = _BUILTIN_PLATFORMIO_BOARDS[target_board]
    files = (
        GeneratedFile(
            path="platformio.ini",
            file_type="ini",
            content=(
                f"[env:{board}]\n"
                f"platform = {platform}\n"
                f"board = {board}\n"
                "framework = arduino\n"
                "monitor_speed = 115200\n"
            ),
        ),
        GeneratedFile(
            path="include/config.h",
            file_type="h",
            content=(
                "#pragma once\n\n"
                "// Replace these values before flashing.\n"
                "constexpr char WIFI_SSID[] = \"YOUR_WIFI_SSID\";\n"
                "constexpr char WIFI_PASSWORD[] = \"YOUR_WIFI_PASSWORD\";\n"
                "constexpr char DEVICE_NAME[] = \"forgex-esp32-monitor\";\n"
            ),
        ),
        GeneratedFile(
            path="src/main.cpp",
            file_type="cpp",
            content=(
                "#include <Arduino.h>\n"
                "#include <WebServer.h>\n"
                "#include <WiFi.h>\n"
                "#include \"config.h\"\n\n"
                "#ifndef LED_BUILTIN\n"
                f"#define LED_BUILTIN {fallback_led_pin}\n"
                "#endif\n\n"
                "WebServer server(80);\n"
                "unsigned long startedAt = 0;\n\n"
                "String statusJson() {\n"
                "  String json = \"{\\\"device\\\":\\\"\" + String(DEVICE_NAME) + \"\\\",\";\n"
                "  json += \"\\\"ip\\\":\\\"\" + WiFi.localIP().toString() + \"\\\",\";\n"
                "  json += \"\\\"rssi\\\":\" + String(WiFi.RSSI()) + \",\";\n"
                "  json += \"\\\"uptime_ms\\\":\" + String(millis() - startedAt) + \"}\";\n"
                "  return json;\n"
                "}\n\n"
                "void handleRoot() {\n"
                "  server.send(200, \"text/html\",\n"
                "    \"<!doctype html><meta name='viewport' content='width=device-width'>\"\n"
                "    \"<title>ESP32 Monitor</title><h1>ESP32 Device Monitor</h1>\"\n"
                "    \"<pre id='status'>Loading...</pre><script>async function refresh(){\"\n"
                "    \"status.textContent=JSON.stringify(await (await fetch('/api/status')).json(),null,2)}\"\n"
                "    \"refresh();setInterval(refresh,2000)</script>\");\n"
                "}\n\n"
                "void setup() {\n"
                "  Serial.begin(115200);\n"
                "  pinMode(LED_BUILTIN, OUTPUT);\n"
                "  digitalWrite(LED_BUILTIN, LOW);\n"
                "  WiFi.setHostname(DEVICE_NAME);\n"
                "  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);\n"
                "  Serial.print(\"Connecting to Wi-Fi\");\n"
                "  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print('.'); }\n"
                "  startedAt = millis();\n"
                "  Serial.println();\n"
                "  Serial.println(WiFi.localIP());\n"
                "  server.on(\"/\", handleRoot);\n"
                "  server.on(\"/api/status\", []() { server.send(200, \"application/json\", statusJson()); });\n"
                "  server.begin();\n"
                "  digitalWrite(LED_BUILTIN, HIGH);\n"
                "}\n\n"
                "void loop() {\n"
                "  server.handleClient();\n"
                "  delay(2);\n"
                "}\n"
            ),
        ),
        GeneratedFile(
            path="README.md",
            file_type="md",
            content=(
                f"# {project_name}\n\n"
                "Deterministic ForgeX ESP32 Wi-Fi device monitor fallback. It exposes `/` and `/api/status` on port 80.\n\n"
                "1. Set `WIFI_SSID` and `WIFI_PASSWORD` in `include/config.h`.\n"
                "2. Build with `pio run`.\n"
                "3. Flash with `pio run --target upload`, then open the IP printed at 115200 baud.\n"
            ),
        ),
    )
    return GeneratedProject(
        project_name=project_name,
        framework=framework,
        target_board=target_board,
        files=files,
        metadata={
            "task_id": request.plan.task_id,
            "generation_source": "forgex_builtin",
            "fallback_kind": "deterministic_template",
        },
    )


def _validate_prompt_specific_project(
    project: GeneratedProject,
    request: CodeGenerationRequest,
) -> None:
    if _framework(project.framework) is Framework.PLATFORMIO and _readme_requested(request):
        paths = {item.path.casefold() for item in project.files}
        if "readme.md" not in paths:
            raise ProjectValidationError(
                "PlatformIO project is missing required README.md for this prompt",
                details={"missing": ["README.md"]},
            )
    source = next((item.content for item in project.files if item.path.casefold() == "src/main.cpp"), "")
    normalized = source.casefold()
    request_text = _request_text(request)
    missing_capabilities: list[str] = []
    if ("wifi" in request_text or "wi-fi" in request_text) and "wifi" not in normalized:
        missing_capabilities.append("WiFi")
    if any(term in request_text for term in ("webserver", "web server", "http server", "browser")) and not any(
        term in normalized for term in ("webserver", "asyncwebserver", "httpserver")
    ):
        missing_capabilities.append("web server")
    if "led" in request_text and "control" in request_text and "digitalwrite" not in normalized:
        missing_capabilities.append("LED control")
    if missing_capabilities:
        raise ProjectValidationError(
            "generated project is missing requested capabilities: " + ", ".join(missing_capabilities),
            details={"missing_capabilities": missing_capabilities},
        )


def _quality_warnings(
    project: GeneratedProject,
    request: CodeGenerationRequest,
) -> tuple[str, ...]:
    if not _advanced_prompt(request):
        return ()
    paths = {item.path.casefold(): item for item in project.files}
    source = paths.get("src/main.cpp")
    warnings: list[str] = []
    if source is None:
        return ()
    content = source.content
    normalized = content.casefold()
    missing: list[str] = []
    if len(content) < 1800:
        missing.append("implementation appears very small")
    if any(term in _request_text(request) for term in ("webserver", "web server", "http server", "browser")) and not any(
        term in normalized for term in ("webserver", "asyncwebserver", "httpserver")
    ):
        missing.append("WebServer")
    if "wifi" in _request_text(request) and ("softap" not in normalized and "wifi" not in normalized):
        missing.append("WiFi AP")
    if "preferences" in _request_text(request) and "preferences" not in normalized:
        missing.append("Preferences")
    if _readme_requested(request) and "readme.md" not in paths:
        missing.append("README.md")
    if missing:
        warnings.append(
            "Generated code may be too minimal for the prompt: "
            + ", ".join(missing)
            + " not detected."
        )
    return tuple(warnings)


def _mentions_minimal(message: str) -> bool:
    normalized = message.casefold()
    return "minimal" in normalized or "tiny" in normalized or "blink" in normalized
