import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from backend.agent_runtime.chat_service import ChatService
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.public_progress import LivePlan, public_text
from backend.agent_runtime.session_store import AgentSessionStore


def test_existing_nonnullable_session_database_migrates_without_losing_history(
    tmp_path,
):
    original = AgentSessionStore(tmp_path / "source.db")
    session = original.create(project_id="project")
    session = original.append_message(
        session.session_id, role="user", content="Keep this history"
    )
    payload = session.to_dict(include_messages=True)
    payload.pop("kind")
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE agent_sessions(session_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL, schema_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO agent_sessions VALUES(?,?,?,?,?,?,?)",
            (
                session.session_id,
                "project",
                "idle",
                json.dumps(payload),
                1,
                session.created_at,
                session.updated_at,
            ),
        )
    restored = AgentSessionStore(path)
    assert restored.get(session.session_id).messages[0].content == "Keep this history"
    chat = restored.create(kind="chat")
    assert chat.project_id is None
    assert AgentSessionStore(path).get(chat.session_id).kind == "chat"


@pytest.mark.parametrize(
    "tag", ["think", "thinking", "analysis", "reasoning", "internal_analysis"]
)
def test_hidden_delimiters_never_leak_at_chunk_boundaries(tag):
    raw = f"Public <{tag}>PRIVATE</{tag}> answer"
    for end in range(1, len(raw) + 1):
        result = public_text(raw[:end])
        assert "PRIVATE" not in result
        assert "<" not in result
    assert public_text(raw) == "Public  answer"


def test_plan_requires_real_evidence_and_matching_verification():
    plan = LivePlan()
    steps = [
        {"text": "Build firmware", "status": "in_progress", "verification": "build"}
    ]
    first = plan.update(steps)
    identity = first["steps"][0]["id"]
    plan.observe("read", True, "read_file")
    steps[0]["status"] = "completed"
    assert plan.update(steps)["steps"][0]["status"] == "in_progress"
    plan.observe("failed-build", False, "build_firmware")
    assert plan.update(steps)["steps"][0]["status"] == "in_progress"
    plan.observe("successful-build", True, "build_firmware")
    done = plan.update(steps)
    assert done["steps"][0]["id"] == identity
    assert done["steps"][0]["status"] == "completed"
    assert plan.update([])["steps"][0]["status"] == "completed"


class Router:
    registry = SimpleNamespace(
        route_for_task=lambda task: SimpleNamespace(
            provider_id="local", model_id="test"
        )
    )

    def __init__(self, *, wait=False, fail=False):
        self.wait, self.fail, self.closed = wait, fail, False
        self.requests = []

    async def generate_stream(self, request):
        self.requests.append(request)
        try:
            for part in ["Hello ", "<thi", "nk>PRIVATE", "</think>", "**world**"]:
                yield part
                await asyncio.sleep(0)
            if self.fail:
                raise RuntimeError("private provider exception")
            if self.wait:
                await asyncio.Event().wait()
        finally:
            self.closed = True


def service(tmp_path, router=None):
    sessions = AgentSessionStore(tmp_path / "sessions.db")
    events = AgentOrchestrationStore(tmp_path / "events.db")
    return ChatService(sessions, events, router or Router())


def test_chat_title_replaces_a_greeting_with_the_first_meaningful_request(tmp_path):
    store = AgentSessionStore(tmp_path / "sessions.db")
    session = store.create(kind="chat")

    session = store.append_message(session.session_id, role="user", content="hi")
    assert session.title == "Hi"
    session = store.append_message(
        session.session_id,
        role="assistant",
        content="Hello! How can I help?",
    )
    session = store.append_message(
        session.session_id,
        role="user",
        content="what is an ESP32 and when should I use one?",
    )
    assert session.title == "What is an ESP32 and when should I use one"

    session = store.configure(session.session_id, title="Embedded notes")
    session = store.append_message(
        session.session_id,
        role="user",
        content="Explain the ESP32 boot process",
    )
    assert session.title == "Embedded notes"


def test_existing_greeting_title_is_repaired_from_chat_history(tmp_path):
    path = tmp_path / "sessions.db"
    store = AgentSessionStore(path)
    session = store.create(kind="chat")
    session = store.append_message(session.session_id, role="user", content="hi")
    session = store.append_message(
        session.session_id,
        role="user",
        content="Help me connect a temperature sensor to an ESP32",
    )
    with sqlite3.connect(path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM agent_sessions WHERE session_id=?",
                (session.session_id,),
            ).fetchone()[0]
        )
        payload["title"] = "hi"
        payload.pop("title_source", None)
        connection.execute(
            "UPDATE agent_sessions SET payload_json=? WHERE session_id=?",
            (json.dumps(payload), session.session_id),
        )

    restored = AgentSessionStore(path).get(session.session_id)
    assert restored.title == "Help me connect a temperature sensor to an ESP32"


