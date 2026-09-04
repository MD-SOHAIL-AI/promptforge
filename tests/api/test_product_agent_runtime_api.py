from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from tests.api.test_api_routes import completed_outcome, make_client
from backend.agent_runtime.product_agent_service import ProductAgentRun
from backend.api.routes.agent_runtime import _inspect_response
from backend.runtime.subprocess_mgr import SubprocessManager
from backend.tools.board_detector import BoardInfo, BoardType


def setup_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, enabled: bool) -> None:
    monkeypatch.setenv("FORGEX_ROOT", str(tmp_path / "app-root"))
    monkeypatch.setenv("FORGEX_MODEL_ROUTER_SETTINGS_PATH", str(tmp_path / "model-settings.json"))
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_RUNTIME", "1" if enabled else "0")
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_RUNTIME_FAKE_PROVIDER", "1" if enabled else "0")
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2", "0")
    monkeypatch.delenv("PROMPTFORGE_ROOT", raising=False)


def wait_run(api, run_id: str) -> dict[str, object]:
    for _ in range(300):
        response = api.get(f"/agent-runtime/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        if run["status"] in {"completed", "failed", "cancelled", "blocked", "timed_out"}:
            return run
        time.sleep(0.01)
    raise AssertionError("product agent run did not finish")


def wait_run_status(api, run_id: str, statuses: set[str]) -> dict[str, object]:
    for _ in range(300):
        response = api.get(f"/agent-runtime/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()["run"]
        if run["status"] in statuses:
            return run
        time.sleep(0.01)
    raise AssertionError(f"product agent run did not reach {sorted(statuses)}")


def wait_session_message_count(api, session_id: str, count: int) -> dict[str, object]:
    for _ in range(300):
        response = api.get(f"/agent-runtime/sessions/{session_id}")
        assert response.status_code == 200, response.text
        session = response.json()["session"]
        if len(session["messages"]) >= count:
            return session
        time.sleep(0.01)
    raise AssertionError(f"agent session did not reach {count} messages")


def run_events(api, run_id: str) -> list[dict[str, object]]:
    response = api.get(f"/agent-runtime/runs/{run_id}/events")
    assert response.status_code == 200, response.text
    events: list[dict[str, object]] = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def activity_names(payload: dict[str, object]) -> list[str]:
    events = payload.get("activity_events")
    assert isinstance(events, list)
    return [str(event.get("activity")) for event in events if isinstance(event, dict) and event.get("event_type") != "activity.completed"]


def test_inspect_latest_forge_failure_reports_saved_provider_diagnostics(tmp_path: Path) -> None:
    run = ProductAgentRun(
        run_id="run-failed",
        project_id="project-test",
        provider_id="openrouter",
        status="failed",
        classification="API_TOOLPLAN_INVALID",
        model_id="nvidia/incompatible:free",
        outbound_request_count=2,
        request_reached_provider=True,
        fallback_reason="API_RESPONSE_INVALID",
    )
    metadata = type("Metadata", (), {"project_path": str(tmp_path)})()

    response = _inspect_response("Diagnose the latest Forge run failure", metadata, run)

    assert "API_TOOLPLAN_INVALID" in response
    assert "nvidia/incompatible:free" in response
    assert "Provider requests: 2; reached provider: yes." in response
    assert "Workspace files:" not in response


def test_product_agent_route_disabled_without_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=False)
    api, project_id, _ = make_client(tmp_path)
    with api:
        response = api.post(
            "/agent-runtime/runs",
            json={"project_id": project_id, "instruction": "Create a test file", "provider_id": "verified_template"},
        )
        providers = api.get("/agent-runtime/providers")
    assert response.status_code == 403
    assert response.json()["code"] == "AGENT_RUNTIME_DISABLED"
    assert providers.status_code == 200
    assert providers.json()["enabled"] is False


def test_agent_session_create_list_and_local_reply(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    api, project_id, _ = make_client(tmp_path)
    with api:
        created = api.post("/agent-runtime/sessions", json={"project_id": project_id, "title": "Bringup"})
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]

        listed = api.get("/agent-runtime/sessions", params={"project_id": project_id})
        assert listed.status_code == 200, listed.text
        assert listed.json()["sessions"][0]["session_id"] == session_id

        replied = api.post(f"/agent-runtime/sessions/{session_id}/messages", json={"content": "hi"})
        assert replied.status_code == 202, replied.text
        payload = replied.json()

    assert payload["run"] is None
    assert payload["intent"] == "chat"
    assert [message["role"] for message in payload["session"]["messages"]] == ["user", "assistant"]
    assert "build, change, fix" in payload["session"]["messages"][-1]["content"]
    assert payload["session"]["messages"][-1]["metadata"]["intent"] == "chat"
    assert payload["activity_events"] == []


def test_v2_orchestrator_keeps_chat_run_free_and_exposes_action_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2", "1")
    api, project_id, _ = make_client(tmp_path)
    with api:
        created = api.post("/api/v2/agents/sessions", json={"project_id": project_id})
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]

        chat = api.post(
            f"/api/v2/agents/sessions/{session_id}/messages",
            json={"content": "hello", "provider_id": "fake_planner"},
        )
        assert chat.status_code == 202, chat.text
        assert chat.json()["run"] is None

        action = api.post(
            f"/api/v2/agents/sessions/{session_id}/messages",
            json={
                "content": "Edit the project and create the safe test file",
                "provider_id": "fake_planner",
                "autonomy": "staged_changes",
            },
        )
        assert action.status_code == 202, action.text
        run_id = action.json()["run"]["run_id"]
        completed = wait_run(api, run_id)
        assert completed["status"] == "completed"

        graph = api.get(f"/api/v2/agents/runs/{run_id}/graph")
        assert graph.status_code == 200, graph.text
        nodes = graph.json()["graph"]["nodes"]
        assert [node["role"] for node in nodes[:3]] == [
            "supervisor_planner",
            "firmware_implementer",
            "validation_services",
        ]
        change_set = api.get(f"/agent-runtime/runs/{run_id}/changes")
        assert change_set.status_code == 200, change_set.text
        assert change_set.json()["change_set"]["status"] == "applied"

        remembered = api.get(f"/api/v2/agents/projects/{project_id}/memory")
        assert remembered.status_code == 200, remembered.text
        assert remembered.json()["memories"][0]["source_type"] == "changeset"
        assert remembered.json()["memories"][0]["source_hash"]

        project = api.get(f"/projects/{project_id}").json()
        workspace = Path(project["project_path"])
        (workspace / "FORGEX_AGENT_RUNTIME_SMOKE.txt").write_text("externally changed\n", encoding="utf-8")
        stale = api.get(f"/api/v2/agents/projects/{project_id}/memory")
        assert stale.status_code == 200, stale.text
        assert stale.json()["memories"] == []


