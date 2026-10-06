from pathlib import Path
import asyncio

import pytest

from backend.agent_runtime.capability_profiles import ASK_PROFILE, BUILD_ONLY_PROFILE, BUILD_REPAIR_PROFILE, PLAN_PROFILE, SIMULATE_PROFILE
from backend.agent_runtime.embedded_context import inspect_embedded_project
from backend.agent_runtime.session_store import AgentSessionStore
from backend.agent_runtime.tool_contracts import ToolName


def test_capability_profiles_keep_one_loop_but_separate_permissions() -> None:
    assert ToolName.READ_FILE in ASK_PROFILE.allowed_tools
    assert ToolName.WRITE_FILE not in ASK_PROFILE.allowed_tools
    assert ToolName.UPDATE_PLAN in PLAN_PROFILE.allowed_tools
    assert ToolName.WRITE_FILE not in PLAN_PROFILE.allowed_tools
    assert ToolName.BUILD_FIRMWARE in BUILD_ONLY_PROFILE.allowed_tools
    assert ToolName.WRITE_FILE not in BUILD_ONLY_PROFILE.allowed_tools
    assert ToolName.WRITE_FILE in BUILD_REPAIR_PROFILE.allowed_tools
    assert ToolName.BUILD_FIRMWARE in BUILD_REPAIR_PROFILE.allowed_tools
    assert ToolName.RUN_SIMULATION in BUILD_REPAIR_PROFILE.allowed_tools
    assert ToolName.RUN_SIMULATION in SIMULATE_PROFILE.allowed_tools
    assert ToolName.WRITE_FILE not in SIMULATE_PROFILE.allowed_tools
    assert ToolName.RUN_COMMAND not in BUILD_REPAIR_PROFILE.allowed_tools


def test_plan_binding_rejects_stale_or_tampered_approval(tmp_path: Path) -> None:
    store = AgentSessionStore(tmp_path / "sessions.json")
    session = store.create(project_id="project-1")
    session = store.record_plan(
        session.session_id,
        run_id="agent-run-1",
        steps=[{"text": "Inspect GPIO assignments", "status": "completed"}, {"text": "Implement OLED support", "status": "pending"}],
    )
    plan = session.current_plan
    assert plan is not None
    approved = store.approve_plan(session.session_id, plan_id=plan.plan_id, revision=plan.revision, plan_hash=plan.plan_hash)
    assert approved.status == "approved"
    with pytest.raises(ValueError, match="AGENT_PLAN_BINDING_MISMATCH"):
        store.approve_plan(session.session_id, plan_id=plan.plan_id, revision=plan.revision, plan_hash="0" * 64)


def test_embedded_context_extracts_platformio_gpio_and_conflicts(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "platformio.ini").write_text("[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n", encoding="utf-8")
    (tmp_path / "src" / "main.cpp").write_text(
        "#include <Arduino.h>\n#define LED_PIN 21\n#define OLED_SDA_PIN 21\nconst int SENSOR_PIN = 34;\nvoid setup(){ Serial.begin(115200); Wire.begin(21,22); }\nvoid loop(){ analogRead(SENSOR_PIN); }\n",
        encoding="utf-8",
    )
    graph = inspect_embedded_project(tmp_path)
    assert graph.board == "esp32dev"
    assert graph.framework == "arduino"
    assert any(pin.pin == 21 and pin.symbol == "LED_PIN" for pin in graph.pins)
    assert any("GPIO21" in warning for warning in graph.warnings)
    assert "I2C/Wire" in graph.buses
    assert "ADC" in graph.buses


