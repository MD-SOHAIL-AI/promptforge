from __future__ import annotations

import asyncio
import json
from dataclasses import FrozenInstanceError
from typing import Any, AsyncIterator

import pytest

from backend.agent.planner import (
    ExecutionPlan,
    ExecutionStep,
    Framework,
    TargetBoard,
    TaskType,
)
from backend.contracts.execution_context import ExecutionContext
from backend.services.code_generation_service import (
    CodeGenerationRequest,
    CodeGenerationService,
    GeneratedFile,
    GeneratedOutputError,
    GeneratedProject,
    ProjectValidationError,
    UnsupportedGenerationTargetError,
)
from backend.services.llm_service import (
    LLMAuthenticationError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    LLMService,
    LLMTimeoutError,
)


class FakeLLMService(LLMService):
    provider = LLMProvider.OPENAI
    model = "fake-model"

    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            content=self.content,
            provider=self.provider,
            model=self.model,
            token_usage={},
            latency_ms=1,
        )

    async def generate_stream(self, request: LLMRequest) -> AsyncIterator[str]:
        if False:
            yield request.prompt

    async def health_check(self) -> bool:
        return True


class SequentialLLMService(FakeLLMService):
    def __init__(self, contents: list[str], *, provider: LLMProvider = LLMProvider.OPENAI, model: str = "fake-model") -> None:
        super().__init__(contents[0] if contents else "")
        self.contents = contents
        self.provider = provider
        self.model = model

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.contents) - 1)
        return LLMResponse(
            content=self.contents[index],
            provider=self.provider,
            model=self.model,
            token_usage={},
            latency_ms=1,
        )


def plan(
    *,
    board: TargetBoard = TargetBoard.ESP32,
    framework: Framework = Framework.PLATFORMIO,
    task_id: str = "task-123",
) -> ExecutionPlan:
    return ExecutionPlan(
        task_id=task_id,
        task_type=TaskType.FIRMWARE_GENERATION,
        target_board=board,
        framework=framework,
        requirements=("LED output", "serial output"),
        execution_steps=(ExecutionStep.GENERATE_CODE,),
        estimated_tools=(),
        confidence=0.9,
        metadata={"framework_inferred": False},
    )


def context(
    *,
    task_id: str = "task-123",
    board: str = "ESP32",
    framework: str = "PlatformIO",
    project_path: str | None = "workspace/projects/Blink Demo",
) -> ExecutionContext:
    return ExecutionContext(
        task_id=task_id,
        project_path=project_path,
        target_board=board,
        framework=framework,
        simulation_enabled=True,
        metadata={"pin": 2},
    )


def manifest(files: list[dict[str, str]]) -> str:
    return json.dumps({"files": files})


def platformio_files() -> list[dict[str, str]]:
    return [
        {
            "path": "platformio.ini",
            "content": "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\n",
        },
        {
            "path": "src/main.cpp",
            "content": "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n",
        },
    ]


def advanced_platformio_files() -> list[dict[str, str]]:
    return [
        platformio_files()[0],
        {
            "path": "src/main.cpp",
            "content": (
                "#include <Arduino.h>\n#include <WiFi.h>\n#include <WebServer.h>\n"
                "WebServer server(80);\nvoid setup() { pinMode(2, OUTPUT); digitalWrite(2, LOW); server.begin(); }\n"
                "void loop() { server.handleClient(); }\n"
            ),
        },
    ]


def readme_file() -> dict[str, str]:
    return {"path": "README.md", "content": "# ESP32 Project\n"}


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_generated_file_is_immutable_and_serializable() -> None:
    generated = GeneratedFile(path="src/main.cpp", content="int main() {}")

    assert GeneratedFile.from_dict(generated.to_dict()) == generated
    with pytest.raises(FrozenInstanceError):
        generated.path = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "path",
    [
        "",
        " /main.cpp",
        "/main.cpp",
        "../main.cpp",
        "src/../main.cpp",
        "src\\main.cpp",
        "src//main.cpp",
        "src/./main.cpp",
        "bad\x00.cpp",
        "C:/main.cpp",
        "src/bad?.cpp",
        "src/trailing.",
        "CON.txt",
    ],
)
def test_generated_file_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValueError, match="path"):
        GeneratedFile(path=path, content="content")


