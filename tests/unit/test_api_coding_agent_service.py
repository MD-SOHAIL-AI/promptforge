from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agent_runtime.api_agent_contracts import API_CODING_AGENT_SCHEMA_VERSION
from backend.agent_runtime.api_coding_agent_service import (
    FAKE_API_CODING_AGENT_DISABLED,
    ApiCodingAgentService,
    ApiCodingAgentServiceError,
)
from backend.bridges.diff_service import BridgeDiffService
from backend.bridges.agy_execution_router import AGYCutoverFlags
from backend.bridges.codex_status import build_codex_safe_user_env
from backend.bridges.review_store import BridgeReviewStore
from backend.bridges.sandbox_service import BridgeSandboxService
from backend.agent_runtime.product_provider_registry import ProductProviderRegistry


class StaticProvider:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def generate(self, prompt: str, context: object | None = None) -> str:
        del prompt, context
        return json.dumps(self.payload)


def make_service(
    tmp_path: Path,
    *,
    enabled: bool,
    provider: object | None = None,
) -> tuple[ApiCodingAgentService, BridgeDiffService, Path]:
    managed = tmp_path / "managed" / "api-coding-agent-sandboxes"
    reviews = BridgeDiffService(store=BridgeReviewStore(
        snapshots_path=tmp_path / "state" / "snapshots.jsonl",
        reviews_path=tmp_path / "state" / "reviews.jsonl",
    ))
    service = ApiCodingAgentService(
        sandbox_service=BridgeSandboxService(managed),
        review_service=reviews,
        provider=provider,  # type: ignore[arg-type]
        enabled=enabled,
    )
    return service, reviews, managed


def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "active"
    root.mkdir()
    (root / "README.md").write_text("active workspace\n", encoding="utf-8")
    return root


def test_fake_blink_creates_standard_review_without_active_mutation(tmp_path: Path) -> None:
    active = workspace(tmp_path)
    service, reviews, managed = make_service(tmp_path, enabled=True)

    result = service.generate_review_from_fake_provider("basic esp32 blink", active)

    assert result.status == "review_created"
    assert result.files == ("platformio.ini", "src/main.cpp")
    assert result.review_id is not None
    assert result.review_path is not None
    assert result.commands_suggested[0].command == "pio run"
    assert (active / "README.md").read_text(encoding="utf-8") == "active workspace\n"
    assert not (active / "platformio.ini").exists()
    assert not (active / "src" / "main.cpp").exists()

    sandbox = Path(result.review_path)
    sandbox.resolve().relative_to(managed.resolve())
    assert (sandbox / "platformio.ini").is_file()
    assert (sandbox / "src" / "main.cpp").is_file()
    review = reviews.get_review(result.review_id)
    assert review.provider_id == "fake_api_coding_agent"
    assert review.status == "pending"
    assert {item.path for item in review.changed_files} == {"platformio.ini", "src/main.cpp"}
    assert review.artifact_metadata == {
        "run_id": review.artifact_metadata["run_id"],  # type: ignore[index]
        "schema_version": API_CODING_AGENT_SCHEMA_VERSION,
        "dev_only": True,
        "real_api_calls": False,
    }


@pytest.mark.parametrize("prompt", ["unsafe path", "protected path", "invalid json", "oversized"])
def test_invalid_fake_proposals_are_rejected_before_sandbox_creation(tmp_path: Path, prompt: str) -> None:
    active = workspace(tmp_path)
    service, reviews, managed = make_service(tmp_path, enabled=True)

    result = service.generate_review_from_fake_provider(prompt, active)

    assert result.status == "rejected"
    assert result.review_id is None
    assert result.validation_errors
    assert reviews.list_reviews() == ()
    assert not managed.exists()
    assert (active / "README.md").read_text(encoding="utf-8") == "active workspace\n"


def test_service_is_disabled_by_default() -> None:
    service = ApiCodingAgentService(
        sandbox_service=BridgeSandboxService("unused"),
        review_service=BridgeDiffService(),
        env={},
    )
    with pytest.raises(ApiCodingAgentServiceError) as exc:
        service.generate_review_from_fake_provider("blink", Path("unused"))
    assert exc.value.code == FAKE_API_CODING_AGENT_DISABLED


def test_environment_flag_enables_only_this_service(tmp_path: Path) -> None:
    service = ApiCodingAgentService(
        sandbox_service=BridgeSandboxService(tmp_path / "managed"),
        review_service=BridgeDiffService(),
        env={"FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1"},
    )
    assert service.enabled is True


def test_fake_agent_flag_does_not_enable_codex_agy_or_provider_routing() -> None:
    flagged = {"FORGEX_ENABLE_FAKE_API_CODING_AGENT": "1"}

    assert AGYCutoverFlags.from_environment(flagged) == AGYCutoverFlags()
    assert build_codex_safe_user_env(flagged) == {}
    assert ProductProviderRegistry(env=flagged).safe_statuses() == ProductProviderRegistry(env={}).safe_statuses()


def delete_payload() -> dict[str, object]:
    return {
        "schema_version": API_CODING_AGENT_SCHEMA_VERSION,
        "summary": "Delete obsolete file.",
        "files": [{"path": "obsolete.txt", "action": "delete"}],
        "commands_suggested": [],
        "risks": [],
        "next_steps": ["Review deletion."],
    }


def test_delete_requires_explicit_permission_and_only_changes_sandbox(tmp_path: Path) -> None:
    active = workspace(tmp_path)
    (active / "obsolete.txt").write_text("keep active\n", encoding="utf-8")
    service, reviews, _ = make_service(tmp_path, enabled=True, provider=StaticProvider(delete_payload()))

    denied = service.generate_review_from_fake_provider("delete", active)
    assert denied.status == "rejected"
    assert any("delete is disabled" in error for error in denied.validation_errors)

    allowed = service.generate_review_from_fake_provider("delete", active, allow_delete=True)
    assert allowed.status == "review_created"
    assert (active / "obsolete.txt").read_text(encoding="utf-8") == "keep active\n"
    assert allowed.review_path is not None
    assert not (Path(allowed.review_path) / "obsolete.txt").exists()
    review = reviews.get_review(allowed.review_id or "")
    assert review.changed_files[0].change_type == "deleted"