def test_read_only_profile_returns_model_answer_without_changes(tmp_path: Path) -> None:
    from backend.agent_runtime.tool_runtime import ForgeXToolRuntime
    from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
    from backend.changes import ChangeSetService

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "src").mkdir()
    (workspace / "src" / "main.cpp").write_text("int value = 42;\n", encoding="utf-8")
    changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")
    runtime = ForgeXToolRuntime(active_workspace_root=workspace, change_service=changes)

    class Planner:
        provider_id = "test"
        model_id = "test-model"
        turn = 0

        def request_turn(self, **kwargs):
            self.turn += 1
            if self.turn == 1:
                return ProductToolPlan.parse({
                    "version": PRODUCT_TOOLPLAN_VERSION,
                    "summary": "Inspecting the implementation",
                    "tool_calls": [{"id": "read_1", "type": "read_file", "arguments": {"path": "src/main.cpp"}}],
                    "final": False,
                })
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION,
                "summary": "The file defines value as 42.",
                "tool_calls": [],
                "final": True,
            })

    result = runtime.run_product(
        task="What does main.cpp contain?",
        planner=Planner(),
        policy=ASK_PROFILE.policy(),
        allow_no_changes_success=True,
    )
    assert result.classification.value == "TOOL_RUNTIME_PASS"
    assert result.change_set_id is None
    assert result.assistant_message == "The file defines value as 42."
    assert (workspace / "src" / "main.cpp").read_text(encoding="utf-8") == "int value = 42;\n"


def test_file_runtime_retries_unverified_multifile_draft(tmp_path: Path) -> None:
    from backend.agent_runtime.tool_runtime import ForgeXToolRuntime, ProductRuntimeLimits
    from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
    from backend.agent_runtime.tool_policy import product_agent_policy
    from backend.changes import ChangeSetService

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    changes = ChangeSetService(state_root=tmp_path / "changes", staging_root=tmp_path / "staging")

    class Writer:
        provider_id = "test"

        def request_turn(self, **kwargs):
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION, "summary": "Writing project files",
                "tool_calls": [
                    {"id": "a", "type": "write_file", "arguments": {"path": "src/main.cpp", "content": "int main() {}\n"}},
                    {"id": "b", "type": "write_file", "arguments": {"path": "include/app.h", "content": "#pragma once\n"}},
                ], "final": False,
            })

    first = ForgeXToolRuntime(active_workspace_root=workspace, change_service=changes).run_product(
        task="Create project", planner=Writer(), policy=product_agent_policy(),
        limits=ProductRuntimeLimits(max_turns=1),
    )
    assert first.change_set_id
    assert changes.get(first.change_set_id).status == "draft"
    assert list(workspace.iterdir()) == []

    class Finisher:
        provider_id = "test"

        def request_turn(self, **kwargs):
            assert any(item.get("tool") == "previous_run_failure" for item in kwargs["tool_results"])
            assert "RECOVERED STAGED WORK" in kwargs["task"]
            assert "src/main.cpp" in kwargs["task"]
            assert "include/app.h" in kwargs["task"]
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION, "summary": "Files repaired",
                "tool_calls": [], "final": True,
            })

    second = ForgeXToolRuntime(active_workspace_root=workspace, change_service=changes).run_product(
        task="Create project", planner=Finisher(), policy=product_agent_policy(),
        retry_change_set_id=first.change_set_id,
        retry_context={"classification": "AGENT_RUNTIME_MAX_TURNS"},
    )
    assert second.classification.value == "TOOL_RUNTIME_PASS"
    assert second.change_set_id
    assert {item.path for item in changes.get(second.change_set_id).changed_files} == {"src/main.cpp", "include/app.h"}


def test_failed_build_uses_registry_evidence_and_skips_unchanged_rebuild(tmp_path: Path) -> None:
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.runtime.subprocess_mgr import ProcessResult

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.cpp").write_text("#include <DHT.h>\n", encoding="utf-8")

    class FailedBuild:
        success = False
        status = "failed"
        message = "Compilation failed"
        firmware_path = None
        build_size_bytes = None
        board = "esp32dev"
        platform = "espressif32"
        warnings_count = 0
        process_result = ProcessResult(
            returncode=1, stdout=b"Could not find the package with 'Adafruit DHT Sensor Library' requirements\n",
            stderr=b"fatal error: DHT.h: No such file or directory\n", args=["pio"], elapsed_s=0.1,
        )

        def api_dict(self):
            return {"success": False, "failure": {"category": "COMPILATION_ERROR"}}

    class PlatformIO:
        builds = 0
        searches = []

        async def build(self, root, *, environment=None):
            self.builds += 1
            return FailedBuild()

        async def search_libraries(self, query, *, limit=5):
            self.searches.append(query)
            return ("adafruit/DHT sensor library",)

    async def progress(event, payload):
        pass

    async def scenario():
        platformio = PlatformIO()
        loop = AutonomousForgeAgentLoop(
            workspace_root=tmp_path, planner=object(), platformio=platformio,
            progress=progress,
        )
        first = await loop._build(None)
        second = await loop._build(None)
        assert platformio.builds == 1
        assert platformio.searches == ["header:DHT.h", "DHT Sensor Library"]
        assert first["output"]["dependency_candidates"]
        assert second["output"]["cached"] is True

    asyncio.run(scenario())


