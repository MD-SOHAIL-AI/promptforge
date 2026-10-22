from __future__ import annotations

import json
from pathlib import Path
from backend.observability.diagnostics import live_log_path

from backend.observability.diagnostics import emit_diagnostic_event, redact


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_api_keys_and_authorization_headers_are_redacted(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "live-runtime.jsonl"
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(log_path))
    log_path = live_log_path()

    emit_diagnostic_event(
        component="provider",
        event="provider.request.failed",
        headers={"Authorization": "Bearer sk-or-v1-supersecret1234567890", "x-api-key": "sk-test1234567890abc"},
        message="failed with Bearer sk-test1234567890abc",
    )

    text = log_path.read_text(encoding="utf-8")
    assert "sk-or-v1-supersecret1234567890" not in text
    assert "sk-test1234567890abc" not in text
    event = _read_jsonl(log_path)[0]
    assert event["headers"] == {"Authorization": "[redacted]", "x-api-key": "[redacted]"}
    assert event["message"] == "failed with Bearer [redacted]"


def test_malformed_log_events_do_not_crash(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "live-runtime.jsonl"
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(log_path))
    log_path = live_log_path()

    emitted = emit_diagnostic_event(
        component="runtime",
        event="runtime.event",
        non_json={1, 2, 3},
        nested={"ok": True, "password": "secret"},
    )

    assert emitted["nested"] == {"ok": True, "password": "[redacted]"}
    assert log_path.exists()
    assert _read_jsonl(log_path)[0]["event"] == "runtime.event"


def test_log_rotation(tmp_path: Path, monkeypatch) -> None:
    log_path = tmp_path / "live-runtime.jsonl"
    monkeypatch.setenv("FORGEX_LIVE_LOG_PATH", str(log_path))
    log_path = live_log_path()
    monkeypatch.setenv("FORGEX_LIVE_LOG_MAX_BYTES", str(64 * 1024))
    log_path.write_text("x" * (64 * 1024), encoding="utf-8")

    emit_diagnostic_event(component="runtime", event="runtime.rotated", message="after rotation")

    assert log_path.with_name(log_path.name + ".1").exists()
    assert _read_jsonl(log_path)[0]["event"] == "runtime.rotated"


def test_redact_handles_nested_provider_secrets() -> None:
    value = redact({
        "provider": "openrouter",
        "body": "key sk-or-v1-abcdefghijklmnopqrstuvwxyz",
        "cookie": "session=abc",
    })
    assert value == {
        "provider": "openrouter",
        "body": "key [redacted]",
        "cookie": "[redacted]",
    }


def test_numeric_usage_counters_survive_redaction_without_allowing_secret_values():
    assert redact({"input_tokens": 12, "output_tokens": 7, "api_token": 123,
                   "total_tokens": "secret", "nested": {"cached_tokens": 3, "token": "value"}}) == {
        "input_tokens": 12, "output_tokens": 7, "api_token": "[redacted]", "total_tokens": "[redacted]",
        "nested": {"cached_tokens": 3, "token": "[redacted]"}}
