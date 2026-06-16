"""Framework-aware firmware source generation for PromptForge AI.

The service translates an immutable ``ExecutionPlan`` into a provider-neutral
``LLMRequest``, delegates generation to an injected ``LLMService``, extracts a
strict file manifest, and validates the resulting in-memory project. It does
not write files, build firmware, execute tools, or call runtime components.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
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
    LLMRequest,
    LLMResponse,
    LLMService,
    LLMTimeoutError,
)

__all__ = [
    "CodeGenerationError",
    "CodeGenerationRequest",
    "CodeGenerationService",
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
    r"\A\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*\Z",
    re.IGNORECASE | re.DOTALL,
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
        self._llm_service = llm_service
        self._temperature = float(temperature)
        self._max_tokens = max_tokens
        self.timeout_s = float(timeout_s)
        self.max_attempts = max_attempts
        self.retry_backoff_s = float(retry_backoff_s)

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
        llm_request = LLMRequest(
            prompt=self._build_prompt(request, project_name=project_name),
            system_prompt=self._build_system_prompt(request.plan),
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            metadata={
                "task_id": request.plan.task_id,
                "project_name": project_name,
                "target_board": target_board.value,
                "framework": framework.value,
            },
        )
        response = await self._generate_with_retries(llm_request)
        files = self.extract_files(response.content)
        project = GeneratedProject(
            project_name=project_name,
            framework=framework,
            target_board=target_board,
            files=files,
            metadata={"task_id": request.plan.task_id},
        )
        self.validate_project(project)
        return project

    async def _generate_with_retries(self, request: LLMRequest) -> LLMResponse:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await asyncio.wait_for(
                    self._llm_service.generate(request),
                    timeout=self.timeout_s,
                )
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as exc:
                error = LLMTimeoutError(
                    f"{self._llm_service.provider.value} generation timed out after "
                    f"{self.timeout_s:g} seconds",
                    provider=self._llm_service.provider,
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
            self._llm_service.provider.value,
            type(error).__name__,
            attempt,
            self.max_attempts,
            delay_s,
        )
        if delay_s:
            await asyncio.sleep(delay_s)

    def extract_files(self, content: str) -> tuple[GeneratedFile, ...]:
        """Extract files from a strict JSON manifest returned by an LLM.

        Accepted forms are raw JSON or a single ``json`` fenced block. The
        document must be ``{"files": [{"path": ..., "content": ...}]}``.
        Conversational text and additional top-level fields are rejected.
        """

        if not isinstance(content, str) or not content.strip():
            raise GeneratedOutputError("generated output must be non-empty")
        document = content.strip()
        fence = _JSON_FENCE_RE.fullmatch(document)
        if fence is not None:
            document = fence.group("body").strip()
        try:
            payload = json.loads(document)
        except json.JSONDecodeError as exc:
            raise GeneratedOutputError(
                "generated output must be valid JSON",
                details={"line": exc.lineno, "column": exc.colno},
            ) from exc
        try:
            values = _exact_schema(
                payload,
                expected={"files"},
                label="generated output",
            )
        except ValueError as exc:
            raise GeneratedOutputError(str(exc)) from exc

        raw_files = values["files"]
        if not isinstance(raw_files, Sequence) or isinstance(
            raw_files, (str, bytes, bytearray)
        ):
            raise GeneratedOutputError("generated output files must be a list")
        if not raw_files:
            raise GeneratedOutputError("generated output files cannot be empty")
        if len(raw_files) > self.MAX_FILES:
            raise GeneratedOutputError(
                f"generated output exceeds the {self.MAX_FILES}-file limit"
            )

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
            "Return JSON only with exactly this schema: "
            '{"files":[{"path":"relative/posix/path","content":"file text"}]}. '
            "Do not return markdown, explanations, binary files, absolute paths, "
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
            "task_id": plan.task_id,
            "task_type": plan.task_type.value,
            "target_board": _identifier_value(plan.target_board),
            "framework": _identifier_value(plan.framework),
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
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value