def test_chat_stream_persistence_isolation_and_no_tools(tmp_path):
    async def exercise():
        chat = service(tmp_path)
        first = chat.sessions.create(kind="chat")
        second = chat.sessions.create(kind="chat")
        agent = chat.sessions.create(project_id="project")
        chat.start(first.session_id, "Hello")
        await chat.tasks[first.session_id]
        result = chat.sessions.get(first.session_id)
        assert result.messages[-1].content == "Hello **world**"
        assert result.messages[-1].metadata["status"] == "completed"
        assert chat.sessions.get(second.session_id).messages == ()
        assert chat.sessions.list() == (agent,)
        events = chat.events.events(first.session_id)
        assert len({event["message_id"] for event in events}) == 1
        assert events[0]["event_type"] == "message.started"
        assert events[-1]["event_type"] == "message.completed"
        assert all(event["event_type"].startswith("message.") for event in events)
        assert "PRIVATE" not in json.dumps(events)
        assert chat.router.closed and not chat.tasks
        restored = service(tmp_path)
        assert (
            restored.sessions.get(first.session_id).messages[-1].content
            == "Hello **world**"
        )

    asyncio.run(exercise())


@pytest.mark.parametrize("immediate", [True, False])
def test_cancel_closes_stream_and_allows_retry(tmp_path, immediate):
    async def exercise():
        router = Router(wait=True)
        chat = service(tmp_path, router)
        session = chat.sessions.create(kind="chat")
        chat.start(session.session_id, "Hello")
        if not immediate:
            await asyncio.sleep(0.03)
        await chat.cancel(session.session_id)
        assert not chat.tasks
        assert (
            chat.sessions.get(session.session_id).messages[-1].metadata["status"]
            == "cancelled"
        )
        if not immediate:
            assert router.closed
        router.wait = False
        chat.start(session.session_id, "", retry=True)
        await chat.tasks[session.session_id]
        assert (
            chat.sessions.get(session.session_id).messages[-1].metadata["status"]
            == "completed"
        )
        assert (
            len(
                [
                    m
                    for m in chat.sessions.get(session.session_id).messages
                    if m.role == "user"
                ]
            )
            == 1
        )

    asyncio.run(exercise())


def test_failure_and_restart_recovery(tmp_path):
    async def exercise():
        chat = service(tmp_path, Router(fail=True))
        session = chat.sessions.create(kind="chat")
        chat.start(session.session_id, "Hello")
        await chat.tasks[session.session_id]
        message = chat.sessions.get(session.session_id).messages[-1]
        assert message.metadata["status"] == "failed"
        assert "private provider exception" not in message.content
        chat.sessions.update_message(
            session.session_id,
            message.message_id,
            content="partial",
            status="streaming",
        )
        restored = service(tmp_path)
        assert (
            restored.sessions.get(session.session_id).messages[-1].metadata["status"]
            == "interrupted"
        )

    asyncio.run(exercise())


def test_specialist_activity_is_separate_and_correlated(tmp_path):
    events = AgentOrchestrationStore(tmp_path / "events.db")

    def emit(kind, agent="child", **kwargs):
        return events.append_event(
            "s",
            dict(
                run_id="r",
                agent_id=agent,
                parent_agent_id="main",
                event_type=kind,
                **kwargs,
            ),
        )["activity_record"]

    start = emit("SUBAGENT_STARTED", message="Inspect files")
    end = emit("SUBAGENT_COMPLETED", message="Inspection completed")
    assert start["id"] == end["id"]
    assert end["status"] == "completed"
    assert end["parentId"] == "main"
    assert (
        emit("tool.started", tool="read_file", tool_call_id="1")["id"]
        != emit("tool.started", agent="main", tool="read_file", tool_call_id="1")["id"]
    )


def test_delayed_tool_packets_do_not_reopen_completed_activity(tmp_path):
    events = AgentOrchestrationStore(tmp_path / "events.db")

    def emit(kind, **kwargs):
        return events.append_event(
            "s",
            dict(
                run_id="r",
                tool="run_command",
                tool_call_id="call",
                event_type=kind,
                **kwargs,
            ),
        )["activity_record"]

    done = emit("tool.completed", duration_ms=72)
    late = emit("tool.started")
    output = emit("tool.output", output="last line")
    assert done["id"] == late["id"] == output["id"]
    assert output["status"] == late["status"] == "completed"
    assert output["durationMs"] == 72


def test_verify_specialist_cannot_report_success_after_failed_build(tmp_path):
    from backend.agent_runtime.subagents import ForgeSubagentRunner
    from backend.agent_runtime.tool_contracts import PRODUCT_TOOLPLAN_VERSION

    class Planner:
        def request_turn(self, **kwargs):
            first = kwargs["turn"] == 1
            return {
                "version": PRODUCT_TOOLPLAN_VERSION,
                "summary": "Checking the build" if first else "Everything passed",
                "tool_calls": (
                    [{"id": "build", "type": "build_firmware", "arguments": {}}]
                    if first
                    else []
                ),
                "final": not first,
            }

    class Build:
        async def build(self, *args, **kwargs):
            return SimpleNamespace(api_dict=lambda: {"success": False, "errors": 1})

    async def exercise():
        emitted = []

        async def progress(event, payload):
            emitted.append(event)

        runner = ForgeSubagentRunner(
            workspace_root=tmp_path,
            planner=Planner(),
            platformio=Build(),
            progress=progress,
        )
        result = await runner.run(role="verify", task="Verify firmware")
        assert not result.success
        assert emitted[-1] == "SUBAGENT_FAILED"
        assert "failed build" in result.summary

    asyncio.run(exercise())
