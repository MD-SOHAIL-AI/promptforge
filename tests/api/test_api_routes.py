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
        if not (Path(str(project)) / "platformio.ini").is_file():
            raise ValueError("missing platformio.ini")
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
    subprocess_manager: SubprocessManager | None = None,
    terminal_service: Any | None = None,
) -> tuple[TestClient, str, FakePlatformIO]:
    projects, project_id = project_service(tmp_path)
    manager = subprocess_manager or SubprocessManager()
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
        terminal_service=terminal_service,
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
        assert response.json()["runtime_contract"] == "forgex-agent-runtime-v2"
        assert len(response.json()["source_fingerprint"]) == 64

        document = client.get("/openapi.json").json()
        for path in (
            "/health",
            "/execute",
            "/projects",
            "/projects/import",
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
            "/execute/{task_id}/cancel",
            "/terminal/profiles",
            "/terminal/sessions",
            "/terminal/sessions/{session_id}/output",
            "/terminal/sessions/{session_id}/input",
            "/terminal/sessions/{session_id}/clear",
            "/terminal/sessions/{session_id}/resize",
            "/terminal/sessions/{session_id}",
        ):
            assert path in document["paths"]
        assert document["paths"]["/execute"]["post"]["requestBody"]


def test_terminal_profiles_expose_default_and_capability(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    with client:
        response = client.get("/terminal/profiles")

    assert response.status_code == 200
    payload = response.json()
    assert payload["default_profile_id"]
    assert payload["profiles"]
    assert any(profile["default"] for profile in payload["profiles"])
    assert {profile["process_capability"] for profile in payload["profiles"]} <= {"conpty", "pipe"}


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


def test_external_project_import_files_and_build(tmp_path: Path) -> None:
    external = tmp_path / "external" / "smart-irrigation-system"
    (external / "src").mkdir(parents=True)
    (external / ".pio" / "build").mkdir(parents=True)
    (external / "platformio.ini").write_text(
        "\n".join(
            [
                "[env:esp32dev]",
                "platform = espressif32",
                "board = esp32dev",
                "framework = arduino",
                "monitor_speed = 115200",
                "upload_speed = 921600",
                "lib_deps =",
                "    adafruit/DHT sensor library",
                "    adafruit/Adafruit Unified Sensor",
                "",
                "[env:uno]",
                "platform = atmelavr",
                "board = uno",
                "framework = arduino",
            ]
        ),
        encoding="utf-8",
    )
    (external / "src" / "main.cpp").write_text("void setup() {}\nvoid loop() {}\n", encoding="utf-8")
    (external / ".pio" / "build" / "ignored.txt").write_text("ignored", encoding="utf-8")

    client, _, platformio = make_client(tmp_path)
    with client:
        imported = client.post("/projects/import", json={"path": str(external)})
        assert imported.status_code == 200
        payload = imported.json()
        assert payload["name"] == "smart-irrigation-system"
        assert payload["external"] is True
        assert payload["project_type"] == "platformio"
        assert payload["board"] == "esp32dev"
        assert payload["framework"] == "arduino"
        assert payload["environment"] == "esp32dev"
        assert payload["monitor_speed"] == 115200
        assert payload["upload_speed"] == 921600
        assert len(payload["platformio"]["environments"]) == 2
        project_id = payload["project_id"]

        projects = client.get("/projects").json()["projects"]
        external_project = next(item for item in projects if item["project_id"] == project_id)
        assert external_project["external"] is True
        assert external_project["metadata"]["active_environment"] == "esp32dev"

        entries = client.get(f"/projects/{project_id}/files")
        assert entries.status_code == 200
        paths = {item["path"] for item in entries.json()["entries"]}
        assert "platformio.ini" in paths
        assert "src/main.cpp" in paths
        assert ".pio/build/ignored.txt" not in paths

        content = client.get("/files/content", params={"project_id": project_id, "path": "src/main.cpp"})
        assert content.status_code == 200
        assert "void setup" in content.json()["content"]

        saved = client.put(
            "/files",
            json={"project_id": project_id, "path": "src/main.cpp", "content": "void setup() {}\n"},
        )
        assert saved.status_code == 200
        assert (external / "src" / "main.cpp").read_text(encoding="utf-8") == "void setup() {}\n"

        traversal = client.get("/files/content", params={"project_id": project_id, "path": "../outside.txt"})
        assert traversal.status_code == 422

        build = client.post("/build", json={"project_id": project_id, "environment": "esp32dev"})
        assert build.status_code == 200
        assert platformio.calls[-1] == (str(external.resolve()), "esp32dev")


def test_external_project_import_opens_generic_folder(tmp_path: Path) -> None:
    unsupported = tmp_path / "notes"
    unsupported.mkdir()
    (unsupported / "readme.txt").write_text("notes", encoding="utf-8")
    client, _, _ = make_client(tmp_path)
    with client:
        response = client.post("/projects/import", json={"path": str(unsupported)})
        assert response.status_code == 200
        payload = response.json()
        assert payload["project_type"] == "generic"
        assert payload["has_platformio_ini"] is False
        entries = client.get(f"/projects/{payload['project_id']}/files")
        assert entries.status_code == 200
        assert {item["path"] for item in entries.json()["entries"]} == {"readme.txt"}

        build = client.post("/build", json={"project_id": payload["project_id"]})
        assert build.status_code == 422
        assert build.json()["message"].startswith("Build requires platformio.ini")


def test_execute_passes_selected_board_for_generic_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "generic"
    workspace.mkdir()
    captured: dict[str, object] = {}

    async def execute_with_capture(prompt: str, **kwargs: object) -> ExecutionOutcome:
        del prompt
        captured.update(kwargs)
        callback = kwargs["progress_callback"]
        task_id = str(kwargs["task_id"])
        await callback("TASK_CREATED", {"task_id": task_id})
        await callback("WORKFLOW_COMPLETED", {"task_id": task_id, "status": "COMPLETED"})
        return completed_outcome(task_id)

    client, _, _ = make_client(tmp_path, execute_prompt_fn=execute_with_capture)
    with client:
        imported = client.post("/projects/import", json={"path": str(workspace)})
        project_id = imported.json()["project_id"]
        response = client.post(
            "/execute",
            json={
                "prompt": "Create firmware",
                "task_id": "task-selected-board",
                "project_id": project_id,
                "selected_board": "esp32dev",
                "selected_framework": "PlatformIO",
                "generation_mode": "generate_into_open_folder",
            },
        )
    assert response.status_code == 200
    active_workspace = captured["active_workspace"]
    assert isinstance(active_workspace, dict)
    assert active_workspace["rootPath"] == str(workspace.resolve())
    assert active_workspace["selected_board"] == "esp32dev"
    assert active_workspace["board"] == "ESP32"
    assert active_workspace["generation_mode"] == "generate_into_open_folder"


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

        missing = client.post(
            "/flash",
            json={"project_id": project_id, "board_type": "ESP32", "port": "COM9"},
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == "BOARD_NOT_FOUND"
        assert missing.json()["message"] == "No matching ESP32 device detected. Connect a board and try again."

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
