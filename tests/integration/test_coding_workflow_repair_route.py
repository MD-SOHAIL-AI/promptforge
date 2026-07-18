from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.test_coding_workflow_real_api_route import StubModelRouter, proposal_json
from tests.integration.test_coding_workflow_routes import (
    BASE,
    apply,
    configure_env,
    generate,
    make_route_rig,
    make_workspace,
)


def configure_repair_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    unified: bool = True,
    fake: bool = True,
    real: bool = True,
    repair: bool = True,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=unified, fake=fake)
    monkeypatch.setenv("FORGEX_ENABLE_REAL_API_CODING_AGENT", "1" if real else "0")
    monkeypatch.setenv("FORGEX_ENABLE_CODING_AGENT_REPAIR_LOOP", "1" if repair else "0")


def make_build_failed_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, router: StubModelRouter):
    configure_repair_env(monkeypatch, tmp_path)
    rig = make_route_rig(tmp_path)
    rig.api.app.state.model_router_service = router
    active = make_workspace(tmp_path)
    generated = generate(rig.api, active)
    apply(rig.api, generated["run_id"], active)
    rig.builder.fail = True
    response = rig.api.post(
        f"{BASE}/{generated['run_id']}/build",
        json={"build_confirmed": True, "workspace_path": str(active), "environment": "esp32dev"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "CODING_WORKFLOW_BUILD_FAILED"
    assert rig.api.get(f"{BASE}/{generated['run_id']}").json()["run"]["status"] == "failed"
    return rig, active, generated["run_id"]


def test_repair_route_is_feature_gated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = StubModelRouter()
    rig, active, parent_run_id = make_build_failed_parent(tmp_path, monkeypatch, router=router)

    configure_repair_env(monkeypatch, tmp_path, repair=False)
    response = rig.api.post(
        f"{BASE}/{parent_run_id}/repair/build",
        json={"workspace_path": str(active), "selected_files": ["platformio.ini", "src/main.cpp"]},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "CODING_WORKFLOW_REPAIR_DISABLED"
    assert router.requests == []


def test_repair_route_rejects_non_build_failure_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_repair_env(monkeypatch, tmp_path)
    rig = make_route_rig(tmp_path)
    rig.api.app.state.model_router_service = StubModelRouter()
    active = make_workspace(tmp_path)
    generated = generate(rig.api, active)

    response = rig.api.post(
        f"{BASE}/{generated['run_id']}/repair/build",
        json={"workspace_path": str(active), "selected_files": ["platformio.ini", "src/main.cpp"]},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "CODING_WORKFLOW_REPAIR_NOT_ALLOWED"


def test_build_failed_run_generates_review_only_repair_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = StubModelRouter(proposal_json(
        path="src/main.cpp",
        content="#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n",
    ))
    rig, active, parent_run_id = make_build_failed_parent(tmp_path, monkeypatch, router=router)
    active_before = (active / "src" / "main.cpp").read_text(encoding="utf-8")

    response = rig.api.post(
        f"{BASE}/{parent_run_id}/repair/build",
        json={"workspace_path": str(active), "selected_files": ["platformio.ini", "src/main.cpp"]},
    )
    payload = response.json()

    assert response.status_code == 200, response.text
    assert payload["status"] == "awaiting_apply"
    assert payload["generation_status"] == "repair_review_created"
    assert payload["repair_of_run_id"] == parent_run_id
    assert payload["review_id"]
    assert payload["next_action"] == "await_user_approval"
    assert payload["files_changed"] == ["src/main.cpp"]
    assert [event["event_type"] for event in payload["events"]][:6] == [
        "repair.started",
        "provider.selected",
        "generation.started",
        "generation.completed",
        "review.created",
        "apply.waiting_for_approval",
    ]
    assert len(router.requests) == 1
    assert "ForgeX build did not complete successfully" in router.requests[0].prompt
    assert "raw compiler log" not in router.requests[0].prompt
    assert (active / "src" / "main.cpp").read_text(encoding="utf-8") == active_before
    assert len(rig.provider.prompts) == 1
    assert len(rig.builder.calls) == 1
    assert rig.flasher.calls == []
    assert rig.monitor.observe_calls == 0

    parent = rig.api.get(f"{BASE}/{parent_run_id}").json()["run"]
    repair = rig.api.get(f"{BASE}/{payload['run_id']}").json()["run"]
    events = rig.api.get(f"{BASE}/{payload['run_id']}/events").json()["events"]
    assert parent["status"] == "failed"
    assert repair["metadata"]["repair_of_run_id"] == parent_run_id
    assert repair["metadata"]["repair_attempt_number"] == 1
    assert repair["metadata"]["repair_reason"] == "build_failed"
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))


def test_repair_route_enforces_attempt_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = StubModelRouter()
    rig, active, parent_run_id = make_build_failed_parent(tmp_path, monkeypatch, router=router)
    for _ in range(2):
        response = rig.api.post(
            f"{BASE}/{parent_run_id}/repair/build",
            json={"workspace_path": str(active), "selected_files": ["platformio.ini", "src/main.cpp"]},
        )
        assert response.status_code == 200, response.text

    blocked = rig.api.post(
        f"{BASE}/{parent_run_id}/repair/build",
        json={"workspace_path": str(active), "selected_files": ["platformio.ini", "src/main.cpp"]},
    )

    assert blocked.status_code == 409
    assert blocked.json()["code"] == "CODING_WORKFLOW_REPAIR_LIMIT_EXCEEDED"
    assert len(router.requests) == 2


def test_repair_route_invalid_model_output_fails_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = StubModelRouter(content="not json with raw model body")
    rig, active, parent_run_id = make_build_failed_parent(tmp_path, monkeypatch, router=router)

    response = rig.api.post(
        f"{BASE}/{parent_run_id}/repair/build",
        json={"workspace_path": str(active), "selected_files": ["platformio.ini", "src/main.cpp"]},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "API_CODING_AGENT_CONTRACT_INVALID"
    assert "raw model body" not in response.text
    assert rig.flasher.calls == []
    assert rig.monitor.observe_calls == 0