@pytest.mark.parametrize("content", [None, "bad\x00content"])
def test_generated_file_rejects_invalid_content(content: object) -> None:
    with pytest.raises(ValueError, match="content"):
        GeneratedFile(path="main.cpp", content=content)  # type: ignore[arg-type]


def test_generated_project_is_immutable_and_round_trips() -> None:
    project = GeneratedProject(
        project_name="blink-demo",
        framework=Framework.PLATFORMIO,
        target_board=TargetBoard.ESP32,
        files=tuple(GeneratedFile(**item) for item in platformio_files()),
    )

    assert GeneratedProject.from_dict(project.to_dict()) == project
    with pytest.raises(FrozenInstanceError):
        project.project_name = "changed"  # type: ignore[misc]


@pytest.mark.parametrize("name", ["", "Bad Name", "UPPER", "-bad", "bad-"])
def test_generated_project_rejects_invalid_names(name: str) -> None:
    with pytest.raises(ValueError, match="project_name"):
        GeneratedProject(
            project_name=name,
            framework=Framework.PLATFORMIO,
            target_board=TargetBoard.ESP32,
            files=(GeneratedFile("main.cpp", "content"),),
        )


def test_code_generation_request_requires_matching_types_and_identity() -> None:
    with pytest.raises(ValueError, match="ExecutionPlan"):
        CodeGenerationRequest(plan=object(), context=context())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="ExecutionContext"):
        CodeGenerationRequest(plan=plan(), context=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="task_id"):
        CodeGenerationRequest(plan=plan(), context=context(task_id="other"))
    with pytest.raises(ValueError, match="target_board"):
        CodeGenerationRequest(plan=plan(), context=context(board="ESP32-S3"))
    with pytest.raises(ValueError, match="framework"):
        CodeGenerationRequest(plan=plan(), context=context(framework="Arduino"))


def test_context_unknown_values_allow_plan_to_remain_authoritative() -> None:
    request = CodeGenerationRequest(
        plan=plan(),
        context=context(board="UNKNOWN", framework="UNKNOWN"),
    )

    assert request.plan.target_board is TargetBoard.ESP32


def test_simple_platformio_blink_uses_verified_builtin_scaffold() -> None:
    class RouterLikeLLM(FakeLLMService):
        registry = type("EmptyRegistry", (), {"list_providers": lambda self: []})()

        async def generate_model(self, request: object) -> object:
            raise AssertionError(f"model router should not be called: {request}")

    llm = RouterLikeLLM("this response must not be used")
    service = CodeGenerationService(llm)
    blink_context = ExecutionContext(
        task_id="task-123",
        project_path="workspace/projects/blink-demo",
        target_board="ESP32",
        framework="PlatformIO",
        metadata={"prompt": "Create an ESP32 blink LED project using PlatformIO."},
    )

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=blink_context)))

    assert project.list_files() == ("platformio.ini", "src/main.cpp")
    assert "board = esp32dev" in project.get_file("platformio.ini").content  # type: ignore[union-attr]
    assert "BLINK_INTERVAL_MS" in project.get_file("src/main.cpp").content  # type: ignore[union-attr]
    report = project.metadata["generation_report"]
    assert report["provider_id"] == "forgex_builtin"  # type: ignore[index]
    assert llm.requests == []


def test_service_requires_injected_llm_service() -> None:
    with pytest.raises(ValueError, match="LLMService"):
        CodeGenerationService(object())  # type: ignore[arg-type]


@pytest.mark.parametrize("timeout_s", [60, 180, 300])
def test_generation_wait_for_uses_configured_timeout(
    monkeypatch: pytest.MonkeyPatch,
    timeout_s: int,
) -> None:
    captured: list[float] = []

    async def recording_wait_for(awaitable: Any, *, timeout: float) -> Any:
        captured.append(timeout)
        return await awaitable

    monkeypatch.setattr(asyncio, "wait_for", recording_wait_for)
    service = CodeGenerationService(
        FakeLLMService(manifest(platformio_files())),
        timeout_s=timeout_s,
    )

    run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert captured == [float(timeout_s)]