def test_v2_compound_project_request_uses_generate_and_build_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("FORGEX_ENABLE_AGENT_ORCHESTRATOR_V2", "1")
    prompts: list[str] = []
    workspaces: list[dict[str, object]] = []

    async def observing_execute(prompt: str, **kwargs: object):
        prompts.append(prompt)
        workspaces.append(dict(kwargs["active_workspace"]))  # type: ignore[arg-type]
        callback = kwargs["progress_callback"]
        task_id = str(kwargs["task_id"])
        await callback("TASK_CREATED", {"task_id": task_id})  # type: ignore[operator]
        await callback("PLAN_GENERATED", {"task_id": task_id})  # type: ignore[operator]
        await callback("GENERATION_STARTED", {"task_id": task_id})  # type: ignore[operator]
        await callback("GENERATION_COMPLETED", {"task_id": task_id})  # type: ignore[operator]
        await callback("CODE_GENERATION_COMPLETED", {"task_id": task_id})  # type: ignore[operator]
        await callback("BUILD_STARTED", {"task_id": task_id})  # type: ignore[operator]
        await callback("BUILD_COMPLETED", {"task_id": task_id})  # type: ignore[operator]
        await callback("WORKFLOW_COMPLETED", {"task_id": task_id, "status": "COMPLETED"})  # type: ignore[operator]
        return completed_outcome(task_id)

    api, project_id, _ = make_client(tmp_path, execute_prompt_fn=observing_execute)
    with api:
        created = api.post("/api/v2/agents/sessions", json={"project_id": project_id})
        session_id = created.json()["session"]["session_id"]
        started = api.post(
            f"/api/v2/agents/sessions/{session_id}/messages",
            json={
                "content": (
                    "Build a complete ESP32 smart room monitor project. When the ESP32 starts, initialize sensors. "
                    "Set every GPIO clearly and validate that the project compiles."
                ),
                "provider_id": "verified_template",
                "autonomy": "auto",
            },
        )
        assert started.status_code == 202, started.text
        payload = started.json()
        assert payload["intent"] == "generate"
        assert payload["decision"]["requires_write"] is True
        assert payload["decision"]["requires_build"] is True
        assert payload["run"]["autonomy"] == "build_only"
        completed = wait_run(api, payload["run"]["run_id"])

    assert completed["status"] == "completed"
    assert completed["stage_statuses"]["planning"] == "completed"
    assert completed["stage_statuses"]["generation"] == "completed"
    assert completed["stage_statuses"]["build"] == "completed"
    assert prompts
    assert workspaces[0]["agent_turn_intent"] == "generate"
    assert workspaces[0]["agent_requested_actions"] == ["generate", "build"]
    assert workspaces[0]["agent_requires_build"] is True


