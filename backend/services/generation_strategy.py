"""Context-budget based generation strategy selection."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "GenerationStrategyDecision",
    "ModelCapability",
    "estimate_model_capability",
    "estimate_tokens",
    "select_generation_strategy",
]


@dataclass(frozen=True, slots=True)
class ModelCapability:
    model_id: str
    context_window_estimate: int
    max_output_estimate: int
    supports_large_generation: bool


@dataclass(frozen=True, slots=True)
class GenerationStrategyDecision:
    strategy: str
    estimated_prompt_tokens: int
    expected_file_count: int
    expected_output_tokens: int
    model: ModelCapability
    reasons: tuple[str, ...]


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def estimate_model_capability(model_id: str | None) -> ModelCapability:
    normalized = (model_id or "unknown").casefold()
    if any(marker in normalized for marker in ("free", "mini", "small", "3.5", "7b", "8b", "oss-20b")):
        return ModelCapability(model_id or "unknown", 8192, 2048, False)
    if any(marker in normalized for marker in ("120b", "70b", "gpt-4", "gpt-5", "claude", "gemini-1.5", "gemini-2")):
        return ModelCapability(model_id or "unknown", 64000, 12000, True)
    if any(marker in normalized for marker in ("ollama", "lmstudio", "local")):
        return ModelCapability(model_id or "unknown", 16000, 4096, False)
    return ModelCapability(model_id or "unknown", 16000, 4096, False)


def select_generation_strategy(
    *,
    prompt_text: str,
    model_id: str | None,
    task_type: str,
    expected_file_count: int,
    previous_failure_reason: str | None = None,
) -> GenerationStrategyDecision:
    del task_type
    estimated_prompt_tokens = estimate_tokens(prompt_text)
    expected_output_tokens = max(900, expected_file_count * 1400)
    model = estimate_model_capability(model_id)
    normalized = prompt_text.casefold()
    reasons: list[str] = []

    if estimated_prompt_tokens > 700:
        reasons.append("prompt_exceeds_chunk_threshold")
    if any(term in normalized for term in (
        "advanced", "complete project", "dashboard", "readme", "multiple files",
        "webserver", "web server", "browser", "http server", "rest api", "freertos",
    )):
        reasons.append("advanced_or_multifile_prompt")
    if expected_file_count > 2:
        reasons.append("expected_multiple_files")
    if expected_file_count > 2 and not model.supports_large_generation and expected_output_tokens > model.max_output_estimate:
        reasons.append("model_output_budget_limited")
    if previous_failure_reason and any(term in previous_failure_reason.casefold() for term in ("missing", "truncated", "too minimal")):
        reasons.append("previous_generation_failure")

    strategy = "chunked" if reasons else "one_shot"
    return GenerationStrategyDecision(
        strategy=strategy,
        estimated_prompt_tokens=estimated_prompt_tokens,
        expected_file_count=expected_file_count,
        expected_output_tokens=expected_output_tokens,
        model=model,
        reasons=tuple(reasons),
    )
