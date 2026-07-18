from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.model_router import ModelRequest, ModelResponse
from backend.model_router.router import ModelRouterService
from backend.services.llm_service import LLMProvider, LLMProviderError
from tests.integration.test_coding_workflow_routes import (
    BASE,
    DIRECT_FAKE,
    apply,
    build,
    configure_env,
    flash,
    make_route_rig,
    make_workspace,
    monitor,
)


API_ROUTE = f"{BASE}/api/generate-review"
FAKE_ROUTE = f"{BASE}/fake/generate-review"


class StubModelRouter(ModelRouterService):
    def __init__(self, content: str | None = None, exc: Exception | None = None) -> None:
        self.content = content or proposal_json()
        self.exc = exc
        self.requests: list[ModelRequest] = []

    async def generate_model(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.exc is not None:
            raise self.exc
        return ModelResponse(
            content=self.content,
            provider_id=request.provider_id or "openrouter",
            model_id=request.model_id or "test-model",
        )


def proposal_json(path: str = "src/main.cpp", content: str = "void setup() {}\nvoid loop() {}\n") -> str:
    return json.dumps({
        "schema_version": "forgex.api_coding_agent.v1",
        "summary": "Create a review-only source update.",
        "files": [{"path": path, "action": "create_or_update", "content": content}],
        "commands_suggested": [{"command": "echo should-not-run", "reason": "Advisory only"}],
        "risks": ["Review before apply."],
        "next_steps": ["Approve apply if acceptable."],
    })


def proposal_with_schema(schema_version: str) -> str:
    payload = json.loads(proposal_json())
    payload["schema_version"] = schema_version
    return json.dumps(payload)


def proposal_with_action(action: str) -> str:
    payload = json.loads(proposal_json())
    payload["files"][0]["action"] = action
    return json.dumps(payload)


def configure_real_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    unified: bool,
    fake: bool,
    real: bool,
) -> None:
    configure_env(monkeypatch, tmp_path, unified=unified, fake=fake)
    monkeypatch.setenv("FORGEX_ENABLE_REAL_API_CODING_AGENT", "1" if real else "0")


def make_real_route_rig(tmp_path: Path, router: StubModelRouter):
    rig = make_route_rig(tmp_path)
    rig.api.app.state.model_router_service = router
    return rig


def test_real_api_route_is_feature_gated_without_affecting_fake_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=False, fake=True, real=True)
    rig = make_real_route_rig(tmp_path, StubModelRouter())
    active = make_workspace(tmp_path)
    response = rig.api.post(API_ROUTE, json={"prompt": "blink", "workspace_path": str(active)})
    assert response.status_code == 403
    assert response.json()["code"] == "UNIFIED_CODING_WORKFLOW_DISABLED"

    configure_real_env(monkeypatch, tmp_path / "real-disabled", unified=True, fake=True, real=False)
    rig2 = make_real_route_rig(tmp_path / "real-disabled", StubModelRouter())
    active2 = make_workspace(tmp_path / "real-disabled")
    response = rig2.api.post(API_ROUTE, json={"prompt": "blink", "workspace_path": str(active2)})
    assert response.status_code == 403
    assert response.json()["code"] == "REAL_API_CODING_AGENT_DISABLED"

    fake_response = rig2.api.post(FAKE_ROUTE, json={"prompt": "basic esp32 blink", "workspace_path": str(active2)})
    direct_fake = rig2.api.post(DIRECT_FAKE, json={"prompt": "basic esp32 blink", "workspace_path": str(active2)})
    assert fake_response.status_code == 200
    assert direct_fake.status_code == 200
    assert fake_response.json()["provider_id"] == "fake_api_coding_agent"


