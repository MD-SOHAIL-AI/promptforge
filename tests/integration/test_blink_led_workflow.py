from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import AsyncIterator

import httpx
import pytest

from backend.agent.coordinator import ExecutionStatus
from backend.contracts.execution_plan import ExecutionPlan, ExecutionStep, TaskType
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
        attempt_type = request.metadata.get("attempt_type")
        target_file = request.metadata.get("target_file")
        if attempt_type == "requirements":
            content = json.dumps(
                {
                    "project_name": "ESP32 Advanced Standalone Control Hub",
                    "board": "esp32dev",
                    "framework": "arduino",
                    "platform": "espressif32",
                    "required_features": ["WiFi AP mode", "WebServer dashboard", "Preferences", "FreeRTOS tasks", "Serial commands", "REST APIs"],
                    "forbidden_features": ["external sensors", "OLED", "relays", "cloud", "MQTT", "Firebase", "Blynk"],
                    "required_files": ["platformio.ini", "include/config.h", "src/main.cpp", "README.md"],
                }
            )
            return LLMResponse(content=content, provider=self.provider, model=self.model)
        if attempt_type == "manifest":
            content = json.dumps(
                {
                    "project_name": "ESP32 Advanced Standalone Control Hub",
                    "files": [
                        {"path": "platformio.ini", "purpose": "PlatformIO build configuration", "required": True},
                        {"path": "include/config.h", "purpose": "Project constants", "required": True},
                        {"path": "src/main.cpp", "purpose": "Firmware implementation", "required": True},
                        {"path": "README.md", "purpose": "Usage documentation", "required": True},
                    ],
                    "build_target": {"platform": "espressif32", "board": "esp32dev", "framework": "arduino"},
                }
            )
            return LLMResponse(content=content, provider=self.provider, model=self.model)
        if target_file == "platformio.ini":
            return LLMResponse(
                content="[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n",
                provider=self.provider,
                model=self.model,
            )
        if target_file == "include/config.h":
            return LLMResponse(
                content="#pragma once\n#define AP_SSID \"ForgeX Hub\"\n#define AP_PASSWORD \"forgex123\"\n#define LED_PIN 2\n#define STATUS_PATH \"/api/status\"\n",
                provider=self.provider,
                model=self.model,
            )
        if target_file == "src/main.cpp":
            return LLMResponse(
                content=(
                    "#include <Arduino.h>\n#include <WiFi.h>\n#include <WebServer.h>\n#include <Preferences.h>\n#include \"config.h\"\n"
                    "WebServer server(80);\nPreferences preferences;\nbool ledState=false;\n"
                    "void statusTask(void*){for(;;){Serial.println(\"status\");vTaskDelay(pdMS_TO_TICKS(1000));}}\n"
                    "void handleStatus(){server.send(200,\"application/json\",\"{\\\"ok\\\":true}\");}\n"
                    "void handleLedOn(){ledState=true;digitalWrite(LED_PIN,HIGH);server.send(200,\"application/json\",\"{\\\"led\\\":true}\");}\n"
                    "void handleLedOff(){ledState=false;digitalWrite(LED_PIN,LOW);server.send(200,\"application/json\",\"{\\\"led\\\":false}\");}\n"
                    "void setup(){Serial.begin(115200);pinMode(LED_PIN,OUTPUT);preferences.begin(\"forgex\",false);WiFi.softAP(AP_SSID,AP_PASSWORD);"
                    "server.on(\"/api/status\",handleStatus);server.on(\"/api/led/on\",handleLedOn);server.on(\"/api/led/off\",handleLedOff);server.begin();"
                    "xTaskCreate(statusTask,\"status\",4096,nullptr,1,nullptr);}\n"
                    "void loop(){server.handleClient();if(Serial.available()){String c=Serial.readStringUntil('\\n');if(c==\"on\")handleLedOn();if(c==\"off\")handleLedOff();}}\n"
                ),
                provider=self.provider,
                model=self.model,
            )
        if target_file == "README.md":
            return LLMResponse(
                content=(
                    "# ESP32 Firmware\n\n## Overview\nESP32 control hub.\n\n## Features\nWiFi AP, WebServer, Preferences, FreeRTOS, serial commands, API endpoints.\n\n"
                    "## Build\nRun `platformio run`.\n\n## Upload\nRun `platformio run -t upload`.\n\n"
                    "## Serial Commands\nUse on/off.\n\n## API Endpoints\n/api/status\n/api/led/on\n/api/led/off\n\n## Troubleshooting\nOpen serial monitor.\n"
                ),
                provider=self.provider,
                model=self.model,
            )
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
                    {
                        "path": "README.md",
                        "content": "# ESP32 Firmware\n\nGenerated PlatformIO firmware project.\n",
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
    )
    assert [result.step for result in outcome.step_results] == list(
        outcome.plan.execution_steps
    )
    assert all(result.success for result in outcome.step_results)
    assert [call[0] for call in tools.calls] == [
        "build_firmware",
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
    generated_content = (
        "Here is a minimal PlatformIO project.\n```json\n"
        + json.dumps(
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
        + "\n```\n"
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


def test_execute_prompt_generates_into_active_external_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "opened-platformio"
    (workspace / "src").mkdir(parents=True)
    (workspace / "platformio.ini").write_text(
        "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n",
        encoding="utf-8",
    )
    (workspace / "src" / "main.cpp").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
    active_workspace = {
        "id": "external-opened-platformio",
        "name": "opened-platformio",
        "rootPath": str(workspace),
        "type": "external",
        "board": "ESP32",
        "framework": "PlatformIO",
        "platformioIniPath": str(workspace / "platformio.ini"),
        "has_platformio_ini": True,
        "project_type": "platformio",
    }
    llm = MockLLM()
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools()
    install_mock_tools(monkeypatch, tools)

    outcome = run(
        execute_prompt(
            "Create firmware for the active workspace",
            task_id="task-active-workspace",
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
            active_workspace=active_workspace,
            tool_executor=tools,
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    generated = outcome.step_results[0].result
    assert isinstance(generated, GenerateCodeResult)
    assert generated.project_path == str(workspace.resolve())
    assert generated.execution_context is not None
    assert generated.execution_context.project_path == str(workspace.resolve())
    assert (workspace / "platformio.ini").is_file()
    assert "digitalWrite" in (workspace / "src" / "main.cpp").read_text(encoding="utf-8")
    assert (workspace / ".promptforge-project.json").is_file()
    assert projects.projects_root.joinpath("opened-platformio").exists() is False
    build_call = next(call for call in tools.calls if call[0] == "build_firmware")
    config = build_call[1][0]
    assert isinstance(config, BuildConfig)
    assert Path(config.project_dir) == workspace.resolve()


def test_execute_prompt_generates_platformio_project_into_empty_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "empty-open-folder"
    workspace.mkdir()
    active_workspace = {
        "id": "external-empty-open-folder",
        "name": "empty-open-folder",
        "rootPath": str(workspace),
        "type": "external",
        "board": "UNKNOWN",
        "framework": "UNKNOWN",
        "selected_board": "esp32dev",
        "selected_framework": "PlatformIO",
        "generation_mode": "generate_into_open_folder",
        "platformioIniPath": str(workspace / "platformio.ini"),
        "has_platformio_ini": False,
        "project_type": "generic",
    }
    llm = MockLLM()
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools()
    install_mock_tools(monkeypatch, tools)

    outcome = run(
        execute_prompt(
            "Create firmware for the active workspace",
            task_id="task-empty-workspace",
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
            active_workspace=active_workspace,
            tool_executor=tools,
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    generated = outcome.step_results[0].result
    assert isinstance(generated, GenerateCodeResult)
    assert generated.project_path == str(workspace.resolve())
    assert (workspace / "platformio.ini").is_file()
    assert (workspace / "src" / "main.cpp").is_file()
    assert (workspace / ".promptforge-project.json").is_file()
    assert outcome.plan.target_board == "ESP32"
    assert outcome.plan.framework == "PlatformIO"
    llm_spec = json.loads(llm.requests[0].prompt.split("\n", 1)[1])
    assert llm_spec["platformio_board"] == "esp32dev"
    assert llm_spec["generation_mode"] == "generate_into_open_folder"
    assert projects.projects_root.joinpath("empty-open-folder").exists() is False
    build_call = next(call for call in tools.calls if call[0] == "build_firmware")
    config = build_call[1][0]
    assert isinstance(config, BuildConfig)
    assert Path(config.project_dir) == workspace.resolve()


def test_execute_task_builds_external_platformio_without_generated_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "external-build-only"
    (workspace / "src").mkdir(parents=True)
    (workspace / "platformio.ini").write_text(
        "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n",
        encoding="utf-8",
    )
    (workspace / "src" / "main.cpp").write_text(
        "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n",
        encoding="utf-8",
    )

    class BuildOnlyPlanner:
        def plan(self, prompt: str) -> ExecutionPlan:
            return ExecutionPlan(
                task_id="task-build-only",
                task_type=TaskType.FIRMWARE_GENERATION,
                target_board="ESP32",
                framework="PlatformIO",
                requirements=(prompt,),
                execution_steps=(ExecutionStep.BUILD_FIRMWARE,),
                confidence=0.9,
            )

    llm = MockLLM()
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools()
    install_mock_tools(monkeypatch, tools)
    task = Task(
        task_id="task-build-only",
        prompt="Build the active external PlatformIO project",
        metadata={
            "active_workspace": {
                "id": "external-build-only",
                "name": "external-build-only",
                "rootPath": str(workspace),
                "type": "external",
                "board": "ESP32",
                "framework": "PlatformIO",
                "platformioIniPath": str(workspace / "platformio.ini"),
                "has_platformio_ini": True,
                "project_type": "platformio",
            }
        },
    )

    outcome = run(
        execute_task(
            task,
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
            planner=BuildOnlyPlanner(),  # type: ignore[arg-type]
            tool_executor=tools,
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert "project must be a GeneratedProject" not in "\n".join(
        failure.message for failure in outcome.failures
    )
    build_call = next(call for call in tools.calls if call[0] == "build_firmware")
    config = build_call[1][0]
    assert isinstance(config, BuildConfig)
    assert Path(config.project_dir) == workspace.resolve()


def test_advanced_esp32_prompt_generates_required_workspace_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "advanced-esp32"
    workspace.mkdir()
    active_workspace = {
        "id": "advanced-esp32",
        "name": "advanced-esp32",
        "rootPath": str(workspace),
        "type": "external",
        "board": "UNKNOWN",
        "framework": "UNKNOWN",
        "selected_board": "esp32dev",
        "selected_framework": "PlatformIO",
        "generation_mode": "generate_into_open_folder",
        "platformioIniPath": str(workspace / "platformio.ini"),
        "has_platformio_ini": False,
        "project_type": "generic",
    }
    prompt = """Create an advanced ESP32-only PlatformIO project.

Project Name:
ESP32 Advanced Standalone Control Hub

Target:
- Board: esp32dev
- Framework: Arduino
- Platform: PlatformIO
- Hardware: ESP32 board only
- Do not use external sensors
- Do not use OLED/display
- Do not use relays
- Do not use extra modules
- Only use ESP32 built-in features:
  - WiFi
  - WebServer
  - Flash/NVS Preferences
  - FreeRTOS tasks
  - Serial Monitor
  - Onboard LED if available

Expected Output:
Generate the complete working PlatformIO project files.
Make sure the code builds without missing dependencies.
"""
    llm = MockLLM()
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools()
    install_mock_tools(monkeypatch, tools)

    outcome = run(
        execute_prompt(
            prompt,
            task_id="task-advanced-esp32",
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
            active_workspace=active_workspace,
            tool_executor=tools,
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert (workspace / "platformio.ini").is_file()
    assert (workspace / "src" / "main.cpp").is_file()
    assert (workspace / ".promptforge-project.json").is_file()
    build_result = next(
        item.result
        for item in outcome.step_results
        if item.step is ExecutionStep.BUILD_FIRMWARE
    )
    assert isinstance(build_result, BuildResult)
    assert build_result.success is True


def test_arduino_wording_uses_platformio_build_system_in_selected_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "arduino-platformio"
    workspace.mkdir()
    active_workspace = {
        "id": "arduino-platformio",
        "rootPath": str(workspace),
        "board": "UNKNOWN",
        "framework": "UNKNOWN",
        "selected_board": "esp32dev",
        "selected_framework": "PlatformIO",
        "generation_mode": "generate_into_open_folder",
        "has_platformio_ini": False,
        "project_type": "generic",
    }
    llm = MockLLM()
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools()
    install_mock_tools(monkeypatch, tools)

    outcome = run(
        execute_task(
            Task(
                task_id="task-arduino-platformio",
                prompt="Generate a basic ESP32 project using the Arduino framework. Create a simple WiFi web server that controls an LED from a browser.",
                metadata={"active_workspace": active_workspace},
            ),
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
            tool_executor=tools,
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert outcome.plan.framework == "PlatformIO"
    assert outcome.plan.metadata["requested_firmware_framework"] == "Arduino"
    assert (workspace / "platformio.ini").is_file()
    assert (workspace / "src" / "main.cpp").is_file()


def test_optional_flash_no_hardware_completes_with_pending_hardware(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OptionalFlashPlanner:
        def plan(self, prompt: str) -> ExecutionPlan:
            return ExecutionPlan(
                task_id="task-optional-hardware",
                task_type=TaskType.FIRMWARE_GENERATION,
                target_board="ESP32",
                framework="PlatformIO",
                requirements=(prompt,),
                execution_steps=(
                    ExecutionStep.GENERATE_CODE,
                    ExecutionStep.BUILD_FIRMWARE,
                    ExecutionStep.DETECT_BOARD,
                    ExecutionStep.FLASH_FIRMWARE,
                    ExecutionStep.START_MONITOR,
                ),
                confidence=0.9,
                metadata={"normalized_prompt": prompt},
            )

    llm = MockLLM()
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools(detected_boards=[])
    install_mock_tools(monkeypatch, tools)
    events: list[tuple[str, object]] = []

    async def progress(event: str, payload: object) -> None:
        events.append((event, payload))

    outcome = run(
        execute_task(
            Task(task_id="task-optional-hardware", prompt="Create ESP32 firmware"),
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
            planner=OptionalFlashPlanner(),  # type: ignore[arg-type]
            tool_executor=tools,
            progress_callback=progress,
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED_WITH_PENDING_HARDWARE
    assert outcome.failures == ()
    flash_step = next(item for item in outcome.step_results if item.step is ExecutionStep.FLASH_FIRMWARE)
    assert flash_step.success is True
    assert getattr(flash_step.result, "status") == "WAITING_FOR_DEVICE"
    assert not any(call[0] == "flash_firmware" for call in tools.calls)
    event_names = [event for event, _ in events]
    assert "FLASH_FAILED" not in event_names
    assert "FLASH_COMPLETED" in event_names
    flash_payload = dict(events[event_names.index("FLASH_COMPLETED")][1])  # type: ignore[arg-type]
    assert flash_payload["status"] == "WAITING_FOR_DEVICE"


def test_explicit_flash_no_hardware_still_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplicitFlashPlanner:
        def plan(self, prompt: str) -> ExecutionPlan:
            return ExecutionPlan(
                task_id="task-explicit-flash",
                task_type=TaskType.FLASH_ONLY,
                target_board="ESP32",
                framework="PlatformIO",
                requirements=(prompt,),
                execution_steps=(
                    ExecutionStep.DETECT_BOARD,
                    ExecutionStep.FLASH_FIRMWARE,
                ),
                confidence=0.9,
                metadata={"normalized_prompt": "flash esp32 firmware"},
            )

    llm = MockLLM()
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = MockTools(detected_boards=[])
    install_mock_tools(monkeypatch, tools)

    outcome = run(
        execute_task(
            Task(
                task_id="task-explicit-flash",
                prompt="Flash ESP32 firmware",
                metadata={
                    "firmware_path": str(tmp_path / "firmware.bin"),
                    "build_artifact": {
                        "path": str(tmp_path / "firmware.bin"),
                        "environment": "esp32dev",
                        "artifact_type": "bin",
                        "size_bytes": 1,
                    },
                },
            ),
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
            planner=ExplicitFlashPlanner(),  # type: ignore[arg-type]
            tool_executor=tools,
        )
    )

    assert outcome.status is ExecutionStatus.FAILED
    assert "no detected board matches ESP32" in outcome.failures[0].message


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
        "GENERATION_STRATEGY_SELECTED",
        "GENERATION_STARTED",
        "GENERATION_COMPLETED",
        "CODE_GENERATION_COMPLETED",
        "BUILD_STARTED",
        "BUILD_COMPLETED",
        "WORKFLOW_COMPLETED",
    ]


def test_execute_prompt_emits_generation_failed_progress_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = MockLLM(error=RuntimeError("mock LLM failure"))
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    install_mock_tools(monkeypatch, MockTools())
    events: list[tuple[str, object]] = []

    async def progress(event: str, payload: object) -> None:
        events.append((event, payload))

    outcome = run(
        execute_prompt(
            "Blink LED on ESP32",
            task_id="task-generation-failure-progress",
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
            progress_callback=progress,
        )
    )

    assert outcome.status is ExecutionStatus.FAILED
    names = [event for event, _ in events]
    assert "CODE_GENERATION_FAILED" in names
    failed_payload = dict(events[names.index("CODE_GENERATION_FAILED")][1])  # type: ignore[arg-type]
    assert failed_payload["success"] is False
    assert failed_payload["failure"] == {
        "category": "GENERATION_FAILED",
        "message": "mock LLM failure",
    }


@pytest.mark.parametrize(
    ("failure", "expected_step", "expected_tools"),
    [
        ("generation", ExecutionStep.GENERATE_CODE, []),
        ("build", ExecutionStep.BUILD_FIRMWARE, ["build_firmware"] * 4),
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


def test_build_failure_is_repaired_and_rebuilt_with_visible_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RepairingTools(MockTools):
        def __init__(self) -> None:
            super().__init__()
            self.build_attempts = 0

        async def __call__(self, name: str, *args: object, **kwargs: object) -> object:
            if name == "build_firmware":
                self.build_attempts += 1
                self.build_failure = self.build_attempts == 1
            return await super().__call__(name, *args, **kwargs)

    llm = MockLLM()
    generation, projects, subprocess_manager = dependencies(tmp_path, llm)
    tools = RepairingTools()
    install_mock_tools(monkeypatch, tools)
    events: list[str] = []

    async def progress(event: str, payload: object) -> None:
        del payload
        events.append(event)

    outcome = run(
        execute_task(
            Task(task_id="task-build-repair", prompt="Blink LED on ESP32"),
            code_generation_service=generation,
            project_service=projects,
            subprocess_manager=subprocess_manager,
            progress_callback=progress,
        )
    )

    assert outcome.status is ExecutionStatus.COMPLETED
    assert tools.build_attempts == 2
    assert "BUILD_REPAIR_STARTED" in events
    assert "BUILD_REPAIR_COMPLETED" in events
    assert "BUILD_REPAIR_FAILED" not in events
