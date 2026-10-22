from backend.agent_runtime.autonomous_loop import AutonomousForgeAgentLoop


async def _progress(_event, _payload):
    return None


def _loop(tmp_path):
    return AutonomousForgeAgentLoop(
        workspace_root=tmp_path,
        planner=object(),
        platformio=object(),
        progress=_progress,
    )


def test_context_checkpoint_is_deterministic_and_retains_failures(tmp_path):
    loop = _loop(tmp_path)
    loop.observations.extend(
        {
            "call_id": f"call-{index}",
            "tool": "read_file",
            "status": "failed" if index == 2 else "completed",
            "error": "stale read" if index == 2 else "",
            "output": {"index": index},
        }
        for index in range(30)
    )

    compacted, created = loop._compacted_observations(keep=24)

    assert len(compacted) == 25
    assert created is not None
    assert created["checkpoint_version"] == "forgex.context-checkpoint.v1"
    assert created["compacted_observations"] == 6
    assert created["failure_count"] == 1
    assert created["recent_failures"][0]["call_id"] == "call-2"
    assert len(created["observation_digest"]) == 64
    assert created["retrieval"] == "session_event_search"
    assert len(created["_artifact_delta"]) == 6

    repeated, duplicate = loop._compacted_observations(keep=24)
    assert duplicate is None
    assert repeated[0] == compacted[0]

    loop.observations.append({
        "call_id": "call-30", "tool": "grep_search", "status": "completed",
    })
    advanced, next_checkpoint = loop._compacted_observations(keep=24)
    assert next_checkpoint is not None
    assert next_checkpoint["compacted_observations"] == 7
    assert next_checkpoint["observation_digest"] != created["observation_digest"]
    assert len(next_checkpoint["_artifact_delta"]) == 1
    assert advanced[0]["call_id"] == "context_checkpoint_7"


def test_context_checkpoint_is_not_created_before_threshold(tmp_path):
    loop = _loop(tmp_path)
    loop.observations.extend({"call_id": str(index)} for index in range(24))
    compacted, created = loop._compacted_observations(keep=24)
    assert created is None
    assert len(compacted) == 24


def test_compaction_keeps_compiler_errors_visible_after_many_reads(tmp_path):
    loop = _loop(tmp_path)
    loop.build_attempted = True
    loop.last_build = {
        "success": False,
        "message": "Compilation failed",
        "error_code": "COMPILATION_ERROR",
        "diagnostics": "include/Pins.h:14:11: error: 'uint8_t' does not name a type\n",
    }
    loop.observations.extend(
        {"call_id": f"read-{index}", "tool": "list_files", "status": "completed"}
        for index in range(40)
    )
    compacted, _ = loop._compacted_observations()
    failure = compacted[0]["output"]["last_build_failure"]
    assert failure["compiler_errors"] == [
        "include/Pins.h:14:11: error: 'uint8_t' does not name a type"
    ]
    assert "before rebuilding" in failure["next_action"]
