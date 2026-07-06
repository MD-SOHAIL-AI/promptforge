"""Deterministic in-process provider used to prove the ForgeX runtime."""

from __future__ import annotations

from typing import Mapping

from .tool_contracts import ToolCall, ToolName, ToolPlan
from .tool_policy import SMOKE_CONTENT, SMOKE_PATH


class FakeApiProvider:
    provider_id = "fake_api_provider"
    model_id = "fake-structured-tool-model-v1"
    supports_structured_tool_calls = True
    supports_streaming = False
    supports_json_schema = True
    max_input_tokens = 4096
    max_output_tokens = 1024
    production_eligible = False

    def request_plan(
        self,
        *,
        task: str,
        sandbox_manifest: Mapping[str, object],
        allowed_tools: list[str],
        policy: Mapping[str, object],
        run_id: str,
    ) -> ToolPlan:
        del task, sandbox_manifest, policy, run_id
        if ToolName.WRITE_FILE.value not in allowed_tools:
            raise PermissionError("fake_provider_write_not_allowed")
        return ToolPlan(
            (
                ToolCall(
                    tool=ToolName.WRITE_FILE,
                    arguments={"path": SMOKE_PATH, "content": SMOKE_CONTENT},
                ),
            )
        )