@pytest.mark.parametrize(
    "timeout_s",
    [0, -1, float("inf"), float("nan"), True],
)
def test_generation_rejects_invalid_timeout(timeout_s: object) -> None:
    with pytest.raises(ValueError, match="timeout_s"):
        CodeGenerationService(
            FakeLLMService(manifest(platformio_files())),
            timeout_s=timeout_s,  # type: ignore[arg-type]
        )


def test_generation_timeout_preserves_structured_llm_error() -> None:
    class SlowLLMService(FakeLLMService):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            await asyncio.sleep(0.05)
            return await super().generate(request)

    service = CodeGenerationService(
        SlowLLMService(manifest(platformio_files())),
        timeout_s=0.001,
        max_attempts=1,
    )

    with pytest.raises(LLMTimeoutError) as exc_info:
        run(
            service.generate_project(
                CodeGenerationRequest(plan=plan(), context=context())
            )
        )

    assert exc_info.value.retryable is True
    assert exc_info.value.details == {
        "timeout_s": 0.001,
        "operation": "generation",
    }


def test_retryable_generation_failure_retries_then_succeeds() -> None:
    class FlakyLLMService(FakeLLMService):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            if len(self.requests) == 1:
                raise LLMTimeoutError(
                    "temporary timeout",
                    provider=self.provider,
                    retryable=True,
                )
            return LLMResponse(
                content=self.content,
                provider=self.provider,
                model=self.model,
            )

    llm = FlakyLLMService(manifest(platformio_files()))
    service = CodeGenerationService(llm, max_attempts=2, retry_backoff_s=0)

    project = run(
        service.generate_project(CodeGenerationRequest(plan=plan(), context=context()))
    )

    assert project.project_name == "blink-demo"
    assert len(llm.requests) == 2


def test_non_retryable_generation_failure_is_not_retried() -> None:
    class RejectedLLMService(FakeLLMService):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            raise LLMAuthenticationError(
                "invalid key",
                provider=self.provider,
            )

    llm = RejectedLLMService("unused")
    service = CodeGenerationService(llm, max_attempts=2, retry_backoff_s=0)

    with pytest.raises(LLMAuthenticationError):
        run(
            service.generate_project(
                CodeGenerationRequest(plan=plan(), context=context())
            )
        )

    assert len(llm.requests) == 1


def test_retryable_generation_failure_stops_at_attempt_limit() -> None:
    class FailingLLMService(FakeLLMService):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            self.requests.append(request)
            raise LLMTimeoutError(
                "provider unavailable",
                provider=self.provider,
                retryable=True,
            )

    llm = FailingLLMService("unused")
    service = CodeGenerationService(llm, max_attempts=2, retry_backoff_s=0)

    with pytest.raises(LLMTimeoutError):
        run(
            service.generate_project(
                CodeGenerationRequest(plan=plan(), context=context())
            )
        )

    assert len(llm.requests) == 2


def test_generate_project_calls_injected_service_once_and_builds_prompt() -> None:
    llm = FakeLLMService(manifest(platformio_files()))
    service = CodeGenerationService(llm, temperature=0.1, max_tokens=4000)
    request = CodeGenerationRequest(plan=plan(), context=context())

    project = run(service.generate_project(request))

    assert project.project_name == "blink-demo"
    assert project.framework is Framework.PLATFORMIO
    assert project.target_board is TargetBoard.ESP32
    assert len(project.files) == 2
    assert project.project_id.startswith("project-")
    assert project.created_at.tzinfo is not None
    assert project.metadata["task_id"] == "task-123"
    report = project.metadata["generation_report"]
    assert report["attempt_count"] == 1
    assert report["repair_used"] is False
    assert report["fallback_used"] is False
    assert project.list_files() == ("platformio.ini", "src/main.cpp")
    assert project.get_file("src/main.cpp") is project.files[1]
    assert tuple(item.file_type for item in project.files) == ("ini", "cpp")
    assert len(llm.requests) == 1
    sent = llm.requests[0]
    assert sent.temperature == 0.1
    assert sent.max_tokens == 4000
    assert sent.metadata["task_id"] == "task-123"
    assert sent.metadata["project_name"] == "blink-demo"
    assert sent.metadata["target_board"] == "ESP32"
    assert sent.metadata["framework"] == "PlatformIO"
    assert sent.metadata["attempt_type"] == "initial"
    assert sent.metadata["attempt_number"] == 1
    specification = json.loads(sent.prompt.split("\n", 1)[1])
    assert specification["requirements"] == ["LED output", "serial output"]
    assert specification["context_metadata"] == {"pin": 2}
    assert specification["simulation_enabled"] is True
    assert "platformio.ini" in sent.system_prompt
    assert "src/main.cpp" in sent.system_prompt