def test_missing_platformio_package_search_uses_package_name() -> None:
    from backend.agent_runtime.autonomous_loop import _build_failure_summary, _dependency_search_queries

    diagnostics = "UnknownPackageError: Could not find the package with 'adafruit/Adafruit_BH1750 @ ^1.0.3' requirements"
    assert _dependency_search_queries(diagnostics) == ("BH1750",)
    assert _dependency_search_queries(
        "UnknownPackageError: Could not find the package with 'sparkfun/BH1750' requirements"
    ) == ("BH1750",)
    summary = _build_failure_summary({"diagnostics": diagnostics, "dependency_candidates": [
        {"query": "BH1750", "package": "claws/BH1750", "source": "platformio_registry"}
    ]})
    assert "platformio.ini" in summary["next_action"]
    assert "dependency_candidates" in summary["next_action"]


def test_build_retries_use_budget_not_repeated_call_limit() -> None:
    from backend.agent_runtime.canonical_runtime import LoopGuard, ToolInvocation
    from backend.agent_runtime.tool_contracts import ProductToolCall

    guard = LoopGuard(max_same_call_repeats=2, max_tool_calls=5)
    for index in range(5):
        call = ProductToolCall(call_id=f"build_{index}", tool_type="build_firmware", arguments={})
        guard.admit(ToolInvocation.from_product_call(
            call, turn=index + 1, step=index + 1, attempt_id=f"attempt_{index}", invocation_index=0,
        ))
    with pytest.raises(RuntimeError, match="AGENT_RUNTIME_TOOL_BUDGET_EXCEEDED"):
        guard.admit(ToolInvocation.from_product_call(
            ProductToolCall(call_id="build_6", tool_type="build_firmware", arguments={}),
            turn=6, step=6, attempt_id="attempt_6", invocation_index=0,
        ))


def test_real_workspace_change_resets_repeated_read_limit() -> None:
    from backend.agent_runtime.canonical_runtime import LoopGuard, ToolInvocation
    from backend.agent_runtime.tool_contracts import ProductToolCall

    guard = LoopGuard(max_same_call_repeats=2)
    def read(index: int) -> ToolInvocation:
        return ToolInvocation.from_product_call(
            ProductToolCall(call_id=f"read_{index}", tool_type="read_file", arguments={"path": "src/main.cpp"}),
            turn=index, step=index, attempt_id=f"attempt_{index}", invocation_index=0,
        )

    guard.admit(read(1))
    guard.admit(read(2))
    with pytest.raises(RuntimeError, match="AGENT_RUNTIME_REPEATED_CALL"):
        guard.admit(read(3))
    guard.workspace_changed()
    guard.admit(read(4))


def test_noop_edits_report_no_change_and_do_not_rewrite_file(tmp_path: Path) -> None:
    from backend.agent_runtime.tool_contracts import ProductToolCall
    from backend.agent_runtime.tool_executor import ForgeXToolExecutor
    from backend.agent_runtime.tool_policy import product_agent_policy

    source = tmp_path / "src" / "main.cpp"
    source.parent.mkdir()
    source.write_text("int pin = 4;\n", encoding="utf-8")
    executor = ForgeXToolExecutor(write_root=tmp_path, policy=product_agent_policy())
    before = source.stat().st_mtime_ns
    edit = executor.execute(ProductToolCall(
        call_id="noop_edit", tool_type="edit_file_simple",
        arguments={"path": "src/main.cpp", "old_text": "pin = 4", "new_text": "pin = 4"},
    ).as_runtime_call())
    write = executor.execute(ProductToolCall(
        call_id="noop_write", tool_type="write_file",
        arguments={"path": "src/main.cpp", "content": "int pin = 4;\n"},
    ).as_runtime_call())
    assert edit["modified"] is False and edit["diff"] == ""
    assert write["modified"] is False and write["diff"] == ""
    assert source.stat().st_mtime_ns == before


