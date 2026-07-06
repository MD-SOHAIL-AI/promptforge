from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.bridges.patch_store import BridgePatchRecord, BridgePatchStore, BridgePatchStoreError


def record(patch_id: str = "review-1.patch", *, created_at: datetime | None = None) -> BridgePatchRecord:
    return BridgePatchRecord(
        patch_id=patch_id,
        review_id=patch_id.removesuffix(".patch"),
        provider_id="antigravity_cli_bridge",
        created_at=created_at or datetime.now(timezone.utc),
        patch_path=patch_id,
        metadata_path=f"{patch_id.removesuffix('.patch')}.metadata.json",
        patch_size=123,
        patch_sha256="a" * 64,
        integrity_status="valid",
        changed_file_count=1,
        created_files=(),
        modified_files=("src/main.cpp",),
        deleted_files=(),
        workspace_root_hash="workspace-hash",
        review_status_at_export="pending",
        apply_enabled=False,
    )


def test_patch_store_writes_metadata_without_patch_content(tmp_path: Path) -> None:
    store = BridgePatchStore(tmp_path / "patches")
    item = record()

    store.upsert(item)

    contents = (tmp_path / "patches" / "index.jsonl").read_text(encoding="utf-8")
    assert "src/main.cpp" in contents
    assert "diff --git" not in contents
    assert "+new" not in contents
    assert store.get(item.patch_id).patch_sha256 == item.patch_sha256


def test_patch_store_lists_with_filters(tmp_path: Path) -> None:
    store = BridgePatchStore(tmp_path / "patches")
    store.upsert(record("review-1.patch"))
    other = record("review-2.patch")
    store.upsert(type(other)(
        **{
            **other.to_dict(),
            "created_at": other.created_at,
            "provider_id": "codex_bridge",
            "integrity_status": "missing",
            "created_files": tuple(other.created_files),
            "modified_files": tuple(other.modified_files),
            "deleted_files": tuple(other.deleted_files),
        }
    ))

    assert len(store.list_records()) == 2
    assert [item.patch_id for item in store.list_records(provider_id="codex_bridge")] == ["review-2.patch"]
    assert [item.patch_id for item in store.list_records(integrity_status="valid")] == ["review-1.patch"]


def test_patch_store_removes_record(tmp_path: Path) -> None:
    store = BridgePatchStore(tmp_path / "patches")
    item = record()
    store.upsert(item)

    removed = store.remove(item.patch_id)

    assert removed.patch_id == item.patch_id
    assert store.list_records() == []


def test_patch_store_rejects_unsafe_relative_paths(tmp_path: Path) -> None:
    store = BridgePatchStore(tmp_path / "patches")

    with pytest.raises(BridgePatchStoreError):
        store.safe_child("../escape.patch")
    with pytest.raises(BridgePatchStoreError):
        store.safe_child("nested/escape.patch")
