from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from backend.agent.coordinator import ExecutionOutcome, ExecutionStatus
from backend.api.app import create_app
from backend.contracts.execution_plan import ExecutionPlan, TaskType
from backend.contracts.generated_project import GeneratedFile, GeneratedProject
from backend.runtime.result import (
    BuildResult,
    FlashResult,
    ResultStatus,
    VerificationStatus,
)
from backend.runtime.subprocess_mgr import SubprocessManager
from backend.services.platformio_service import PlatformIOService
from backend.services.project_service import ProjectService
from backend.services.serial_service import SerialConfiguration
from backend.tools.board_detector import BoardDetector, BoardInfo, BoardType
from backend.utils.paths import PathManager
from backend.workspace.workspace_manager import WorkspaceManager


class FakePlatformIO(PlatformIOService):
    def __init__(self, manager: SubprocessManager, result: BuildResult) -> None:
        super().__init__(manager)
        self.result = result
        self.calls: list[tuple[str, str | None]] = []

    async def build(
        self,
        project: object,
        *,
        environment: str | None = None,
        **kwargs: object,
    ) -> BuildResult:
        del kwargs
        self.calls.append((str(project), environment))
        return self.result


class FakeBoardDetector(BoardDetector):
    def __init__(self, boards: list[BoardInfo]) -> None:
        self.boards = boards

    def detect_boards(self) -> list[BoardInfo]:
        return list(self.boards)


class FakeSerialService:
    def __init__(self, configuration: SerialConfiguration) -> None:
        self.configuration = configuration
        self.state = SimpleNamespace(name="DISCONNECTED")
        self.active_port: str | None = None
        self.metrics = SimpleNamespace(snapshot=lambda: {"lines_received": 0})

    async def connect(self) -> object:
        self.state = SimpleNamespace(name="CONNECTED")
        self.active_port = self.configuration.port or "COM-AUTO"
        return object()

    async def disconnect(self) -> object:
        self.state = SimpleNamespace(name="DISCONNECTED")
        self.active_port = None
        return object()

    def is_connected(self) -> bool:
        return self.state.name == "CONNECTED"


def successful_build(firmware: Path) -> BuildResult:
    firmware.parent.mkdir(parents=True, exist_ok=True)
    firmware.write_bytes(b"firmware")
    return BuildResult(
        success=True,
        status=ResultStatus.SUCCESS,
        duration_ms=12,
        message="Build completed",
        firmware_path=str(firmware),
        build_size_bytes=8,
        platform="platformio",
        board="esp32dev",
        toolchain_version="test",
    )


def project_service(tmp_path: Path) -> tuple[ProjectService, str]:
    service = ProjectService(tmp_path / "projects")
    project = GeneratedProject(
        project_id="project-api-test",
        project_name="api-test",
        target_board="ESP32",
        framework="PlatformIO",
        files=(
            GeneratedFile(
                path="platformio.ini",
                content="[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino",
            ),
            GeneratedFile(path="src/main.cpp", content="void setup() {}\nvoid loop() {}"),
        ),
        created_at=datetime.now(timezone.utc),
    )
    asyncio.run(service.create_project(project))
    return service, project.project_id


def completed_outcome(task_id: str) -> ExecutionOutcome:
    plan = ExecutionPlan(
        task_id=task_id,
        task_type=TaskType.FIRMWARE_GENERATION,
        target_board="ESP32",
        framework="PlatformIO",
        requirements=(),
        execution_steps=(),
        confidence=1.0,
    )
    return ExecutionOutcome(
        plan=plan,
        status=ExecutionStatus.COMPLETED,
        step_results=(),
        failures=(),
        execution_time=0.012,
    )


