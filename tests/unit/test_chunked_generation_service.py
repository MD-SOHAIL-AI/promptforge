from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from backend.agent.planner import ExecutionPlan, ExecutionStep, Framework, TargetBoard, TaskType
from backend.contracts.execution_context import ExecutionContext
from backend.services.code_generation_service import (
    CodeGenerationRequest,
    CodeGenerationService,
    ProjectValidationError,
)
from backend.services.generation_diagnostics_store import GenerationDiagnosticsStore
from backend.services.llm_service import LLMProvider, LLMProviderError, LLMRequest, LLMResponse, LLMService


class SequentialLLM(LLMService):
    provider = LLMProvider.OPENAI
    model = "openai/gpt-oss-120b:free"

    def __init__(self, outputs: list[str], *, provider: LLMProvider = LLMProvider.OPENAI, model: str = "openai/gpt-oss-120b:free") -> None:
        self.outputs = outputs
        self.provider = provider
        self.model = model
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.outputs) - 1)
        return LLMResponse(self.outputs[index], self.provider, self.model, latency_ms=1)

    async def generate_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        yield (await self.generate(request)).content

    async def health_check(self) -> bool:
        return True


class UnavailableLLM(LLMService):
    provider = LLMProvider.OPENROUTER
    model = "openai/gpt-oss-120b:free"

    async def generate(self, request: LLMRequest) -> LLMResponse:
        raise LLMProviderError(
            "OpenRouter connection failed",
            provider=self.provider,
            retryable=True,
            details={"request_reached_provider": False},
        )

    async def generate_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        raise NotImplementedError

    async def health_check(self) -> bool:
        return False


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def plan() -> ExecutionPlan:
    return ExecutionPlan(
        task_id="task-chunked",
        task_type=TaskType.FIRMWARE_GENERATION,
        target_board=TargetBoard.ESP32,
        framework=Framework.PLATFORMIO,
        requirements=("advanced control hub",),
        execution_steps=(ExecutionStep.GENERATE_CODE,),
        confidence=0.9,
    )


def simple_plan() -> ExecutionPlan:
    return ExecutionPlan(
        task_id="task-simple",
        task_type=TaskType.FIRMWARE_GENERATION,
        target_board=TargetBoard.ESP32,
        framework=Framework.PLATFORMIO,
        requirements=("blink led",),
        execution_steps=(ExecutionStep.GENERATE_CODE,),
        confidence=0.9,
    )


def context() -> ExecutionContext:
    return ExecutionContext(
        task_id="task-chunked",
        project_path="workspace/projects/advanced",
        target_board="ESP32",
        framework="PlatformIO",
        metadata={
            "prompt": (
                "Create an advanced ESP32-only PlatformIO project with WiFi AP, "
                "WebServer dashboard, Preferences, FreeRTOS tasks, serial commands, "
                "REST API endpoints, embedded UI, and README."
            )
        },
    )


def simple_context(workspace_root: Path) -> ExecutionContext:
    return ExecutionContext(
        task_id="task-simple",
        project_path=str(workspace_root),
        target_board="ESP32",
        framework="PlatformIO",
        metadata={
            "prompt": "Create an ESP32 blink LED PlatformIO project for esp32dev.",
            "generation_strategy": "one_shot",
            "execution_id": "exec-simple",
        },
    )


def context_at(workspace_root: Path) -> ExecutionContext:
    return ExecutionContext(
        task_id="task-chunked",
        project_path=str(workspace_root),
        target_board="ESP32",
        framework="PlatformIO",
        metadata={
            "prompt": (
                "Create an advanced ESP32-only PlatformIO project with WiFi AP, "
                "WebServer dashboard, Preferences, FreeRTOS tasks, serial commands, "
                "REST API endpoints, embedded UI, and README."
            ),
            "active_workspace": {
                "rootPath": str(workspace_root),
                "generation_mode": "generate_into_open_folder",
            },
            "execution_id": "exec-chunked",
        },
    )