def test_missing_platformio_ini_triggers_repair_attempt() -> None:
    llm = SequentialLLMService(
        [
            manifest([{"path": "src/main.cpp", "content": "#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n"}]),
            manifest(platformio_files()),
        ]
    )
    service = CodeGenerationService(llm, retry_backoff_s=0)

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert project.list_files() == ("platformio.ini", "src/main.cpp")
    assert len(llm.requests) == 2
    assert llm.requests[1].metadata["attempt_type"] == "repair"
    report = project.metadata["generation_report"]
    assert report["attempt_count"] == 2
    assert report["repair_used"] is True
    assert report["fallback_used"] is False
    attempts = service.list_attempts(task_id="task-123")
    assert attempts[-2]["success"] is False
    assert attempts[-1]["success"] is True


def test_missing_src_main_cpp_triggers_repair_attempt() -> None:
    llm = SequentialLLMService(
        [
            manifest([{"path": "platformio.ini", "content": "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\n"}]),
            manifest(platformio_files()),
        ]
    )
    service = CodeGenerationService(llm, retry_backoff_s=0)

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert project.get_file("src/main.cpp") is not None
    assert len(llm.requests) == 2
    assert "src/main.cpp" in llm.requests[1].prompt


def test_unsafe_path_is_rejected_and_repaired() -> None:
    llm = SequentialLLMService(
        [
            manifest([{"path": "../src/main.cpp", "content": "bad"}]),
            manifest(platformio_files()),
        ]
    )
    service = CodeGenerationService(llm, retry_backoff_s=0)

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert project.list_files() == ("platformio.ini", "src/main.cpp")
    assert len(llm.requests) == 2
    attempts = project.metadata["generation_report"]["attempts"]
    assert attempts[0]["error_code"] == "INVALID_GENERATED_OUTPUT"


def test_repair_failure_returns_clear_generation_error() -> None:
    llm = SequentialLLMService(["This is only an explanation.", "Still no files."])
    service = CodeGenerationService(llm, retry_backoff_s=0, fallback_enabled=False)

    with pytest.raises(GeneratedOutputError) as exc_info:
        run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert "Try a stronger model or enable fallback" in str(exc_info.value)
    assert len(llm.requests) == 2
    assert len(exc_info.value.details["generation_attempts"]) == 2


def test_fallback_model_is_used_when_repair_fails() -> None:
    primary = SequentialLLMService(["Explanation only.", "Still invalid."], provider=LLMProvider.OPENAI, model="weak")
    fallback = SequentialLLMService([manifest(platformio_files())], provider=LLMProvider.OPENROUTER, model="strong")
    service = CodeGenerationService(
        primary,
        fallback_llm_service=fallback,
        retry_backoff_s=0,
    )

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert len(primary.requests) == 2
    assert len(fallback.requests) == 1
    report = project.metadata["generation_report"]
    assert report["fallback_used"] is True
    assert report["model_id"] == "strong"


def test_fallback_is_not_used_when_disabled() -> None:
    primary = SequentialLLMService(["Explanation only.", "Still invalid."])
    fallback = SequentialLLMService([manifest(platformio_files())], provider=LLMProvider.OPENROUTER, model="strong")
    service = CodeGenerationService(
        primary,
        fallback_llm_service=fallback,
        fallback_enabled=False,
        retry_backoff_s=0,
    )

    with pytest.raises(GeneratedOutputError):
        run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert len(primary.requests) == 2
    assert fallback.requests == []