def make_client(
    tmp_path: Path,
    *,
    execute_prompt_fn: Any | None = None,
    build_result: BuildResult | None = None,
    boards: list[BoardInfo] | None = None,
    tool_executor: Any | None = None,
) -> tuple[TestClient, str, FakePlatformIO]:
    projects, project_id = project_service(tmp_path)
    manager = SubprocessManager()
    platformio = FakePlatformIO(
        manager,
        build_result or successful_build(tmp_path / "firmware" / "firmware.bin"),
    )

    async def default_execute(prompt: str, **kwargs: object) -> ExecutionOutcome:
        del prompt
        callback = kwargs["progress_callback"]
        task_id = str(kwargs["task_id"])
        await callback("TASK_CREATED", {"task_id": task_id})
        await callback("PLAN_GENERATED", {"task_id": task_id})
        await callback("WORKFLOW_COMPLETED", {"task_id": task_id, "status": "COMPLETED"})
        return completed_outcome(task_id)

    async def default_tool(name: str, *args: object, **kwargs: object) -> FlashResult:
        del args, kwargs
        assert name == "flash_firmware"
        return FlashResult(
            success=True,
            status=ResultStatus.SUCCESS,
            duration_ms=20,
            message="Flash completed",
            port="COM7",
            board="ESP32",
            verification_status=VerificationStatus.PASSED,
            bytes_written=8,
            tool="esptool",
            tool_version="test",
        )

    app = create_app(
        code_generation_service=object(),  # Existing boundary is replaced in route tests.
        project_service=projects,
        subprocess_manager=manager,
        platformio_service=platformio,
        board_detector=FakeBoardDetector(boards or []),
        execute_prompt_fn=execute_prompt_fn or default_execute,
        tool_executor=tool_executor or default_tool,
        serial_service_factory=FakeSerialService,
        workspace_manager=WorkspaceManager(PathManager(tmp_path / "workspace-root")),
        version="test-version",
    )
    return TestClient(app, raise_server_exceptions=False), project_id, platformio


