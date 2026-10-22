"""Bound model observations without cutting JSON or dropping recent failures."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(item) for item in value]
    return value


def _bounded_compiler_focus(candidate, *, max_chars, encode):
    """Shorten source previews before dropping the errors they explain.

    Alphabetically sliced JSON can contain only source previews and lose all
    diagnostics and repair instructions. Keep the host's structured evidence
    and freshness markers; shortened excerpts still require a complete read.
    The candidate is already a detached plain value, never durable evidence.
    """
    if candidate.get("tool") not in {"compiler_repair_focus", "linker_repair_focus"} or not isinstance(candidate.get("output"), dict):
        return candidate
    output = candidate["output"]
    sources = output.get("current_sources", [])
    diagnostics = output.get("diagnostics", [])
    if not isinstance(sources, list) or not isinstance(diagnostics, list):
        return candidate
    while len(encode(candidate)) > max_chars:
        previews = [item for item in sources if isinstance(item, dict)
            and isinstance(item.get("numbered_preview"), str) and item["numbered_preview"]]
        if previews:
            source = max(previews, key=lambda item: len(item["numbered_preview"]))
            preview = source["numbered_preview"]
            shortened = preview[:len(preview) // 2]
            source["numbered_preview"] = shortened.rsplit("\n", 1)[0] if "\n" in shortened else ""
            source["preview_truncated"] = True
            continue
        if len(sources) > 1:
            sources.pop()
            output["omitted_source_count"] = output.get("omitted_source_count", 0) + 1
            continue
        if len(diagnostics) > 1:
            diagnostics.pop()
            output["omitted_diagnostic_count"] = output.get("omitted_diagnostic_count", 0) + 1
            continue
        break
    return candidate


def render_observations(observations: Sequence[Mapping[str, object]], *, max_chars: int = 48_000) -> str:
    """Keep complete recent records and a factual index of omitted evidence.

    This is deterministic projection, not semantic model compaction. Omitted
    content is explicitly marked so the model knows when to inspect again.
    """
    if max_chars < 1024:
        raise ValueError("observation_budget_too_small")
    encode = lambda value: json.dumps(_plain(value), ensure_ascii=True, sort_keys=True)
    selected: list[object] = []
    used = 2
    omitted = 0
    # Reserve room for the omission record and use newest evidence first.
    available = max_chars - min(2048, max_chars // 8)
    for observation in reversed(observations):
        remaining = available - used - 2
        if remaining < 256:
            omitted += 1
            continue
        candidate = _plain(observation)
        rendered = encode(candidate)
        if len(rendered) > remaining:
            candidate = _bounded_compiler_focus(candidate, max_chars=remaining - 4, encode=encode)
            if candidate.get("tool") == "source_contract_focus" and isinstance(candidate.get("output"), dict):
                from .source_contracts import shrink_source_contracts
                shrink_source_contracts(candidate["output"], max(0, remaining - 140))
            rendered = encode(candidate)
        if len(rendered) > remaining:
            candidate = {
                key: _plain(observation[key])
                for key in ("call_id", "tool", "status", "error") if key in observation
            }
            candidate["output_truncated"] = True
            output = observation.get("output")
            if isinstance(output, Mapping):
                candidate["evidence"] = {
                    key: _plain(output[key])
                    for key in ("path", "success", "exit_code", "offset", "end_line", "next_offset", "total_lines", "content_sha256", "truncated_lines", "citation_ranges", "artifact_id")
                    if key in output
                }
            candidate["output_preview"] = encode(output)[:max(128, remaining // 4)]
            rendered = encode(candidate)
        if used + len(rendered) + 2 > available:
            omitted += 1
            continue
        selected.insert(0, candidate)
        used += len(rendered) + 2
    if omitted:
        selected.insert(0, {
            "tool": "context_checkpoint",
            "omitted_observations": omitted,
            "instruction": "Older evidence was omitted. Re-read affected files before relying on their contents.",
        })
    result = encode(selected)
    if len(result) > max_chars:
        raise ValueError("observation_budget_exceeded")
    return result