def test_advanced_prompt_missing_readme_triggers_repair() -> None:
    advanced_plan = plan()
    advanced_context = ExecutionContext(
        task_id="task-123",
        project_path="workspace/projects/advanced",
        target_board="ESP32",
        framework="PlatformIO",
        metadata={
            "prompt": "Create an advanced ESP32 control hub with README and WebServer Preferences",
            "generation_strategy": "one_shot",
        },
    )
    llm = SequentialLLMService(
        [
            manifest(advanced_platformio_files()),
            manifest([*advanced_platformio_files(), readme_file()]),
        ]
    )
    service = CodeGenerationService(llm, retry_backoff_s=0)

    project = run(service.generate_project(CodeGenerationRequest(plan=advanced_plan, context=advanced_context)))

    assert project.get_file("README.md") is not None
    assert project.metadata["generation_report"]["repair_used"] is True


def test_advanced_prompt_missing_requested_capabilities_is_rejected() -> None:
    advanced_context = ExecutionContext(
        task_id="task-123",
        project_path="workspace/projects/advanced",
        target_board="ESP32",
        framework="PlatformIO",
        metadata={
            "prompt": "Create an advanced ESP32 control hub with README, WebServer, WiFi, and Preferences",
            "generation_strategy": "one_shot",
        },
    )
    llm = FakeLLMService(manifest([*platformio_files(), readme_file()]))
    service = CodeGenerationService(llm)

    with pytest.raises(ProjectValidationError, match="missing requested capabilities"):
        run(service.generate_project(CodeGenerationRequest(plan=plan(), context=advanced_context)))


def test_simple_blink_prompt_does_not_record_advanced_warning() -> None:
    llm = FakeLLMService(manifest(platformio_files()))
    service = CodeGenerationService(llm)

    project = run(service.generate_project(CodeGenerationRequest(plan=plan(), context=context())))

    assert tuple(project.metadata["warnings"]) == ()


def test_prompt_generation_is_deterministic() -> None:
    llm = FakeLLMService(manifest(platformio_files()))
    service = CodeGenerationService(llm)
    request = CodeGenerationRequest(plan=plan(), context=context())

    run(service.generate_project(request))
    run(service.generate_project(request))

    assert llm.requests[0].to_dict() == llm.requests[1].to_dict()


def test_project_name_falls_back_to_task_id() -> None:
    llm = FakeLLMService(manifest(platformio_files()))
    request = CodeGenerationRequest(plan=plan(), context=context(project_path=None))

    project = run(CodeGenerationService(llm).generate_project(request))

    assert project.project_name == "task-123"


@pytest.mark.parametrize(
    ("board", "framework"),
    [
        (TargetBoard.UNKNOWN, Framework.UNKNOWN),
        (TargetBoard.ESP32, Framework.STM32_CUBE),
        (TargetBoard.STM32, Framework.ARDUINO),
        (TargetBoard.ARDUINO_UNO, Framework.ESP_IDF),
    ],
)
def test_unsupported_target_fails_before_llm_call(
    board: TargetBoard,
    framework: Framework,
) -> None:
    llm = FakeLLMService(manifest(platformio_files()))
    request = CodeGenerationRequest(
        plan=plan(board=board, framework=framework),
        context=ExecutionContext(task_id="task-123"),
    )

    with pytest.raises(UnsupportedGenerationTargetError):
        run(CodeGenerationService(llm).generate_project(request))
    assert llm.requests == []


def test_extract_files_accepts_raw_json_and_single_json_fence() -> None:
    service = CodeGenerationService(FakeLLMService("unused"))
    raw = manifest(platformio_files())

    assert service.extract_files(raw) == service.extract_files(
        f"```json\n{raw}\n```"
    )
    assert service.extract_files(raw) == service.extract_files(
        f"Here is the project manifest:\n```json\n{raw}\n```\nBuild it with PlatformIO."
    )


def test_extract_files_accepts_markdown_file_blocks() -> None:
    service = CodeGenerationService(FakeLLMService("unused"))
    content = """```file:platformio.ini
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino
```

```file:src/main.cpp
#include <Arduino.h>
void setup() {}
void loop() {}
```"""

    files = service.extract_files(content)

    assert [item.path for item in files] == ["platformio.ini", "src/main.cpp"]
    assert files[0].file_type == "ini"
    assert "board = esp32dev" in files[0].content
    assert "void loop()" in files[1].content