def test_health_and_openapi(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    with client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
        assert response.json()["version"] == "test-version"

        document = client.get("/openapi.json").json()
        for path in (
            "/health",
            "/execute",
            "/projects",
            "/projects/{project_id}",
            "/projects/{project_id}/files",
            "/files/content",
            "/files",
            "/build-history",
            "/logs",
            "/build",
            "/flash",
            "/monitor/start",
            "/monitor/stop",
            "/monitor/status",
        ):
            assert path in document["paths"]
        assert document["paths"]["/execute"]["post"]["requestBody"]


def test_execute_and_request_ids(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    with client:
        response = client.post(
            "/execute",
            headers={"X-Request-ID": "request-test"},
            json={"prompt": "Blink LED on ESP32", "task_id": "task-api-test"},
        )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "request-test"
    assert response.headers["X-Execution-ID"].startswith("execution-")
    assert response.headers["X-Workflow-Correlation-ID"].startswith("workflow-")
    assert response.json() == {
        "status": "RUNNING",
        "task_id": "task-api-test",
        "execution_time_ms": response.json()["execution_time_ms"],
        "steps": [],
        "failures": [],
    }


def test_execute_validation_and_failure_redaction(tmp_path: Path) -> None:
    async def failing_execute(prompt: str, **kwargs: object) -> ExecutionOutcome:
        del prompt, kwargs
        raise RuntimeError("secret provider response")

    client, _, _ = make_client(tmp_path, execute_prompt_fn=failing_execute)
    with client:
        invalid = client.post("/execute", json={"prompt": "   ", "extra": True})
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "VALIDATION_ERROR"

        failed = client.post("/execute", json={"prompt": "test"})
        assert failed.status_code == 200
        assert failed.headers["X-Request-ID"].startswith("request-")
        payload = failed.json()
        assert payload["status"] == "RUNNING"
        assert payload["task_id"].startswith("task-")
        assert payload["steps"] == []
        assert payload["failures"] == []
        assert "secret" not in failed.text


def test_project_routes(tmp_path: Path) -> None:
    client, project_id, _ = make_client(tmp_path)
    with client:
        listed = client.get("/projects")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1
        assert listed.json()["projects"][0]["project_id"] == project_id

        loaded = client.get(f"/projects/{project_id}")
        assert loaded.status_code == 200
        assert loaded.json()["project_name"] == "api-test"

        missing = client.get("/projects/project-missing")
        assert missing.status_code == 404
        assert missing.json()["code"] == "PROJECT_NOT_FOUND"

        deleted = client.delete(f"/projects/{project_id}")
        assert deleted.json() == {"project_id": project_id, "deleted": True}
        assert client.get(f"/projects/{project_id}").status_code == 404


def test_workspace_file_routes(tmp_path: Path) -> None:
    client, project_id, _ = make_client(tmp_path)
    with client:
        entries = client.get(f"/projects/{project_id}/files")
        assert entries.status_code == 200
        paths = {item["path"]: item["kind"] for item in entries.json()["entries"]}
        assert paths["src"] == "folder"
        assert paths["src/main.cpp"] == "file"

        content = client.get(
            "/files/content",
            params={"project_id": project_id, "path": "src/main.cpp"},
        )
        assert content.status_code == 200
        assert "void setup" in content.json()["content"]

        created = client.post(
            "/files",
            json={
                "project_id": project_id,
                "path": "include/config.h",
                "content": "",
                "file_type": "h",
            },
        )
        assert created.status_code == 200
        assert any(item["path"] == "include/config.h" for item in created.json()["entries"])

        saved = client.put(
            "/files",
            json={
                "project_id": project_id,
                "path": "include/config.h",
                "content": "#pragma once\n",
            },
        )
        assert saved.status_code == 200
        assert saved.json()["content"] == "#pragma once\n"

        renamed = client.put(
            "/files",
            json={
                "project_id": project_id,
                "path": "include/config.h",
                "new_path": "include/settings.h",
            },
        )
        assert renamed.status_code == 200
        assert renamed.json()["path"] == "include/settings.h"

        deleted = client.delete(
            "/files",
            params={"project_id": project_id, "path": "include/settings.h"},
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted_count"] == 1


def test_workspace_build_history_and_logs(tmp_path: Path) -> None:
    client, project_id, _ = make_client(tmp_path)
    with client:
        build = client.post("/build", json={"project_id": project_id})
        assert build.status_code == 200

        history = client.get("/build-history")
        assert history.status_code == 200
        assert history.json()["count"] == 1
        assert history.json()["builds"][0]["execution_id"] == f"build-{project_id}"
        assert history.json()["builds"][0]["duration_ms"] == 12

        logs = client.get("/logs")
        assert logs.status_code == 200
        assert logs.json()["count"] >= 1
        assert any(item["log_type"] == "build" for item in logs.json()["logs"])


def test_build_success_and_missing_project(tmp_path: Path) -> None:
    client, project_id, platformio = make_client(tmp_path)
    with client:
        response = client.post("/build", json={"project_id": project_id})
        assert response.status_code == 200
        assert response.json()["result"]["success"] is True
        assert platformio.calls[0][1] is None

        missing = client.post("/build", json={"project_id": "project-missing"})
        assert missing.status_code == 404


def test_flash_success_build_failure_and_board_mismatch(tmp_path: Path) -> None:
    board = BoardInfo(
        board_type=BoardType.ESP32,
        port="COM7",
        vid=1,
        pid=2,
        manufacturer="test",
        description="test",
        serial_number="test",
    )
    client, project_id, _ = make_client(tmp_path, boards=[board])
    with client:
        response = client.post(
            "/flash",
            json={"project_id": project_id, "board_type": "ESP32", "port": "COM7"},
        )
        assert response.status_code == 200
        assert response.json()["flash"]["success"] is True

        mismatch = client.post(
            "/flash",
            json={"project_id": project_id, "board_type": "STM32", "port": "COM7"},
        )
        assert mismatch.status_code == 409
        assert mismatch.json()["code"] == "BOARD_TYPE_MISMATCH"

    failed_build = BuildResult(
        success=False,
        status=ResultStatus.FAILED,
        message="Compiler failed",
    )
    client, project_id, _ = make_client(tmp_path / "failed", build_result=failed_build, boards=[board])
    with client:
        response = client.post(
            "/flash",
            json={"project_id": project_id, "board_type": "ESP32", "port": "COM7"},
        )
        assert response.status_code == 200
        assert response.json()["build"]["success"] is False
        assert response.json()["flash"] is None


def test_monitor_lifecycle_and_conflict(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    with client:
        initial = client.get("/monitor/status")
        assert initial.json()["connected"] is False

        started = client.post("/monitor/start", json={"port": "COM7", "baudrate": 115200})
        assert started.status_code == 200
        assert started.json()["state"] == "CONNECTED"

        repeated = client.post("/monitor/start", json={"port": "COM7", "baudrate": 115200})
        assert repeated.status_code == 200

        conflict = client.post("/monitor/start", json={"port": "COM8", "baudrate": 115200})
        assert conflict.status_code == 409

        stopped = client.post("/monitor/stop")
        assert stopped.status_code == 200
        assert stopped.json()["connected"] is False
