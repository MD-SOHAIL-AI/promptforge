from __future__ import annotations

from backend.services.generation_strategy import (
    estimate_model_capability,
    estimate_tokens,
    select_generation_strategy,
)


def test_simple_blink_prompt_selects_one_shot_generation() -> None:
    decision = select_generation_strategy(
        prompt_text="Create an ESP32 blink LED PlatformIO project.",
        model_id="gpt-4.1",
        task_type="FIRMWARE_GENERATION",
        expected_file_count=2,
    )

    assert decision.strategy == "one_shot"
    assert decision.reasons == ()


def test_long_advanced_prompt_selects_chunked_generation() -> None:
    prompt = (
        "Create an advanced ESP32-only PlatformIO project with WiFi AP, "
        "WebServer dashboard, Preferences, FreeRTOS tasks, REST APIs, README, "
        "embedded dark UI, diagnostics, serial commands, and multiple files. "
        * 20
    )

    decision = select_generation_strategy(
        prompt_text=prompt,
        model_id="openai/gpt-oss-120b:free",
        task_type="FIRMWARE_GENERATION",
        expected_file_count=4,
    )

    assert decision.strategy == "chunked"
    assert "advanced_or_multifile_prompt" in decision.reasons
    assert "expected_multiple_files" in decision.reasons


def test_natural_language_web_server_prompt_selects_chunked_generation() -> None:
    decision = select_generation_strategy(
        prompt_text="Create an ESP32 WiFi web server that controls an LED from a browser.",
        model_id="codex-agent",
        task_type="FIRMWARE_GENERATION",
        expected_file_count=3,
    )

    assert decision.strategy == "chunked"
    assert "advanced_or_multifile_prompt" in decision.reasons


def test_model_capability_uses_safe_default_for_free_models() -> None:
    capability = estimate_model_capability("openai/gpt-oss-120b:free")

    assert capability.max_output_estimate <= 2048
    assert capability.supports_large_generation is False


def test_token_estimator_is_simple_and_stable() -> None:
    assert estimate_tokens("a" * 400) == 100