def test_agent_session_model_question_does_not_start_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    prompts: list[str] = []

    async def observing_execute(prompt: str, **kwargs: object):
        prompts.append(prompt)
        callback = kwargs["progress_callback"]
        task_id = str(kwargs["task_id"])
        await callback("PLAN_GENERATED", {"task_id": task_id})
        return completed_outcome(task_id)

    api, project_id, _ = make_client(tmp_path, execute_prompt_fn=observing_execute)
    with api:
        created = api.post("/agent-runtime/sessions", json={"project_id": project_id})
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]

        replied = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={"content": "what model are you", "provider_id": "verified_template"},
        )
        assert replied.status_code == 202, replied.text
        payload = replied.json()

    assert payload["intent"] == "chat"
    assert payload["run"] is None
    assert prompts == []
    assert "checking" in activity_names(payload)
    assert "Selected provider:" in payload["session"]["messages"][-1]["content"]
    assert "forgex-verified-templates-v1" in payload["session"]["messages"][-1]["content"]


def test_agent_session_inspect_reads_workspace_without_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    api, project_id, _ = make_client(tmp_path)
    with api:
        created = api.post("/agent-runtime/sessions", json={"project_id": project_id})
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]

        replied = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={"content": "explain src/main.cpp", "provider_id": "verified_template"},
        )
        assert replied.status_code == 202, replied.text
        payload = replied.json()

    assert payload["intent"] == "inspect"
    assert payload["run"] is None
    names = activity_names(payload)
    assert names[:3] == ["inspecting", "reading", "analyzing"]
    content = payload["session"]["messages"][-1]["content"]
    assert "No files were modified, built, or flashed." in content
    assert "src/main.cpp" in content
    assert "void setup()" in content


def test_agent_session_auto_generate_builds_without_flash_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    prompts: list[str] = []

    async def observing_execute(prompt: str, **kwargs: object):
        prompts.append(prompt)
        callback = kwargs["progress_callback"]
        task_id = str(kwargs["task_id"])
        await callback("PLAN_GENERATED", {"task_id": task_id})
        await callback("BUILD_STARTED", {"task_id": task_id})
        await callback("BUILD_COMPLETED", {"task_id": task_id})
        await callback("WORKFLOW_COMPLETED", {"task_id": task_id, "status": "COMPLETED"})
        return completed_outcome(task_id)

    api, project_id, _ = make_client(tmp_path, execute_prompt_fn=observing_execute)
    with api:
        created = api.post("/agent-runtime/sessions", json={"project_id": project_id})
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]

        started = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={
                "content": "Create an ESP32 blink LED project using PlatformIO.",
                "provider_id": "verified_template",
                "board_type": "ESP32",
            },
        )
        assert started.status_code == 202, started.text
        run = wait_run(api, started.json()["run"]["run_id"])
        session = wait_session_message_count(api, session_id, 2)
        events = run_events(api, run["run_id"])

    assert started.json()["intent"] == "generate"
    assert run["status"] == "completed"
    assert run["autonomy"] == "build_only"
    assert run["flash_confirmation_required"] is False
    assert run["stage_statuses"]["build"] == "completed"
    assert "building" in {str(event.get("activity")) for event in events}
    assert "Confirm flash" not in session["messages"][-1]["content"]
    assert len(prompts) == 1