def test_autonomous_loop_extends_turns_while_files_keep_changing(tmp_path: Path) -> None:
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
    from backend.agent_runtime.tool_policy import product_agent_policy

    class Planner:
        def request_turn(self, **kwargs):
            turn = kwargs["turn"]
            if turn == 4:
                return ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION,
                    "summary": "Done", "tool_calls": [], "final": True})
            return ProductToolPlan.parse({"version": PRODUCT_TOOLPLAN_VERSION,
                "summary": "Writing another file",
                "tool_calls": [{"id": f"write_{turn}", "type": "write_file",
                    "arguments": {"path": f"src/file{turn}.cpp", "content": f"int value{turn};\n"}}],
                "final": False})

    events = []

    async def progress(event, payload):
        events.append(event)

    result = asyncio.run(AutonomousForgeAgentLoop(
        workspace_root=tmp_path, planner=Planner(), platformio=object(),
        progress=progress, policy=product_agent_policy(),
        max_turns=2, max_progress_extension_turns=2,
    ).run(task="Create three files", require_build=False))
    assert result.success is True
    assert "AGENT_TURN_BUDGET_EXTENDED" in events
    assert len(list((tmp_path / "src").glob("*.cpp"))) == 3


def test_autonomous_loop_never_substitutes_template_for_invalid_model_output(tmp_path: Path) -> None:
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.tool_policy import product_agent_policy

    workspace = tmp_path / "empty-project"
    workspace.mkdir()

    class BrokenPlanner:
        provider_id = "openrouter"
        model_id = "minimax/minimax-m3:free"

        def request_turn(self, **kwargs):
            raise ValueError("provider_response_content_invalid")

    class BuildResult:
        success = True
        status = "success"
        message = "Build succeeded"
        build_size_bytes = 1234
        board = "esp32dev"
        platform = "platformio"
        warnings_count = 0
        process_result = None

        def __init__(self, firmware_path: str) -> None:
            self.firmware_path = firmware_path

        def api_dict(self) -> dict[str, object]:
            return {
                "success": True,
                "status": "success",
                "message": self.message,
                "firmware_path": self.firmware_path,
                "build_size_bytes": self.build_size_bytes,
                "board": self.board,
                "platform": self.platform,
                "warnings_count": self.warnings_count,
            }

    class FakePlatformIO:
        async def build(self, root: Path, *, environment: str | None = None):
            del environment
            artifact = root / ".pio" / "build" / "esp32dev" / "firmware.bin"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"firmware")
            return BuildResult(str(artifact))

    events: list[tuple[str, dict[str, object]]] = []

    async def progress(event: str, payload: dict[str, object]) -> None:
        events.append((event, dict(payload)))

    async def scenario() -> None:
        loop = AutonomousForgeAgentLoop(
            workspace_root=workspace,
            planner=BrokenPlanner(),
            platformio=FakePlatformIO(),
            progress=progress,
            policy=product_agent_policy(),
        )
        result = await loop.run(task="Create a complete ESP32 temperature detection PlatformIO project.")
        assert result.success is False
        assert result.classification == "ValueError"

    asyncio.run(scenario())

    assert list(workspace.iterdir()) == []
    assert not any(event == "PROVIDER_FALLBACK" for event, _ in events)