def test_real_api_route_creates_awaiting_apply_review_without_downstream_stages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=True, fake=False, real=True)
    router = StubModelRouter()
    rig = make_real_route_rig(tmp_path, router)
    active = make_workspace(tmp_path)
    (active / "platformio.ini").write_text("[env:esp32dev]\n", encoding="utf-8")
    (active / "src").mkdir(exist_ok=True)
    (active / "src" / "main.cpp").write_text("old source\n", encoding="utf-8")
    (active / ".env").write_text("OPENAI_API_KEY=sk-secret\n", encoding="utf-8")

    response = rig.api.post(
        API_ROUTE,
        json={
            "prompt": "Create blink update",
            "workspace_path": str(active),
            "context_mode": "selected_files",
            "selected_files": ["platformio.ini", "src/main.cpp", ".env"],
            "provider_id": "openrouter",
            "model": "openai/gpt-test",
            "live_api_confirmed": True,
        },
    )
    payload = response.json()

    assert response.status_code == 200, response.text
    assert payload["status"] == "awaiting_apply"
    assert payload["generation_status"] == "review_created"
    assert payload["next_action"] == "await_user_approval"
    assert payload["provider_id"] == "api_coding_agent"
    assert payload["review_id"]
    assert payload["files_changed"] == ["src/main.cpp"]
    assert [event["event_type"] for event in payload["events"]] == [
        "provider.selected",
        "generation.started",
        "generation.completed",
        "review.created",
        "apply.waiting_for_approval",
    ]
    assert len(router.requests) == 1
    assert "Return ONLY valid JSON." in router.requests[0].system_prompt
    assert "Do not use markdown fences." in router.requests[0].system_prompt
    assert "schema_version = forgex.api_coding_agent.v1" in router.requests[0].system_prompt
    assert "Do not modify .env, .git, generated folders, or protected paths." in router.requests[0].system_prompt
    assert "OPENAI_API_KEY" not in router.requests[0].prompt
    assert "sk-secret" not in router.requests[0].prompt
    assert (active / "src" / "main.cpp").read_text(encoding="utf-8") == "old source\n"
    assert not (active / "executed.txt").exists()
    assert rig.provider.prompts == []
    assert rig.builder.calls == []
    assert rig.flasher.calls == []
    assert rig.detector.calls == 0
    assert rig.monitor.observe_calls == 0

    run = rig.api.get(f"{BASE}/{payload['run_id']}").json()["run"]
    events = rig.api.get(f"{BASE}/{payload['run_id']}/events").json()["events"]
    encoded = json.dumps({"run": run, "events": events}, sort_keys=True)
    assert "old source" not in encoded
    assert "void setup" not in encoded
    assert "OPENAI_API_KEY" not in encoded
    assert "sk-secret" not in encoded
    assert run["metadata"]["context_file_count"] == 2
    assert run["metadata"]["real_api_calls"] is True
    assert [event["event_type"] for event in events][:5] == [
        "provider.selected",
        "generation.started",
        "generation.completed",
        "review.created",
        "apply.waiting_for_approval",
    ]