def summary_json() -> str:
    return json.dumps(
        {
            "project_name": "ESP32 Advanced Standalone Control Hub",
            "board": "esp32dev",
            "framework": "arduino",
            "platform": "espressif32",
            "required_features": ["WiFi AP mode", "WebServer dashboard", "Preferences/NVS", "FreeRTOS tasks", "Serial commands", "REST APIs"],
            "forbidden_features": ["external sensors", "OLED", "relays", "cloud", "MQTT", "Firebase", "Blynk"],
            "required_files": ["platformio.ini", "include/config.h", "src/main.cpp", "README.md"],
        }
    )


def manifest_json() -> str:
    return json.dumps(
        {
            "project_name": "ESP32 Advanced Standalone Control Hub",
            "files": [
                {"path": "platformio.ini", "purpose": "PlatformIO build configuration", "required": True},
                {"path": "include/config.h", "purpose": "constants", "required": True},
                {"path": "src/main.cpp", "purpose": "firmware", "required": True},
                {"path": "README.md", "purpose": "docs", "required": True},
            ],
            "build_target": {"platform": "espressif32", "board": "esp32dev", "framework": "arduino"},
        }
    )


def platformio_ini() -> str:
    return "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n"


def config_h() -> str:
    return "#pragma once\n#define AP_SSID \"ForgeX Hub\"\n#define AP_PASSWORD \"forgex123\"\n#define LED_PIN 2\n#define STATUS_PATH \"/api/status\"\n"


def main_cpp() -> str:
    return r'''#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include "config.h"

WebServer server(80);
Preferences preferences;
bool ledState = false;

void statusTask(void *param) {
  for (;;) {
    Serial.println("status task");
    vTaskDelay(pdMS_TO_TICKS(1000));
  }
}

void handleStatus() { server.send(200, "application/json", "{\"ok\":true}"); }
void handleLedOn() { ledState = true; digitalWrite(LED_PIN, HIGH); server.send(200, "application/json", "{\"led\":true}"); }
void handleLedOff() { ledState = false; digitalWrite(LED_PIN, LOW); server.send(200, "application/json", "{\"led\":false}"); }
void handleRoot() { server.send(200, "text/html", "<html><body><h1>Dashboard</h1></body></html>"); }

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  preferences.begin("forgex", false);
  WiFi.softAP(AP_SSID, AP_PASSWORD);
  server.on("/", handleRoot);
  server.on("/api/status", handleStatus);
  server.on("/api/led/on", handleLedOn);
  server.on("/api/led/off", handleLedOff);
  server.begin();
  xTaskCreate(statusTask, "status", 4096, nullptr, 1, nullptr);
}

void loop() {
  server.handleClient();
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    if (command == "on") handleLedOn();
    if (command == "off") handleLedOff();
  }
}
'''


def readme_md() -> str:
    return """# ESP32 Advanced Standalone Control Hub

## Overview
Standalone ESP32 web dashboard.

## Features
WiFi AP, WebServer, Preferences, FreeRTOS, serial commands, and API endpoints.

## Build
Run `platformio run`.

## Upload
Run `platformio run -t upload`.

## Serial Commands
Use `on` and `off`.

## API Endpoints
- /api/status
- /api/led/on
- /api/led/off

## Troubleshooting
Check serial output.
"""


def one_shot_project_json() -> str:
    return json.dumps(
        {
            "project_name": "esp32-blink-led",
            "files": [
                {"path": "platformio.ini", "content": platformio_ini()},
                {"path": "src/main.cpp", "content": "#include <Arduino.h>\nvoid setup(){pinMode(2,OUTPUT);}\nvoid loop(){digitalWrite(2,HIGH);delay(500);digitalWrite(2,LOW);delay(500);}\n"},
            ],
        }
    )


def test_chunked_generation_creates_required_files() -> None:
    llm = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), config_h(), main_cpp(), readme_md()])
    service = CodeGenerationService(llm)

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert project.list_files() == ("platformio.ini", "include/config.h", "src/main.cpp", "README.md")
    report = project.metadata["generation_report"]
    assert report["generation_mode"] == "chunked"
    assert report["chunked_generation"]["failed_files"] == ()
    assert len(report["chunked_generation"]["file_statuses"]) == 4