def test_extract_files_accepts_heading_blocks() -> None:
    service = CodeGenerationService(FakeLLMService("unused"))
    content = """### platformio.ini
[env:esp32dev]
platform = espressif32
board = esp32dev
framework = arduino

### src/main.cpp
#include <Arduino.h>
void setup() {}
void loop() {}
"""

    files = service.extract_files(content)

    assert [item.path for item in files] == ["platformio.ini", "src/main.cpp"]
    assert files[0].content.startswith("[env:esp32dev]")
    assert files[1].content.endswith("void loop() {}")


@pytest.mark.parametrize(
    "content",
    [
        "",
        "not json",
        '{"files": []}',
        '{"files": "main.cpp"}',
        '{"files": [], "explanation": "done"}',
        'Here is the project: {"files": []}',
        '```json\n{"files": []}\n``` trailing',
    ],
)
def test_extract_files_rejects_malformed_output(content: str) -> None:
    service = CodeGenerationService(FakeLLMService("unused"))

    with pytest.raises(GeneratedOutputError):
        service.extract_files(content)


def test_extract_files_wraps_invalid_file_with_index() -> None:
    service = CodeGenerationService(FakeLLMService("unused"))
    content = manifest([{"path": "../main.cpp", "content": "bad"}])

    with pytest.raises(GeneratedOutputError, match="index 0"):
        service.extract_files(content)


def test_extract_files_rejects_unsafe_markdown_file_paths() -> None:
    service = CodeGenerationService(FakeLLMService("unused"))
    content = """```file:../platformio.ini
[env:esp32dev]
board = esp32dev
```"""

    with pytest.raises(GeneratedOutputError, match="index 0"):
        service.extract_files(content)


def test_validate_platformio_project() -> None:
    service = CodeGenerationService(FakeLLMService("unused"))
    project = GeneratedProject(
        "esp32-project",
        Framework.PLATFORMIO,
        TargetBoard.ESP32,
        tuple(GeneratedFile(**item) for item in platformio_files()),
    )

    assert service.validate_project(project) is None


@pytest.mark.parametrize(
    "files",
    [
        [{"path": "src/main.cpp", "content": "void setup() {}"}],
        [
            {"path": "platformio.ini", "content": "[env:test]\nboard = esp32dev"},
        ],
        [
            {"path": "platformio.ini", "content": "board = esp32dev"},
            {"path": "src/main.cpp", "content": "int main() {}"},
        ],
        [
            {"path": "platformio.ini", "content": "[env:test]"},
            {"path": "src/main.cpp", "content": "int main() {}"},
        ],
    ],
)
def test_validate_rejects_invalid_platformio_project(
    files: list[dict[str, str]],
) -> None:
    project = GeneratedProject(
        "project",
        Framework.PLATFORMIO,
        TargetBoard.ESP32,
        tuple(GeneratedFile(**item) for item in files),
    )

    with pytest.raises(ProjectValidationError):
        CodeGenerationService(FakeLLMService("unused")).validate_project(project)


@pytest.mark.parametrize(
    ("board", "board_id"),
    [
        (TargetBoard.ESP32, "esp32-s3-devkitc-1"),
        (TargetBoard.ESP32_S3, "esp32dev"),
        (TargetBoard.ESP32_C3, "esp32dev"),
        (TargetBoard.ARDUINO_UNO, "esp32dev"),
    ],
)
def test_platformio_board_must_match_target_family(
    board: TargetBoard,
    board_id: str,
) -> None:
    project = GeneratedProject(
        "project",
        Framework.PLATFORMIO,
        board,
        (
            GeneratedFile(
                "platformio.ini",
                f"[env:test]\nplatform = test\nboard = {board_id}\n",
            ),
            GeneratedFile("src/main.cpp", "int main() {}"),
        ),
    )

    with pytest.raises(ProjectValidationError, match="incompatible"):
        CodeGenerationService(FakeLLMService("unused")).validate_project(project)