def test_autonomous_loop_creates_and_reads_multiple_staged_files(tmp_path: Path) -> None:
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
    from backend.agent_runtime.tool_policy import product_agent_policy

    class Planner:
        turns = 0

        def request_turn(self, **kwargs):
            self.turns += 1
            if self.turns == 1:
                files = {
                    "platformio.ini": "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n",
                    "include/SystemManager.h": "#pragma once\nvoid startSystem();\n",
                    "src/SystemManager.cpp": '#include "SystemManager.h"\nvoid startSystem() {}\n',
                    "src/main.cpp": '#include "SystemManager.h"\nvoid setup() { startSystem(); }\nvoid loop() {}\n',
                }
                return ProductToolPlan.parse({
                    "version": PRODUCT_TOOLPLAN_VERSION, "summary": "Creating the project modules",
                    "tool_calls": [
                        {"id": f"write_{index}", "type": "write_file", "arguments": {"path": path, "content": content}}
                        for index, (path, content) in enumerate(files.items())
                    ], "final": False,
                })
            if self.turns == 2:
                return ProductToolPlan.parse({
                    "version": PRODUCT_TOOLPLAN_VERSION, "summary": "Checking the staged header",
                    "tool_calls": [{"id": "read_header", "type": "read_file", "arguments": {"path": "include/SystemManager.h"}}],
                    "final": False,
                })
            assert any(
                result.get("tool") == "read_file" and "startSystem" in str(result.get("output"))
                for result in kwargs["tool_results"]
            )
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION, "summary": "Project files are complete",
                "tool_calls": [], "final": True,
            })

    async def progress(event: str, payload: dict[str, object]) -> None:
        del event, payload

    planner = Planner()
    result = asyncio.run(AutonomousForgeAgentLoop(
        workspace_root=tmp_path, planner=planner, platformio=object(),
        progress=progress, policy=product_agent_policy(),
    ).run(task="Create a complete ESP32 multi-file PlatformIO project.", require_build=False))
    assert result.success is True
    assert planner.turns == 3
    assert (tmp_path / "include/SystemManager.h").is_file()
    assert (tmp_path / "src/SystemManager.cpp").is_file()
    assert (tmp_path / "src/main.cpp").is_file()


def test_autonomous_loop_allows_large_project_written_one_file_per_turn(tmp_path: Path) -> None:
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
    from backend.agent_runtime.tool_policy import product_agent_policy

    class Planner:
        turns = 0

        def request_turn(self, **kwargs):
            del kwargs
            self.turns += 1
            if self.turns <= 20:
                return ProductToolPlan.parse({
                    "version": PRODUCT_TOOLPLAN_VERSION,
                    "summary": f"Creating module {self.turns}",
                    "tool_calls": [{
                        "id": f"write_{self.turns}",
                        "type": "write_file",
                        "arguments": {
                            "path": f"include/Module{self.turns}.h",
                            "content": f"#pragma once\n#define MODULE_{self.turns} {self.turns}\n",
                        },
                    }],
                    "final": False,
                })
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION,
                "summary": "All 20 modules are complete",
                "tool_calls": [],
                "final": True,
            })

    async def progress(event: str, payload: dict[str, object]) -> None:
        del event, payload

    planner = Planner()
    result = asyncio.run(AutonomousForgeAgentLoop(
        workspace_root=tmp_path, planner=planner, platformio=object(),
        progress=progress, policy=product_agent_policy(),
    ).run(task="Create a 20-file project.", require_build=False))

    assert result.success is True
    assert planner.turns == 21
    assert len(list((tmp_path / "include").glob("Module*.h"))) == 20


def test_autonomous_loop_stops_repeated_searches_with_no_new_results(tmp_path: Path) -> None:
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
    from backend.agent_runtime.tool_policy import product_agent_policy

    class Planner:
        turns = 0

        def request_turn(self, **kwargs):
            self.turns += 1
            pattern = "**/*" if self.turns % 2 else "*.cpp"
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION, "summary": "Searching project files",
                "tool_calls": [{"id": f"search_{self.turns}", "type": "glob_files", "arguments": {"path": ".", "pattern": pattern}}],
                "final": False,
            })

    events: list[str] = []

    async def progress(event: str, payload: dict[str, object]) -> None:
        del payload
        events.append(event)

    planner = Planner()
    result = asyncio.run(AutonomousForgeAgentLoop(
        workspace_root=tmp_path, planner=planner, platformio=object(),
        progress=progress, policy=product_agent_policy(), max_turns=10,
    ).run(task="Create an ESP32 project.", require_build=False))
    assert result.success is False
    assert result.classification == "AGENT_RUNTIME_NO_PROGRESS"
    assert planner.turns < 10
    assert "AGENT_NO_PROGRESS_WARNING" in events
    assert "AGENT_REORIENTATION_STARTED" in events
    assert 5 <= planner.turns <= 7