def test_agent_session_edit_with_build_prohibition_uses_staged_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    api, project_id, _ = make_client(tmp_path)
    with api:
        created = api.post("/agent-runtime/sessions", json={"project_id": project_id})
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]

        started = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={
                "content": "Change delay to 250ms but don't build it.",
                "provider_id": "fake_planner",
            },
        )
        assert started.status_code == 202, started.text
        run = wait_run(api, started.json()["run"]["run_id"])
        events = run_events(api, run["run_id"])

    assert started.json()["intent"] == "edit"
    assert "planning" in activity_names(started.json())
    assert run["autonomy"] == "staged_changes"
    assert run["stage_statuses"]["build"] == "pending"
    assert "editing" in {str(event.get("activity")) for event in events}


def test_agent_session_repair_activity_follows_repair_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)

    async def repairing_execute(prompt: str, **kwargs: object):
        del prompt
        callback = kwargs["progress_callback"]
        task_id = str(kwargs["task_id"])
        await callback("PLAN_GENERATED", {"task_id": task_id})
        await callback("BUILD_STARTED", {"task_id": task_id})
        await callback("BUILD_FAILED", {"task_id": task_id, "message": "Compile failed"})
        await callback("BUILD_REPAIR_STARTED", {"task_id": task_id, "attempt_number": 1})
        await callback("BUILD_REPAIR_COMPLETED", {"task_id": task_id, "attempt_number": 1})
        return completed_outcome(task_id)

    api, project_id, _ = make_client(tmp_path, execute_prompt_fn=repairing_execute)
    with api:
        created = api.post("/agent-runtime/sessions", json={"project_id": project_id})
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]

        started = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={"content": "fix the build error", "provider_id": "verified_template"},
        )
        assert started.status_code == 202, started.text
        run = wait_run(api, started.json()["run"]["run_id"])
        events = run_events(api, run["run_id"])

    activities = [str(event.get("activity")) for event in events]
    assert "diagnosing" in activities
    assert "repairing" in activities
    assert "building" in activities


def test_agent_session_flash_requires_existing_confirmation_then_yes_confirms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    board = BoardInfo(
        board_type=BoardType.ESP32,
        port="COM7",
        vid=1,
        pid=2,
        manufacturer="test",
        description="test",
        serial_number="test",
    )
    api, project_id, _ = make_client(tmp_path, boards=[board])
    with api:
        created = api.post("/agent-runtime/sessions", json={"project_id": project_id})
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]

        started = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={
                "content": "Create an ESP32 blink LED project using PlatformIO.",
                "provider_id": "verified_template",
                "autonomy": "build_then_confirm_flash",
                "board_port": "COM7",
                "board_type": "ESP32",
            },
        )
        assert started.status_code == 202, started.text
        pending = wait_run_status(api, started.json()["run"]["run_id"], {"awaiting_flash_confirmation"})

        gated = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={"content": "flash it", "provider_id": "verified_template", "board_port": "COM7", "board_type": "ESP32"},
        )
        assert gated.status_code == 202, gated.text
        assert gated.json()["intent"] == "flash"
        assert "waiting" in activity_names(gated.json())
        assert "flashing" not in activity_names(gated.json())
        assert gated.json()["run"]["run_id"] == pending["run_id"]
        assert gated.json()["run"]["status"] == "awaiting_flash_confirmation"

        confirmed = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={"content": "yes", "provider_id": "verified_template", "board_port": "COM7", "board_type": "ESP32"},
        )
        assert confirmed.status_code == 202, confirmed.text
        completed = wait_run(api, pending["run_id"])
        events = run_events(api, pending["run_id"])

    assert confirmed.json()["intent"] == "confirm"
    assert completed["status"] == "completed"
    assert completed["stage_statuses"]["flash"] == "completed"
    assert "connecting" in {str(event.get("activity")) for event in events}
    assert "flashing" in {str(event.get("activity")) for event in events}