def test_one_shot_generation_emits_basic_live_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events: list[dict[str, Any]] = []
    llm = SequentialLLM([one_shot_project_json()])
    service = CodeGenerationService(llm)

    project = run(
        service.generate_project(
            CodeGenerationRequest(
                plan=simple_plan(),
                context=simple_context(workspace),
                generation_event_callback=lambda event: events.append(dict(event)),
            )
        )
    )

    assert project.get_file("src/main.cpp") is not None
    event_types = [event["event_type"] for event in events]
    assert event_types == [
        "generation_strategy_selected",
        "generation_started",
        "generation_completed",
    ]


def test_missing_file_content_triggers_file_repair() -> None:
    llm = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), "```cpp\n\n```", config_h(), main_cpp(), readme_md()])
    service = CodeGenerationService(llm)

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert project.get_file("include/config.h") is not None
    attempts = project.metadata["generation_report"]["attempts"]
    assert any(item["attempt_type"] == "file_repair" and item["success"] for item in attempts)


def test_file_fallback_is_used_without_restarting_whole_project() -> None:
    primary = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), config_h(), "void setup(){}\nvoid loop(){}\n", "void setup(){}\nvoid loop(){}\n", readme_md()])
    fallback = SequentialLLM([main_cpp()], provider=LLMProvider.OPENROUTER, model="strong-model")
    service = CodeGenerationService(primary, fallback_llm_service=fallback)

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert project.get_file("src/main.cpp") is not None
    assert len(fallback.requests) == 1
    statuses = project.metadata["generation_report"]["chunked_generation"]["file_statuses"]
    src_status = next(item for item in statuses if item["path"] == "src/main.cpp")
    assert src_status["fallback_used"] is True


def test_truncated_main_cpp_triggers_repair() -> None:
    truncated = "#include <Arduino.h>\nvoid setup() {\n"
    llm = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), config_h(), truncated, main_cpp(), readme_md()])
    service = CodeGenerationService(llm, fallback_enabled=False)

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    attempts = project.metadata["generation_report"]["attempts"]
    assert any(item["attempt_type"] == "file_repair" and item["success"] for item in attempts)


def test_advanced_prompt_rejects_blink_only_main_cpp() -> None:
    blink = "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n"
    llm = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), config_h(), blink, blink])
    service = CodeGenerationService(llm, fallback_enabled=False)

    with pytest.raises(ProjectValidationError, match="src/main.cpp"):
        run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))


def test_chunked_generation_writes_validated_files_incrementally(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = GenerationDiagnosticsStore(tmp_path / "diagnostics")
    llm = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), config_h(), main_cpp(), readme_md()])
    service = CodeGenerationService(llm, diagnostics_store=store)

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context_at(workspace))))

    assert (workspace / "platformio.ini").read_text(encoding="utf-8").strip() == platformio_ini().strip()
    assert (workspace / "include" / "config.h").read_text(encoding="utf-8").strip() == config_h().strip()
    assert "WebServer server" in (workspace / "src" / "main.cpp").read_text(encoding="utf-8")
    statuses = project.metadata["generation_report"]["chunked_generation"]["file_statuses"]
    assert all(item["status"] == "written" for item in statuses)
    assert all("new_hash" in item for item in statuses)
    runs = store.list_chunked_runs(execution_id="exec-chunked")
    assert runs[-1]["status"] == "success"
    assert len(runs[-1]["file_statuses"]) == 4