def test_safe_missing_project_file_is_distinct_from_unsafe_path(tmp_path: Path) -> None:
    from backend.agent_runtime.tool_contracts import ProductToolCall, RuntimeClassification
    from backend.agent_runtime.tool_executor import ForgeXToolExecutor
    from backend.agent_runtime.tool_policy import ToolPolicyError, product_agent_policy

    executor = ForgeXToolExecutor(write_root=tmp_path, policy=product_agent_policy())
    with pytest.raises(ToolPolicyError) as missing:
        executor.execute(ProductToolCall(
            call_id="missing", tool_type="read_file", arguments={"path": "include/Pins.h"}
        ).as_runtime_call())
    assert missing.value.classification is RuntimeClassification.FILE_NOT_FOUND
    with pytest.raises(ToolPolicyError) as unsafe:
        executor.execute(ProductToolCall(
            call_id="unsafe", tool_type="read_file", arguments={"path": "../Pins.h"}
        ).as_runtime_call())
    assert unsafe.value.classification is RuntimeClassification.PATH_UNSAFE


def test_common_text_project_files_can_be_staged(tmp_path: Path) -> None:
    from backend.agent_runtime.tool_contracts import ProductToolCall
    from backend.agent_runtime.tool_executor import ForgeXToolExecutor
    from backend.agent_runtime.tool_policy import product_agent_policy

    executor = ForgeXToolExecutor(write_root=tmp_path, policy=product_agent_policy())
    for index, (path, content) in enumerate((
        ("Makefile", "build:\n\t@echo ready\n"),
        ("Dockerfile", "FROM scratch\n"),
        ("src/service.go", "package main\n"),
        ("docs/design.md", "# Design\n"),
    )):
        result = executor.execute(ProductToolCall(
            call_id=f"write_{index}", tool_type="write_file",
            arguments={"path": path, "content": content},
        ).as_runtime_call())
        assert result["created"] is True
        assert (tmp_path / path).read_text(encoding="utf-8") == content


def test_runtime_failures_are_not_reported_as_provider_outages() -> None:
    from backend.agent_runtime.run_models import ProductAgentRun

    stalled = ProductAgentRun(
        run_id="stalled", project_id="project", provider_id="nvidia_nim",
        status="failed", classification="AGENT_RUNTIME_NO_PROGRESS",
    ).to_safe_dict()["summary"]
    assert stalled["generation_status"] == "runtime_failed"
    assert stalled["errors"][0]["code"] == "AGENT_RUNTIME_NO_PROGRESS"
    assert "no new information" in stalled["errors"][0]["message"]

    outage = ProductAgentRun(
        run_id="outage", project_id="project", provider_id="nvidia_nim",
        status="failed", classification="PROVIDER_UNAVAILABLE",
    ).to_safe_dict()["summary"]
    assert outage["generation_status"] == "provider_failed"
    assert outage["errors"][0]["code"] == "PROVIDER_UNAVAILABLE"


def test_redundant_build_after_verified_firmware_completes_run(tmp_path: Path) -> None:
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
    from backend.agent_runtime.tool_policy import product_agent_policy

    class Planner:
        turns = 0

        def request_turn(self, **kwargs):
            del kwargs
            self.turns += 1
            if self.turns == 1:
                calls = [{
                    "id": "write_source", "type": "write_file",
                    "arguments": {"path": "src/main.cpp", "content": "void setup() {}\nvoid loop() {}\n"},
                }]
            else:
                calls = [{"id": f"build_{self.turns}", "type": "build_firmware", "arguments": {}}]
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION,
                "summary": "Building firmware",
                "tool_calls": calls,
                "final": False,
            })

    class BuildResult:
        success = True
        status = "success"
        message = "Build succeeded"
        build_size_bytes = 8
        board = "esp32dev"
        platform = "platformio"
        warnings_count = 0
        process_result = None

        def __init__(self, firmware_path: str) -> None:
            self.firmware_path = firmware_path

        def api_dict(self):
            return {"success": True, "status": self.status, "message": self.message,
                    "firmware_path": self.firmware_path}

    class PlatformIO:
        calls = 0

        async def build(self, root: Path, *, environment=None):
            del environment
            self.calls += 1
            artifact = root / ".pio" / "build" / "esp32dev" / "firmware.bin"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"firmware")
            return BuildResult(str(artifact))

    async def progress(event: str, payload: dict[str, object]) -> None:
        del event, payload

    planner = Planner()
    platformio = PlatformIO()
    result = asyncio.run(AutonomousForgeAgentLoop(
        workspace_root=tmp_path, planner=planner, platformio=platformio,
        progress=progress, policy=product_agent_policy(),
    ).run(task="Create and build ESP32 firmware.", require_build=True))

    assert result.success is True
    assert result.build_verified is True
    assert planner.turns == 3
    assert platformio.calls == 1