def test_agent_session_second_message_includes_conversation_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    prompts: list[str] = []

    async def observing_execute(prompt: str, **kwargs: object):
        prompts.append(prompt)
        callback = kwargs["progress_callback"]
        task_id = str(kwargs["task_id"])
        await callback("PLAN_GENERATED", {"task_id": task_id})
        await callback("WORKFLOW_COMPLETED", {"task_id": task_id, "status": "COMPLETED"})
        return completed_outcome(task_id)

    api, project_id, _ = make_client(tmp_path, execute_prompt_fn=observing_execute)
    with api:
        created = api.post("/agent-runtime/sessions", json={"project_id": project_id})
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]

        first = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={
                "content": "Create an ESP32 blink LED project using PlatformIO.",
                "provider_id": "verified_template",
                "autonomy": "build_then_confirm_flash",
                "board_type": "ESP32",
            },
        )
        assert first.status_code == 202, first.text
        first_run = wait_run_status(api, first.json()["run"]["run_id"], {"awaiting_flash_confirmation"})
        assert first_run["status"] == "awaiting_flash_confirmation"
        wait_session_message_count(api, session_id, 2)

        second = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={
                "content": "Change the blink delay to 250ms.",
                "provider_id": "verified_template",
                "autonomy": "build_then_confirm_flash",
                "board_type": "ESP32",
            },
        )
        assert second.status_code == 202, second.text
        second_run = wait_run_status(api, second.json()["run"]["run_id"], {"awaiting_flash_confirmation"})

    assert second_run["status"] == "awaiting_flash_confirmation"
    assert len(prompts) == 2
    assert "Continue the existing conversation" in prompts[-1]
    assert "Create an ESP32 blink LED project using PlatformIO." in prompts[-1]
    assert "Change the blink delay to 250ms." in prompts[-1]


def test_agent_session_structured_context_reaches_workflow_prompt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    prompts: list[str] = []

    async def observing_execute(prompt: str, **kwargs: object):
        prompts.append(prompt)
        callback = kwargs["progress_callback"]
        task_id = str(kwargs["task_id"])
        await callback("PLAN_GENERATED", {"task_id": task_id})
        await callback("WORKFLOW_COMPLETED", {"task_id": task_id, "status": "COMPLETED"})
        return completed_outcome(task_id)

    api, project_id, _ = make_client(tmp_path, execute_prompt_fn=observing_execute)
    with api:
        created = api.post("/agent-runtime/sessions", json={"project_id": project_id})
        assert created.status_code == 201, created.text
        session_id = created.json()["session"]["session_id"]

        started = api.post(
            f"/agent-runtime/sessions/{session_id}/messages",
            json={
                "content": "Create an ESP32 blink LED project using PlatformIO.",
                "provider_id": "verified_template",
                "board_port": "COM9",
                "board_type": "ESP32",
                "context": {
                    "references": ["board", "project", "latest-build"],
                    "attachments": [{
                        "name": "pin-notes.txt",
                        "size": 21,
                        "type": "text/plain",
                        "content": "Use GPIO2 for the LED.",
                    }],
                },
            },
        )
        assert started.status_code == 202, started.text
        wait_run(api, started.json()["run"]["run_id"])
        session = api.get(f"/agent-runtime/sessions/{session_id}").json()["session"]

    assert prompts
    assert "Explicit UI context:" in prompts[-1]
    assert "Selected board: ESP32" in prompts[-1]
    assert "Selected port: COM9" in prompts[-1]
    assert "[attachment:pin-notes.txt]" in prompts[-1]
    assert "Use GPIO2 for the LED." in prompts[-1]
    assert session["messages"][0]["metadata"]["context_references"] == "board,project,latest-build"
    assert session["messages"][0]["metadata"]["attachment_names"] == "pin-notes.txt"


