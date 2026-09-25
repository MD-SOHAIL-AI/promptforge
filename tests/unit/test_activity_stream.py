from backend.agent_runtime.orchestration_store import AgentOrchestrationStore


def test_lifecycle_persists_and_replays(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "events.db")

    def emit(kind, **payload):
        return store.append_event(
            "session",
            dict(
                run_id="run",
                event_type=kind,
                tool="run_command",
                tool_call_id="call",
                **payload
            ),
        )["activity_record"]

    start = emit(
        "tool.started", arguments={"command": "npm run build", "reasoning": "PRIVATE"}
    )
    output = emit("tool.output", output="hello")
    end = emit("tool.completed", result={"exit_code": 1}, duration_ms=40)
    assert start["id"] == output["id"] == end["id"]
    assert end["status"] == "failed"
    assert end["durationMs"] == 40
    assert end["details"]["output"] == "hello"
    assert "PRIVATE" not in str(end)
    restored = AgentOrchestrationStore(tmp_path / "events.db")
    assert restored.events("session")[-1]["activity_record"] == end


def test_attempts_approval_and_bounded_output(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "events.db")

    def emit(kind, **payload):
        return store.append_event("s", dict(run_id="r", event_type=kind, **payload))[
            "activity_record"
        ]

    first = emit("build_started")
    emit("build_failed")
    retry = emit("build_started")
    assert retry["id"] != first["id"]
    assert len(emit("build_output", output="x" * 20000)["details"]["output"]) == 16000
    approval = emit("runtime.awaiting_flash_confirmation")
    approved = emit("flash.confirmed", node_id="flash")
    assert approved["id"] == approval["id"]
    assert approved["status"] == "completed"


def test_tool_edits_include_real_diff(tmp_path):
    from backend.agent_runtime.tool_executor import _edit_evidence

    evidence = _edit_evidence("main.cpp", "old\n", "new\nextra\n")
    assert evidence["added"] == 2
    assert evidence["removed"] == 1
    assert "+extra" in evidence["diff"]


def test_real_process_streams_and_cleans_observer():
    import asyncio
    import sys
    from backend.runtime.process_output import observe_output, observer
    from backend.runtime.subprocess_mgr import ProcessConfig, SubprocessManager

    async def check():
        chunks = []

        async def receive(chunk):
            chunks.append(chunk)

        with observe_output(receive):
            result = await SubprocessManager().run(
                ProcessConfig(
                    args=[
                        sys.executable,
                        "-u",
                        "-c",
                        "print('first', flush=True); import time; time.sleep(.2); print('second')",
                    ]
                )
            )
        assert result.success
        assert "first" in "".join(chunks) and "second" in "".join(chunks)
        assert observer.get() is None
        try:
            with observe_output(receive):
                raise asyncio.CancelledError()
        except asyncio.CancelledError:
            pass
        assert observer.get() is None

    asyncio.run(check())


def test_context_and_thinking_use_public_unified_states(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "events.db")
    started = store.append_event(
        "session",
        {"run_id": "run", "event_type": "context.started", "status": "running"},
    )["activity_record"]
    completed = store.append_event(
        "session",
        {
            "run_id": "run",
            "event_type": "context.completed",
            "status": "completed",
            "message": "Loaded verified project context",
            "context_files": 27,
            "memory_count": 1,
            "recalled_context": [{"kind": "board", "summary": "ESP32"}],
        },
    )["activity_record"]
    thinking = store.append_event(
        "session",
        {
            "run_id": "run",
            "event_type": "thinking.started",
            "status": "running",
            "turn": 1,
        },
    )["activity_record"]

    assert started["id"] == completed["id"]
    assert completed["state"] == "recalling_context"
    assert completed["eventType"] == "context.completed"
    assert completed["displayStatus"] == "success"
    assert completed["details"]["recalled_context"][0]["summary"] == "ESP32"
    assert thinking["state"] == "thinking"
    assert thinking["eventType"] == "thinking.started"


def test_operational_activity_uses_host_message_as_public_summary(tmp_path):
    store = AgentOrchestrationStore(tmp_path / "events.db")
    activity = store.append_event(
        "session",
        {
            "run_id": "run",
            "event_type": "tool.started",
            "status": "running",
            "tool": "grep_search",
            "tool_call_id": "call-1",
            "turn": 2,
            "step": 3,
            "message": "Inspecting Runtime APIs and Steering Support",
            "arguments": {"query": "steer"},
        },
    )["activity_record"]

    assert activity["details"]["summary"] == "Inspecting Runtime APIs and Steering Support"
    assert activity["details"]["turn"] == 2
    assert activity["details"]["step"] == 3
    assert activity["details"]["tool"] == "grep_search"
    assert "reasoning" not in str(activity).casefold()


def test_canonical_tool_settlement_closes_running_activity() -> None:
    from backend.agent_runtime.activity_stream import project_activity

    started = project_activity({
        "event_type": "tool.execution.started",
        "run_id": "run-1",
        "sequence": 1,
        "created_at": "2026-09-15T00:00:00+00:00",
        "status": "running",
        "tool": "read_file",
        "tool_call_id": "call-1",
        "path": "src/main.cpp",
        "message": "Reading src/main.cpp",
    })
    assert started is not None and started["status"] == "running"
    settled = project_activity({
        "event_type": "tool.execution.settled",
        "run_id": "run-1",
        "sequence": 2,
        "created_at": "2026-09-15T00:00:01+00:00",
        "status": "completed",
        "tool": "read_file",
        "tool_call_id": "call-1",
        "path": "src/main.cpp",
        "message": "Read completed",
    }, started)
    assert settled is not None
    assert settled["id"] == started["id"]
    assert settled["status"] == "completed"
