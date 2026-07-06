from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agent_runtime.product_provider_registry import ProductProviderRegistry
from backend.bridges.codex_subscription_review import (
    MARKER_CONTENT,
    MARKER_NAME,
    SMOKE_CONTENT,
    SMOKE_NAME,
    create_codex_subscription_review,
)
from backend.bridges.review_store import BridgeReviewStore


def sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "external" / "codex-0123456789abcdef"
    root.mkdir(parents=True)
    (root / MARKER_NAME).write_text(MARKER_CONTENT, encoding="utf-8")
    return root


def test_review_is_created_only_for_exact_pass(tmp_path: Path) -> None:
    root = sandbox(tmp_path)
    (root / SMOKE_NAME).write_text(SMOKE_CONTENT, encoding="utf-8")
    state = tmp_path / "state"

    review_id = create_codex_subscription_review(root, state)
    reviews = BridgeReviewStore(
        snapshots_path=state / "bridge-snapshots.jsonl",
        reviews_path=state / "bridge-reviews.jsonl",
    ).load_reviews()
    review = reviews[review_id]
    assert review.provider_id == "codex_cli_subscription"
    assert [(item.path, item.change_type) for item in review.changed_files] == [(SMOKE_NAME, "created")]
    assert review.artifact_source == "codex_exec"
    assert review.artifact_type == "codex_subscription_bridge_diff"
    assert review.artifact_metadata == {
        "transport": "codex_exec",
        "auth_mode": "official_cli_auth",
        "execution_mode": "cli_auth_bridge",
        "workspace_mode": "external_managed_sandbox",
        "classification": "CODEX_SUBSCRIPTION_BRIDGE_PASS",
        "instruction_type": "codex_subscription_bridge_smoke",
        "expected_filename": SMOKE_NAME,
        "expected_content_hash": "6d2d37f75801e9ebfb5e2ce4130e7d49a2280cb02b97e6c3926c6075fc54e9f8",
        "created_file_count": 1,
        "modified_file_count": 0,
        "deleted_file_count": 0,
        "active_workspace_unchanged": True,
        "auto_apply": False,
        "auto_build": False,
        "auto_flash": False,
    }
    persisted = (state / "bridge-reviews.jsonl").read_text(encoding="utf-8")
    assert str(root) not in persisted
    assert "stdout" not in persisted and "stderr" not in persisted and "raw_prompt" not in persisted


@pytest.mark.parametrize(
    ("content", "extra"),
    [(None, False), ("wrong", False), (SMOKE_CONTENT, True)],
)
def test_no_review_for_no_change_invalid_content_or_extra_change(
    tmp_path: Path, content: str | None, extra: bool
) -> None:
    root = sandbox(tmp_path)
    if content is not None:
        (root / SMOKE_NAME).write_text(content, encoding="utf-8")
    if extra:
        (root / "EXTRA.txt").write_text("extra", encoding="utf-8")
    state = tmp_path / "state"
    with pytest.raises(ValueError):
        create_codex_subscription_review(root, state)
    assert not (state / "bridge-reviews.jsonl").exists()


def test_product_registry_keeps_subscription_bridge_qa_only_after_pass(tmp_path: Path) -> None:
    state = tmp_path / ".promptforge" / "state"
    state.mkdir(parents=True)
    (state / "codex-subscription-bridge-status.json").write_text(
        json.dumps({"classification": "CODEX_SUBSCRIPTION_BRIDGE_PASS"}), encoding="utf-8"
    )
    entry = next(
        item
        for item in ProductProviderRegistry(env={"PROMPTFORGE_ROOT": str(tmp_path)}).list()
        if item.provider_id == "codex_cli_subscription"
    )
    assert entry.experimental and entry.review_eligible and entry.qa_only
    assert not entry.production_eligible and not entry.product_routing_enabled and not entry.routeable
    with pytest.raises(ValueError, match="PRODUCT_PROVIDER_NOT_ROUTEABLE"):
        ProductProviderRegistry(env={"PROMPTFORGE_ROOT": str(tmp_path)}).resolve(entry.provider_id)


def test_product_registry_records_failure_as_paused_reason(tmp_path: Path) -> None:
    state = tmp_path / ".promptforge" / "state"
    state.mkdir(parents=True)
    (state / "codex-subscription-bridge-status.json").write_text(
        json.dumps({"classification": "CODEX_NATIVE_USAGE_LIMIT_REACHED"}), encoding="utf-8"
    )
    entry = next(
        item
        for item in ProductProviderRegistry(env={"PROMPTFORGE_ROOT": str(tmp_path)}).list()
        if item.provider_id == "codex_cli_subscription"
    )
    assert entry.experimental and entry.qa_only and not entry.review_eligible
    assert entry.paused_reason == "CODEX_NATIVE_USAGE_LIMIT_REACHED"