def test_fake_agent_changeset_apply_and_undo_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    api, project_id, _ = make_client(tmp_path)
    with api:
        project = api.get(f"/projects/{project_id}")
        assert project.status_code == 200, project.text
        workspace = Path(project.json()["project_path"])
        target = workspace / "FORGEX_AGENT_RUNTIME_SMOKE.txt"
        assert not target.exists()

        started = api.post(
            "/agent-runtime/runs",
            json={"project_id": project_id, "instruction": "Create a safe staged test file.", "provider_id": "fake_planner"},
        )
        assert started.status_code == 202, started.text
        run = wait_run(api, started.json()["run"]["run_id"])
        assert run["status"] == "completed"
        assert run["classification"] == "TOOL_RUNTIME_PASS"
        assert run["change_set_id"]
        assert run["active_workspace_unchanged"] is True
        assert not target.exists()

        changes = api.get(f"/agent-runtime/runs/{run['run_id']}/changes")
        assert changes.status_code == 200, changes.text
        change_set = changes.json()["change_set"]
        assert change_set["status"] == "pending"
        assert change_set["change_set_id"] == run["change_set_id"]

        applied = api.post(f"/changes/{run['change_set_id']}/apply")
        assert applied.status_code == 200, applied.text
        assert applied.json()["change_set"]["status"] == "applied"
        assert target.read_text(encoding="utf-8") == "ForgeX product agent runtime completed.\n"

        undone = api.post(f"/changes/{run['change_set_id']}/undo")
        assert undone.status_code == 200, undone.text
        assert undone.json()["change_set"]["status"] == "undone"
        assert not target.exists()

        events = api.get(f"/agent-runtime/runs/{run['run_id']}/events")
        assert events.status_code == 200
        serialized = json.dumps(run).casefold() + events.text.casefold()
        for forbidden in ("api_key", "raw_prompt", "raw_response", "stdout", "stderr"):
            assert forbidden not in serialized


def test_autonomous_agent_builds_then_waits_for_flash_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    api, project_id, _ = make_client(tmp_path)
    with api:
        started = api.post(
            "/agent-runtime/runs",
            json={
                "project_id": project_id,
                "instruction": "Create an ESP32 blink LED project using PlatformIO.",
                "provider_id": "verified_template",
                "autonomy": "build_then_confirm_flash",
                "board_type": "ESP32",
            },
        )
        assert started.status_code == 202, started.text
        run = wait_run_status(api, started.json()["run"]["run_id"], {"awaiting_flash_confirmation"})

    assert run["status"] == "awaiting_flash_confirmation"
    assert run["flash_confirmation_required"] is True
    assert run["stage_statuses"]["build"] == "completed"
    assert run["stage_statuses"]["flash_confirmation"] == "waiting"
    assert run["stage_statuses"]["flash"] == "pending"


def test_autonomous_agent_passes_selected_provider_to_workflow_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-oss-20b:free")
    seen_workspace: dict[str, object] = {}

    async def observing_execute(prompt: str, **kwargs: object):
        del prompt
        seen_workspace.update(kwargs["active_workspace"])  # type: ignore[index]
        callback = kwargs["progress_callback"]
        task_id = str(kwargs["task_id"])
        await callback("PLAN_GENERATED", {"task_id": task_id})
        await callback("WORKFLOW_COMPLETED", {"task_id": task_id, "status": "COMPLETED"})
        return completed_outcome(task_id)

    api, project_id, _ = make_client(tmp_path, execute_prompt_fn=observing_execute)
    with api:
        started = api.post(
            "/agent-runtime/runs",
            json={
                "project_id": project_id,
                "instruction": "Create an ESP32 blink LED project using PlatformIO.",
                "provider_id": "openrouter",
                "autonomy": "build_then_confirm_flash",
                "board_type": "ESP32",
            },
        )
        assert started.status_code == 202, started.text
        run = wait_run_status(api, started.json()["run"]["run_id"], {"awaiting_flash_confirmation"})

    assert run["provider_id"] == "openrouter"
    assert seen_workspace["provider_id"] == "openrouter"
    assert isinstance(seen_workspace["model_id"], str)
    assert seen_workspace["model_id"]
    assert seen_workspace["local_only"] is False


