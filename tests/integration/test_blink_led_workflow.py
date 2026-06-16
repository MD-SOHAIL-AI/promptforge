from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest

from backend.agent.coordinator import ExecutionStatus
from backend.contracts.execution_plan import ExecutionStep
from backend.contracts.task import Task
from backend.main import execute_prompt, execute_task
from backend.runtime.result import (
    BuildResult,
    FailureResult,
    FlashResult,
    ObserveResult,
    ObserveTerminationReason,
    ResultStatus,
    VerificationStatus,
)
from backend.runtime.subprocess_mgr import SubprocessManager
from backend.services.code_generation_service import CodeGenerationService
from backend.services.llm_service import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMService,
    OpenRouterService,
)
from backend.services.project_service import ProjectService
from backend.tools.board_detector import BoardInfo, BoardType
from backend.tools.build_firmware import BuildConfig
from backend.tools.flash_firmware import FlashConfig
from backend.tools.serial_monitor import SerialMonitorConfig
from backend.tools import tool_registry
from backend.workflow.generate_code_handler import GenerateCodeResult


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


class MockLLM(LLMService):
    provider = LLMProvider.OPENAI
    model = "mock-code-model"

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        content = json.dumps(
            {
                "files": [
                    {
                        "path": "platformio.ini",
                        "content": (
                            "[env:esp32dev]\n"
                            "platform = espressif32\n"
                            "board = esp32dev\n"
                            "framework = arduino\n"
                        ),
                    },
                    {
                        "path": "src/main.cpp",
                        "content": (
                            "#include <Arduino.h>\n"
                            "void setup() { pinMode(2, OUTPUT); }\n"
                            "void loop() {\n"
                            "  digitalWrite(2, HIGH); delay(500);\n"
                            "  digitalWrite(2, LOW); delay(500);\n"
                            "}\n"
                        ),
                    },
                ]
            }
        )
        return LLMResponse(
            content=content,
            provider=self.provider,
            model=self.model,
        )

    def generate_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        async def stream() -> AsyncIterator[str]:
            response = await self.generate(request)
            yield response.content

        return stream()

    async def health_check(self) -> bool:
        return True


class MockTools:
    def __init__(
        self,
        *,
        build_failure: bool = False,
        detected_boards: list[BoardInfo] | None = None,
        flash_failure: bool = False,
    ) -> None:
        self.build_failure = build_failure
        self.detected_boards = detected_boards
        self.flash_failure = flash_failure
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def __call__(self, name: str, *args: object, **kwargs: object) -> object:
        self.calls.append((name, args, dict(kwargs)))
        if name == "build_firmware":
            config, manager = args
            assert isinstance(config, BuildConfig)
            assert isinstance(manager, SubprocessManager)
            if self.build_failure:
                return BuildResult(
                    success=False,
                    status=ResultStatus.FAILED,
                    message="mock PlatformIO build failed",
                    failure=FailureResult(
                        category="BUILD_FAILED",
                        message="mock PlatformIO build failed",
                        retryable=False,
                        stage="build",
                    ),
                )
            firmware = (
                Path(config.project_dir)
                / ".pio"
                / "build"
                / "esp32dev"
                / "firmware.bin"
            )
            return BuildResult(
                success=True,
                status=ResultStatus.SUCCESS,
                firmware_path=str(firmware),
                build_size_bytes=4096,
                platform="platformio",
                board="esp32dev",
                toolchain_version="mock",
            )
        if name == "board_detector":
            if self.detected_boards is not None:
                return self.detected_boards
            return [esp32_board()]
        if name == "flash_firmware":
            _, board, config, manager = args
            assert isinstance(board, BoardInfo)
            assert isinstance(config, FlashConfig)
            assert isinstance(manager, SubprocessManager)
            if self.flash_failure:
                return FlashResult(
                    success=False,
                    status=ResultStatus.FAILED,
                    port=board.port,
                    board=board.board_type.value,
                    message="mock flash failed",
                    failure=FailureResult(
                        category="FLASH_FAILED",
                        message="mock flash failed",
                        retryable=False,
                        stage="flash",
                    ),
                )
            return FlashResult(
                success=True,
                status=ResultStatus.SUCCESS,
                port=board.port,
                board=board.board_type.value,
                verification_status=VerificationStatus.PASSED,
                bytes_written=4096,
                tool="platformio",
                tool_version="mock",
            )
        if name == "serial_monitor":
            (config,) = args
            assert isinstance(config, SerialMonitorConfig)
            return ObserveResult(
                success=True,
                status=ResultStatus.SUCCESS,
                lines_captured=1,
                monitoring_duration_ms=10,
                termination_reason=ObserveTerminationReason.SUCCESS_PATTERN,
                matched_pattern="blink",
                port=config.port or "",
            )
        raise AssertionError(f"unexpected tool: {name}")


def esp32_board() -> BoardInfo:
    return BoardInfo(
        board_type=BoardType.ESP32,
        port="COM7",
        vid=0x10C4,
        pid=0xEA60,
        manufacturer="Mock",
        description="Mock ESP32",
        serial_number="ESP32-TEST",
    )


def dependencies(tmp_path: Path, llm: LLMService) -> tuple[
    CodeGenerationService,
    ProjectService,
    SubprocessManager,
]:
    generation = CodeGenerationService(llm)
    projects = ProjectService(
        tmp_path / "projects",
        code_generation_service=generation,
    )
    return generation, projects, SubprocessManager()


def install_mock_tools(monkeypatch: pytest.MonkeyPatch, tools: MockTools) -> None:
    for name in (
        "build_firmware",
        "board_detector",
        "flash_firmware",
        "serial_monitor",
    ):
        async def registered(
            *args: object,
            _name: str = name,
            **kwargs: object,
        ) -> object:
            return await tools(_name, *args, **kwargs)

        monkeypatch.setitem(tool_registry._TOOLS, name, registered)


