#!/usr/bin/env python3
"""Make one real Forge Agent planner request without exposing credentials.

Examples:
  python scripts/forge_agent_provider_smoke.py --provider openrouter
  python scripts/forge_agent_provider_smoke.py --provider openai --model gpt-4.1-mini

The script reads the normal provider environment variables, makes an actual
OpenAI-compatible request through ForgeX's production ApiPlannerProvider, and
prints only safe diagnostics plus the validated model summary. It never prints
the API key or raw provider payload.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent_runtime.api_planner_provider import API_PLANNER_CONFIGS, ApiPlannerError, ApiPlannerProvider
from backend.agent_runtime.tool_contracts import ToolName


PROVIDER_ENV = {
    "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_MODEL", "openai/gpt-oss-20b:free"),
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4o-mini"),
    "groq": ("GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "gemini-1.5-flash"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a real ForgeX provider API request and safe streaming ToolPlan parse.")
    parser.add_argument("--provider", choices=tuple(PROVIDER_ENV), default="openrouter")
    parser.add_argument("--model", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)


    key_env, model_env, default_model = PROVIDER_ENV[args.provider]
    api_key = os.getenv(key_env, "").strip()
    model = (args.model or os.getenv(model_env, "") or default_model).strip()
    if not api_key:
        print(f"SMOKE_SKIPPED: set {key_env} first")
        return 2

    extra_headers = None
    if args.provider == "openrouter":
        extra_headers = {"HTTP-Referer": "https://forgex.local", "X-Title": "ForgeX provider smoke"}

    planner = ApiPlannerProvider(
        API_PLANNER_CONFIGS[args.provider],
        api_key=api_key,
        model_id=model,
        timeout_seconds=args.timeout,
        extra_headers=extra_headers,
    )
    summaries: list[str] = []
    try:
        plan = planner.request_turn_stream(
            task=(
                "Connectivity smoke test. Do not modify anything. Return a short summary confirming the "
                "Forge Agent planner can respond; use no tools and finish this turn."
            ),
            tool_results=(),
            allowed_tools=(ToolName.READ_FILE.value,),
            run_id="provider-smoke",
            turn=1,
            on_summary=lambda value: summaries.append(value),
        )
    except ApiPlannerError as exc:
        print(f"SMOKE_FAILED: {exc.classification} ({exc.safe_code})")
        print(planner.to_safe_metadata())
        return 1
    except Exception as exc:  # pragma: no cover - network/platform dependent smoke helper
        print(f"SMOKE_FAILED: {type(exc).__name__}: {str(exc)[:240]}")
        print(planner.to_safe_metadata())
        return 1

    metadata = planner.to_safe_metadata()
    print("SMOKE_PASS")
    print(f"provider={args.provider} model={model}")
    print(f"summary={plan.summary[:500]}")
    print(
        "diagnostics="
        f"outbound={metadata.get('outbound_request_count')} "
        f"reached_provider={metadata.get('request_reached_provider')} "
        f"http_status={metadata.get('http_status')} "
        f"streaming={metadata.get('streaming_used')} "
        f"stream_deltas={metadata.get('stream_delta_count')}"
    )
    if summaries:
        print(f"safe_summary_updates={len(summaries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