def test_autonomous_agent_cancel_kills_owned_subprocesses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)

    class TrackingSubprocessManager(SubprocessManager):
        def __init__(self) -> None:
            super().__init__()
            self.killed = False

        async def kill_all(self) -> None:
            self.killed = True

    manager = TrackingSubprocessManager()

    async def blocking_execute(prompt: str, **kwargs: object):
        del prompt
        callback = kwargs["progress_callback"]
        task_id = str(kwargs["task_id"])
        await callback("PLAN_GENERATED", {"task_id": task_id})
        await asyncio.sleep(60)
        return completed_outcome(task_id)

    api, project_id, _ = make_client(tmp_path, execute_prompt_fn=blocking_execute, subprocess_manager=manager)
    with api:
        started = api.post(
            "/agent-runtime/runs",
            json={
                "project_id": project_id,
                "instruction": "Create an ESP32 blink LED project using PlatformIO.",
                "provider_id": "verified_template",
                "autonomy": "build_then_confirm_flash",
                "board_type": "ESP32",
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["run"]["run_id"]
        cancelled = api.post(f"/agent-runtime/runs/{run_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
        run = wait_run_status(api, run_id, {"cancelled"})

    assert run["status"] == "cancelled"
    assert manager.killed is True


def test_autonomous_agent_confirm_flash_uses_existing_flash_tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    board = BoardInfo(
        board_type=BoardType.ESP32,
        port="COM7",
        vid=1,
        pid=2,
        manufacturer="test",
        description="test",
        serial_number="test",
    )
    api, project_id, _ = make_client(tmp_path, boards=[board])
    with api:
        started = api.post(
            "/agent-runtime/runs",
            json={
                "project_id": project_id,
                "instruction": "Create an ESP32 blink LED project using PlatformIO.",
                "provider_id": "verified_template",
                "autonomy": "build_then_confirm_flash",
                "board_port": "COM7",
                "board_type": "ESP32",
            },
        )
        assert started.status_code == 202, started.text
        run = wait_run_status(api, started.json()["run"]["run_id"], {"awaiting_flash_confirmation"})

        confirmed = api.post(
            f"/agent-runtime/runs/{run['run_id']}/confirm-flash",
            json={"port": "COM7", "board_type": "ESP32"},
        )
        assert confirmed.status_code == 202, confirmed.text
        completed = wait_run(api, run["run_id"])

    assert completed["status"] == "completed"
    assert completed["stage_statuses"]["flash"] == "completed"
    assert completed["flash_result"]["success"] is True


def test_autonomous_agent_flash_rejects_board_type_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    board = BoardInfo(
        board_type=BoardType.ESP32_C3,
        port="COM7",
        vid=1,
        pid=2,
        manufacturer="test",
        description="test",
        serial_number="test",
    )
    api, project_id, _ = make_client(tmp_path, boards=[board])
    with api:
        started = api.post(
            "/agent-runtime/runs",
            json={
                "project_id": project_id,
                "instruction": "Create an ESP32 blink LED project using PlatformIO.",
                "provider_id": "verified_template",
                "autonomy": "build_then_confirm_flash",
                "board_port": "COM7",
                "board_type": "ESP32",
            },
        )
        assert started.status_code == 202, started.text
        run = wait_run_status(api, started.json()["run"]["run_id"], {"awaiting_flash_confirmation"})

        confirmed = api.post(
            f"/agent-runtime/runs/{run['run_id']}/confirm-flash",
            json={"port": "COM7", "board_type": "ESP32"},
        )
        assert confirmed.status_code == 202, confirmed.text
        failed = wait_run(api, run["run_id"])

    assert failed["status"] == "failed"
    assert failed["classification"] == "BOARD_TYPE_MISMATCH"
    assert failed["stage_statuses"]["flash"] == "failed"


def test_removed_provider_is_not_routeable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    api, project_id, _ = make_client(tmp_path)
    with api:
        response = api.post(
            "/agent-runtime/runs",
            json={"project_id": project_id, "instruction": "safe", "provider_id": "removed_provider"},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "PRODUCT_PROVIDER_NOT_ROUTEABLE"


def test_changeset_apply_rejects_remote_origin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_env(monkeypatch, tmp_path, enabled=True)
    api, project_id, _ = make_client(tmp_path)
    with api:
        started = api.post(
            "/agent-runtime/runs",
            json={"project_id": project_id, "instruction": "Create a test file.", "provider_id": "fake_planner"},
        )
        run = wait_run(api, started.json()["run"]["run_id"])
        response = api.post(
            f"/changes/{run['change_set_id']}/apply",
            headers={"Origin": "https://remote.example"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "REMOTE_ORIGIN_REJECTED"