def test_real_api_generated_run_continues_through_existing_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=True, fake=False, real=True)
    router = StubModelRouter(proposal_json(
        path="src/main.cpp",
        content="#include <Arduino.h>\nvoid setup() {}\nvoid loop() {}\n",
    ))
    rig = make_real_route_rig(tmp_path, router)
    active = make_workspace(tmp_path)
    (active / "platformio.ini").write_text("[env:esp32dev]\nframework = arduino\n", encoding="utf-8")
    (active / "src").mkdir(exist_ok=True)
    (active / "src" / "main.cpp").write_text("// old\n", encoding="utf-8")

    generated_response = rig.api.post(
        API_ROUTE,
        json={
            "prompt": "Create blink update",
            "workspace_path": str(active),
            "context_mode": "selected_files",
            "selected_files": ["platformio.ini", "src/main.cpp"],
            "live_api_confirmed": True,
        },
    )
    assert generated_response.status_code == 200, generated_response.text
    generated = generated_response.json()
    run_id = generated["run_id"]
    assert generated["status"] == "awaiting_apply"
    assert len(router.requests) == 1
    assert rig.provider.prompts == []
    assert rig.builder.calls == []
    assert rig.flasher.calls == []
    assert rig.monitor.observe_calls == 0

    applied = apply(rig.api, run_id, active)
    assert applied["status"] == "awaiting_build"
    assert rig.provider.prompts == []
    assert rig.builder.calls == []
    built = build(rig.api, run_id, active)
    assert built["status"] == "awaiting_flash"
    assert len(rig.builder.calls) == 1
    assert rig.flasher.calls == []
    flashed = flash(rig.api, run_id, active)
    assert flashed["status"] == "awaiting_monitor"
    assert len(rig.flasher.calls) == 1
    assert rig.monitor.observe_calls == 0
    completed = monitor(rig.api, run_id)
    assert completed["status"] == "completed"
    assert completed["next_action"] is None
    assert rig.monitor.observe_calls == 1
    assert len(router.requests) == 1

    run = rig.api.get(f"{BASE}/{run_id}").json()["run"]
    events = rig.api.get(f"{BASE}/{run_id}/events").json()["events"]
    assert run["status"] == "completed"
    assert run["provider_id"] == "api_coding_agent"
    assert events[-1]["event_type"] == "workflow.completed"
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    event_types = [event["event_type"] for event in events]
    for expected in (
        "provider.selected",
        "generation.started",
        "generation.completed",
        "review.created",
        "apply.waiting_for_approval",
        "apply.completed",
        "build.completed",
        "flash.completed",
        "monitor.completed",
        "workflow.completed",
    ):
        assert expected in event_types


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("not json", "API_CODING_AGENT_CONTRACT_INVALID"),
        (f"```json\n{proposal_json()}\n```", "API_CODING_AGENT_CONTRACT_INVALID"),
        (proposal_with_schema("forgex.api_coding_agent.v0"), "API_CODING_AGENT_CONTRACT_INVALID"),
        (proposal_json(path="../evil.txt"), "CODING_PROVIDER_UNSAFE_OUTPUT"),
        (proposal_json(content="x" * (64 * 1024 + 1)), "CODING_PROVIDER_OVERSIZED_OUTPUT"),
        (proposal_with_action("execute_command"), "API_CODING_AGENT_CONTRACT_INVALID"),
    ],
    ids=["prose", "fenced-json", "wrong-schema", "unsafe-path", "oversized", "unsupported-action"],
)
def test_real_api_route_rejects_invalid_model_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str,
    code: str,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=True, fake=False, real=True)
    rig = make_real_route_rig(tmp_path, StubModelRouter(content=content))
    active = make_workspace(tmp_path)

    response = rig.api.post(API_ROUTE, json={"prompt": "blink", "workspace_path": str(active), "live_api_confirmed": True})

    assert response.status_code == 422
    assert response.json()["code"] == code
    assert "Traceback" not in response.text
    assert "void setup" not in response.text
    assert "```json" not in response.text
    assert rig.builder.calls == []
    assert rig.flasher.calls == []
    assert rig.monitor.observe_calls == 0
    assert not (active / "platformio.ini").exists()
    assert not (active / "src" / "main.cpp").exists()


def test_real_api_route_maps_provider_failure_to_safe_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=True, fake=False, real=True)
    rig = make_real_route_rig(
        tmp_path,
        StubModelRouter(exc=LLMProviderError("provider leaked sk-secret", provider=LLMProvider.OPENAI)),
    )
    active = make_workspace(tmp_path)

    response = rig.api.post(API_ROUTE, json={"prompt": "blink", "workspace_path": str(active), "live_api_confirmed": True})

    assert response.status_code == 422
    assert response.json()["code"] == "API_CODING_MODEL_CALL_FAILED"
    assert "sk-secret" not in response.text
    assert "Traceback" not in response.text


def test_real_api_route_requires_live_confirmation_before_model_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure_real_env(monkeypatch, tmp_path, unified=True, fake=True, real=True)
    router = StubModelRouter()
    rig = make_real_route_rig(tmp_path, router)
    active = make_workspace(tmp_path)

    response = rig.api.post(API_ROUTE, json={"prompt": "blink", "workspace_path": str(active)})

    assert response.status_code == 403
    assert response.json()["code"] == "REAL_API_CODING_AGENT_CONFIRMATION_REQUIRED"
    assert router.requests == []

    fake_response = rig.api.post(FAKE_ROUTE, json={"prompt": "basic esp32 blink", "workspace_path": str(active)})
    assert fake_response.status_code == 200