@pytest.mark.parametrize(
    ("board", "files"),
    [
        (
            TargetBoard.ARDUINO_UNO,
            [{"path": "blink.ino", "content": "void setup() {}\nvoid loop() {}"}],
        ),
        (
            TargetBoard.ESP32_S3,
            [{"path": "main.ino", "content": "void setup() {}\nvoid loop() {}"}],
        ),
        (
            TargetBoard.ESP32_C3,
            [{"path": "app.ino", "content": "void setup() {}\nvoid loop() {}"}],
        ),
    ],
)
def test_validate_supported_arduino_projects(
    board: TargetBoard,
    files: list[dict[str, str]],
) -> None:
    project = GeneratedProject(
        "arduino-project",
        Framework.ARDUINO,
        board,
        tuple(GeneratedFile(**item) for item in files),
    )

    assert CodeGenerationService(FakeLLMService("unused")).validate_project(project) is None


@pytest.mark.parametrize(
    "content",
    [
        "void setup() {}",
        "void loop() {}",
    ],
)
def test_validate_arduino_requires_setup_and_loop(content: str) -> None:
    project = GeneratedProject(
        "arduino-project",
        Framework.ARDUINO,
        TargetBoard.ARDUINO_UNO,
        (GeneratedFile("main.ino", content),),
    )

    with pytest.raises(ProjectValidationError):
        CodeGenerationService(FakeLLMService("unused")).validate_project(project)


def test_validate_esp_idf_project() -> None:
    project = GeneratedProject(
        "idf-project",
        Framework.ESP_IDF,
        TargetBoard.ESP32_C3,
        (
            GeneratedFile("CMakeLists.txt", "project(app)"),
            GeneratedFile("main/CMakeLists.txt", "idf_component_register(SRCS main.c)"),
            GeneratedFile("main/main.c", "void app_main(void) {}"),
        ),
    )

    assert CodeGenerationService(FakeLLMService("unused")).validate_project(project) is None


def test_validate_esp_idf_requires_app_main() -> None:
    project = GeneratedProject(
        "idf-project",
        Framework.ESP_IDF,
        TargetBoard.ESP32,
        (
            GeneratedFile("CMakeLists.txt", "project(app)"),
            GeneratedFile("main/CMakeLists.txt", "idf_component_register(SRCS main.c)"),
            GeneratedFile("main/main.c", "int main(void) { return 0; }"),
        ),
    )

    with pytest.raises(ProjectValidationError, match="app_main"):
        CodeGenerationService(FakeLLMService("unused")).validate_project(project)


def test_validate_stm32_cube_project() -> None:
    project = GeneratedProject(
        "stm32-project",
        Framework.STM32_CUBE,
        TargetBoard.STM32,
        (
            GeneratedFile("Core/Src/main.c", "int main(void) { while (1) {} }"),
            GeneratedFile("Core/Inc/main.h", "#pragma once"),
        ),
    )

    assert CodeGenerationService(FakeLLMService("unused")).validate_project(project) is None


def test_validate_rejects_case_insensitive_duplicate_paths() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        GeneratedProject(
            "project",
            Framework.PLATFORMIO,
            TargetBoard.ESP32,
            (
                GeneratedFile(
                    "platformio.ini",
                    "[env:test]\nboard = esp32dev",
                ),
                GeneratedFile("src/main.cpp", "int main() {}"),
                GeneratedFile("SRC/Main.cpp", "int other() {}"),
            ),
        )


def test_generate_project_validates_llm_output_before_returning() -> None:
    llm = FakeLLMService(
        manifest([{"path": "src/main.cpp", "content": "int main() {}"}])
    )
    request = CodeGenerationRequest(plan=plan(), context=context())

    with pytest.raises(ProjectValidationError, match="platformio.ini"):
        run(CodeGenerationService(llm).generate_project(request))


def test_generate_project_reports_missing_src_main() -> None:
    llm = FakeLLMService(
        manifest(
            [
                {
                    "path": "platformio.ini",
                    "content": "[env:esp32dev]\nboard = esp32dev\n",
                }
            ]
        )
    )
    request = CodeGenerationRequest(plan=plan(), context=context())

    with pytest.raises(ProjectValidationError, match="src/main.cpp"):
        run(CodeGenerationService(llm).generate_project(request))