def test_failed_write_is_reported_without_substituting_a_template(tmp_path: Path) -> None:
    from backend.agent_runtime.api_planner_provider import (
        ApiPlannerClassification,
        ApiPlannerError,
    )
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
    from backend.agent_runtime.tool_policy import product_agent_policy

    workspace = tmp_path / "empty-project"
    workspace.mkdir()

    class Planner:
        provider_id = "openrouter"
        model_id = "reasoning-model"
        calls = 0

        def request_turn(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ProductToolPlan.parse(
                    {
                        "version": PRODUCT_TOOLPLAN_VERSION,
                        "summary": "Write source",
                        "tool_calls": [
                            {
                                "id": "bad_write",
                                "type": "write_file",
                                "arguments": {
                                    "path": "../outside.cpp",
                                    "content": "void setup(){}",
                                },
                            }
                        ],
                        "final": False,
                    }
                )
            raise ApiPlannerError(
                ApiPlannerClassification.OUTPUT_TRUNCATED,
                "MODEL_OUTPUT_TRUNCATED",
            )

    class BuildResult:
        success = True
        status = "success"
        message = "Build succeeded"
        build_size_bytes = 1234
        board = "esp32dev"
        platform = "platformio"
        warnings_count = 0
        process_result = None

        def __init__(self, firmware_path: str) -> None:
            self.firmware_path = firmware_path

        def api_dict(self):
            return {
                "success": True,
                "status": "success",
                "message": self.message,
                "firmware_path": self.firmware_path,
            }

    class PlatformIO:
        async def build(self, root: Path, *, environment=None):
            del environment
            artifact = root / ".pio" / "build" / "esp32dev" / "firmware.bin"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b"firmware")
            return BuildResult(str(artifact))

    events: list[tuple[str, dict[str, object]]] = []

    async def progress(event: str, payload: dict[str, object]) -> None:
        events.append((event, dict(payload)))

    async def scenario() -> None:
        loop = AutonomousForgeAgentLoop(
            workspace_root=workspace,
            planner=Planner(),
            platformio=PlatformIO(),
            progress=progress,
            policy=product_agent_policy(),
        )
        result = await loop.run(task="Create an ESP32 LED project controlled from the Serial Monitor.")
        assert result.success is False
        assert result.classification == "MODEL_OUTPUT_TRUNCATED"

    asyncio.run(scenario())

    failed = next(payload for event, payload in events if event == "TOOL_FAILED")
    assert failed["message"] == "Writing staged source failed"
    assert failed["result"]["success"] is False
    assert "path" in failed["result"]["error"]
    assert not any(event == "PROVIDER_FALLBACK" for event, _ in events)
    assert not (workspace / "platformio.ini").exists()