def test_blink_led_workflow_runs_through_execution_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = Task(task_id="task-blink-led", prompt="Blink LED on ESP32")
    llm = MockLLM()
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools()
    install_mock_tools(monkeypatch, tools)

    outcome = run(
        execute_task(
            task,
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert outcome.plan.task_id == task.task_id
    assert outcome.plan.framework == "PlatformIO"
    assert outcome.plan.execution_steps == (
        ExecutionStep.GENERATE_CODE,
        ExecutionStep.BUILD_FIRMWARE,
        ExecutionStep.DETECT_BOARD,
        ExecutionStep.FLASH_FIRMWARE,
        ExecutionStep.START_MONITOR,
    )
    assert [result.step for result in outcome.step_results] == list(
        outcome.plan.execution_steps
    )
    assert all(result.success for result in outcome.step_results)
    assert [call[0] for call in tools.calls] == [
        "build_firmware",
        "board_detector",
        "flash_firmware",
        "serial_monitor",
    ]

    generated = outcome.step_results[0].result
    assert isinstance(generated, GenerateCodeResult)
    assert generated.generated_project is not None
    assert generated.execution_context is not None
    project_path = Path(generated.execution_context.project_path or "")
    assert project_path.is_dir()
    assert (project_path / "platformio.ini").is_file()
    assert (project_path / "src" / "main.cpp").is_file()
    assert len(llm.requests) == 1


def test_openrouter_generates_firmware_through_generate_code_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}
    generated_content = json.dumps(
        {
            "files": [
                {
                    "path": "platformio.ini",
                    "content": (
                        "[env:esp32dev]\n"
                        "platform = espressif32\n"
                        "board = esp32dev\n"
                        "framework = arduino\n"
                    ),
                },
                {
                    "path": "src/main.cpp",
                    "content": (
                        "#include <Arduino.h>\n"
                        "void setup() { pinMode(2, OUTPUT); }\n"
                        "void loop() { digitalWrite(2, !digitalRead(2)); delay(500); }\n"
                    ),
                },
            ]
        }
    )

    def openrouter(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-4.1-mini",
                "choices": [{"message": {"content": generated_content}}],
                "usage": {
                    "prompt_tokens": 200,
                    "completion_tokens": 100,
                    "total_tokens": 300,
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(openrouter))
    llm = OpenRouterService(
        api_key="test-openrouter-key",
        model="openai/gpt-4.1-mini",
        client=client,
    )
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools()
    install_mock_tools(monkeypatch, tools)

    outcome = run(
        execute_task(
            Task(
                task_id="task-openrouter-firmware",
                prompt="Generate ESP32 firmware that blinks the onboard LED",
            ),
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    request = seen["request"]
    payload = seen["payload"]
    assert isinstance(request, httpx.Request)
    assert isinstance(payload, dict)
    assert request.url == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer test-openrouter-key"
    assert payload["model"] == "openai/gpt-4.1-mini"
    assert [message["role"] for message in payload["messages"]] == [
        "system",
        "user",
    ]
    generated = outcome.step_results[0].result
    assert isinstance(generated, GenerateCodeResult)
    assert generated.execution_context is not None
    project_path = Path(generated.execution_context.project_path or "")
    assert (project_path / "platformio.ini").is_file()
    assert "digitalWrite" in (project_path / "src" / "main.cpp").read_text()
    run(client.aclose())


def test_execute_prompt_emits_ordered_progress_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = MockLLM()
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools()
    install_mock_tools(monkeypatch, tools)
    events: list[str] = []

    async def progress(event: str, payload: object) -> None:
        del payload
        events.append(event)

    outcome = run(
        execute_prompt(
            "Blink LED on ESP32",
            task_id="task-progress",
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
            progress_callback=progress,
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert events == [
        "TASK_CREATED",
        "PLAN_GENERATED",
        "CODE_GENERATION_STARTED",
        "CODE_GENERATION_COMPLETED",
        "BUILD_STARTED",
        "BUILD_COMPLETED",
        "FLASH_STARTED",
        "FLASH_COMPLETED",
        "MONITOR_STARTED",
        "WORKFLOW_COMPLETED",
    ]


@pytest.mark.parametrize(
    ("failure", "expected_step", "expected_tools"),
    [
        ("generation", ExecutionStep.GENERATE_CODE, []),
        ("build", ExecutionStep.BUILD_FIRMWARE, ["build_firmware"]),
        (
            "board",
            ExecutionStep.FLASH_FIRMWARE,
            ["build_firmware", "board_detector"],
        ),
        (
            "flash",
            ExecutionStep.FLASH_FIRMWARE,
            ["build_firmware", "board_detector", "flash_firmware"],
        ),
    ],
)
def test_workflow_surfaces_failures_and_stops_dependents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_step: ExecutionStep,
    expected_tools: list[str],
) -> None:
    llm = MockLLM(
        error=RuntimeError("mock LLM failure")
        if failure == "generation"
        else None
    )
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools(
        build_failure=failure == "build",
        detected_boards=[] if failure == "board" else None,
        flash_failure=failure == "flash",
    )
    install_mock_tools(monkeypatch, tools)

    outcome = run(
        execute_task(
            Task(task_id=f"task-{failure}", prompt="Blink LED on ESP32"),
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
        )
    )

    assert outcome.status is ExecutionStatus.FAILED
    assert outcome.failures[0].step is expected_step
    assert [call[0] for call in tools.calls] == expected_tools
