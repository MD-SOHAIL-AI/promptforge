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
    available = max_chars - min(4096, max_chars // 4)
    for observation in reversed(observations):
        candidate = _plain(observation)
        rendered = encode(candidate)
        if len(rendered) > available // 2:
            candidate = {
                key: _plain(observation[key])
                for key in ("call_id", "tool", "status", "error") if key in observation
            }
            candidate["output_truncated"] = True
            output = observation.get("output")
            if isinstance(output, Mapping):
                candidate["evidence"] = {
                    key: _plain(output[key])
                    for key in ("path", "success", "exit_code", "next_offset", "total_lines", "artifact_id")
                    if key in output
                }
            candidate["output_preview"] = encode(output)[:max(128, available // 4)]
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