def test_autonomous_loop_stops_when_platformio_executable_is_missing(tmp_path: Path) -> None:
    from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop
    from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION, ProductToolPlan
    from backend.agent_runtime.tool_policy import product_agent_policy
    from backend.runtime.result import BuildResult, FailureResult, ResultStatus

    workspace = tmp_path / "missing-pio-project"
    workspace.mkdir()

    class Planner:
        provider_id = "openrouter"
        model_id = "minimax/minimax-m3:free"
        calls = 0

        def request_turn(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ProductToolPlan.parse({
                    "version": PRODUCT_TOOLPLAN_VERSION,
                    "summary": "Create source files",
                    "tool_calls": [
                        {"id": "write_ini", "type": "write_file", "arguments": {"path": "platformio.ini", "content": "[env:esp32dev]\nplatform = espressif32\nboard = esp32dev\nframework = arduino\n"}},
                        {"id": "write_cpp", "type": "write_file", "arguments": {"path": "src/main.cpp", "content": "#include <Arduino.h>\nvoid setup(){}\nvoid loop(){}\n"}},
                    ],
                    "final": False,
                })
            return ProductToolPlan.parse({
                "version": PRODUCT_TOOLPLAN_VERSION,
                "summary": "Build the project",
                "tool_calls": [{"id": "build", "type": "build_firmware", "arguments": {}}],
                "final": False,
            })

    class MissingPlatformIO:
        async def build(self, root: Path, *, environment: str | None = None):
            del root, environment
            return BuildResult(
                success=False,
                status=ResultStatus.FAILED,
                message="PlatformIO is not installed or not on PATH. Run `npm run setup:platformio`, then restart ForgeX.",
                platform="platformio",
                failure=FailureResult(
                    category="TOOLCHAIN_MISSING",
                    message="pio was not found",
                    retryable=False,
                    stage="build",
                    exception_type="FileNotFoundError",
                ),
            )

    events: list[str] = []

    async def progress(event: str, payload: dict[str, object]) -> None:
        del payload
        events.append(event)

    planner = Planner()

    async def scenario() -> None:
        loop = AutonomousForgeAgentLoop(
            workspace_root=workspace,
            planner=planner,
            platformio=MissingPlatformIO(),
            progress=progress,
            policy=product_agent_policy(),
        )
        result = await loop.run(task="Create a complete ESP32 PlatformIO project.")
        assert result.success is False
        assert result.classification == "BUILD_TOOL_UNAVAILABLE"
        assert result.build_result["error_code"] == "platformio_executable_missing"

    asyncio.run(scenario())

    assert planner.calls == 2
    assert events.count("BUILD_FAILED") == 1


def test_apply_patch_is_precise_and_rejects_stale_context() -> None:
    from backend.agent_runtime.tool_executor import _apply_unified_patch

    current = "int ledPin = 4;\nvoid setup() {\n  pinMode(ledPin, OUTPUT);\n}\n"
    patch = """--- a/src/main.cpp
+++ b/src/main.cpp
@@ -1,4 +1,4 @@
-int ledPin = 4;
+int ledPin = 5;
 void setup() {
   pinMode(ledPin, OUTPUT);
 }
"""
    updated = _apply_unified_patch(current, patch, expected_path="src/main.cpp")
    assert "int ledPin = 5;" in updated
    assert "int ledPin = 4;" not in updated

    stale = patch.replace("pinMode(ledPin, OUTPUT);", "pinMode(ledPin, INPUT);")
    with pytest.raises(ValueError, match="patch_context_mismatch"):
        _apply_unified_patch(current, stale, expected_path="src/main.cpp")


def test_edit_profile_exposes_apply_patch_but_build_only_does_not() -> None:
    from backend.agent_runtime.capability_profiles import EDIT_PROFILE

    assert ToolName.APPLY_PATCH in EDIT_PROFILE.allowed_tools
    assert ToolName.APPLY_PATCH not in BUILD_ONLY_PROFILE.allowed_tools


def test_embedded_context_warns_about_classic_esp32_pin_rules(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "platformio.ini").write_text(
        "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "main.cpp").write_text(
        """#include <Arduino.h>
#include <WiFi.h>
#define BAD_OUTPUT_PIN 34
#define ADC2_SENSOR_PIN 27
#define STRAP_LED_PIN 12
void setup(){
  Serial.begin(115200);
  pinMode(BAD_OUTPUT_PIN, OUTPUT);
  pinMode(STRAP_LED_PIN, OUTPUT);
  WiFi.begin("ssid", "pw");
}
void loop(){ analogRead(ADC2_SENSOR_PIN); }
""",
        encoding="utf-8",
    )
    graph = inspect_embedded_project(tmp_path)
    rendered = graph.render()
    assert "GPIO34 is input-only" in rendered
    assert "GPIO12 is a classic ESP32 strapping pin" in rendered
    assert "GPIO27 uses ADC2" in rendered
    assert "Wi-Fi" in graph.features


def test_embedded_context_extracts_i2c_addresses(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "platformio.ini").write_text(
        "[env:esp32dev]\nplatform=espressif32\nboard=esp32dev\nframework=arduino\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "main.cpp").write_text(
        "void scan(){ Wire.beginTransmission(0x3C); }\n",
        encoding="utf-8",
    )
    graph = inspect_embedded_project(tmp_path)
    assert [item.hex for item in graph.i2c_addresses] == ["0x3C"]