def test_chunked_generation_emits_live_progress_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events: list[dict[str, Any]] = []

    async def on_event(event: dict[str, Any]) -> None:
        if event.get("event_type") == "file_written":
            path = event.get("file_path")
            assert isinstance(path, str)
            assert (workspace / path).is_file()
        events.append(dict(event))

    llm = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), config_h(), main_cpp(), readme_md()])
    service = CodeGenerationService(llm)

    run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context_at(workspace), generation_event_callback=on_event)))

    event_types = [event["event_type"] for event in events]
    assert event_types[0] == "generation_strategy_selected"
    assert "requirements_extraction_started" in event_types
    assert "requirements_extracted" in event_types
    assert "manifest_generation_started" in event_types
    assert "manifest_created" in event_types
    assert event_types.count("file_generation_started") == 4
    assert event_types.count("file_generation_validated") == 4
    assert event_types.count("file_written") == 4
    assert event_types[-1] == "generation_completed"


def test_file_repair_emits_live_repair_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events: list[dict[str, Any]] = []
    llm = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), "```cpp\n\n```", config_h(), main_cpp(), readme_md()])
    service = CodeGenerationService(llm)

    run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context_at(workspace), generation_event_callback=lambda event: events.append(dict(event)))))

    repair_events = [event for event in events if event.get("event_type") == "file_generation_repair_started"]
    assert repair_events
    assert repair_events[0]["file_path"] == "include/config.h"


def test_failed_middle_file_rolls_back_workspace_and_marks_incomplete(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = GenerationDiagnosticsStore(tmp_path / "diagnostics")
    blink = "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n"
    llm = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), config_h(), blink, blink])
    service = CodeGenerationService(llm, diagnostics_store=store, fallback_enabled=False)

    with pytest.raises(ProjectValidationError, match="src/main.cpp"):
        run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context_at(workspace))))

    assert not (workspace / "platformio.ini").exists()
    assert not (workspace / "include" / "config.h").exists()
    assert not (workspace / "src" / "main.cpp").exists()
    runs = store.list_chunked_runs(execution_id="exec-chunked")
    assert runs[-1]["status"] == "incomplete"
    assert runs[-1]["failed_files"] == ["src/main.cpp"]


def test_failed_chunked_generation_restores_existing_workspace_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "include").mkdir(parents=True)
    (workspace / "platformio.ini").write_text("original ini\n", encoding="utf-8")
    (workspace / "include" / "config.h").write_text("original config\n", encoding="utf-8")
    blink = "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n"
    llm = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), config_h(), blink, blink])
    service = CodeGenerationService(llm, fallback_enabled=False)

    with pytest.raises(ProjectValidationError, match="src/main.cpp"):
        run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context_at(workspace))))

    assert (workspace / "platformio.ini").read_text(encoding="utf-8") == "original ini\n"
    assert (workspace / "include" / "config.h").read_text(encoding="utf-8") == "original config\n"


def test_provider_failure_uses_complete_wifi_monitor_fallback(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events: list[dict[str, Any]] = []
    service = CodeGenerationService(UnavailableLLM(), retry_backoff_s=0)

    project = run(service.generate_project(CodeGenerationRequest(
        plan=plan(),
        context=context_at(workspace),
        generation_event_callback=lambda event: events.append(dict(event)),
    )))

    paths = {item.path for item in project.files}
    assert paths == {"platformio.ini", "include/config.h", "src/main.cpp", "README.md"}
    assert "WebServer server(80)" in project.get_file("src/main.cpp").content
    assert project.metadata["generation_report"]["fallback_used"] is True
    assert [event["event_type"] for event in events][-3:] == [
        "workspace_rolled_back", "fallback_started", "fallback_completed",
    ]
    

def test_incomplete_generation_emits_live_incomplete_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    events: list[dict[str, Any]] = []
    blink = "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n"
    llm = SequentialLLM([summary_json(), manifest_json(), platformio_ini(), config_h(), blink, blink])
    service = CodeGenerationService(llm, fallback_enabled=False)

    with pytest.raises(ProjectValidationError, match="src/main.cpp"):
        run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context_at(workspace), generation_event_callback=lambda event: events.append(dict(event)))))

    event_types = [event["event_type"] for event in events]
    assert "file_failed" in event_types
    assert event_types[-2:] == ["generation_incomplete", "workspace_rolled_back"]
    assert events[-1]["rollback_performed"] is True
