"""Disabled-by-default OpenAI Responses API planner adapter spike."""

from __future__ import annotations

import json
import os
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any
from pathlib import Path, PureWindowsPath

from .provider_contracts import ApiProviderClassification, ApiProviderError
from .tool_contracts import ToolName, ToolPlan
from .tool_policy import API_SMOKE_CONTENT, API_SMOKE_PATH


OPENAI_API_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
SPIKE_FLAG = "FORGEX_ENABLE_API_PROVIDER_SPIKE"
PROVIDER_FLAG = "FORGEX_ENABLE_OPENAI_API_PROVIDER"
API_KEY_ENV = "OPENAI_API_KEY"
MODEL_ENV = "FORGEX_OPENAI_MODEL"
MAX_RESPONSE_BYTES = 64 * 1024
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
Transport = Callable[[str, Mapping[str, object], str, float], Mapping[str, object]]


class OpenAIApiProvider:
    provider_id = "openai_api"
    supports_structured_tool_calls = True
    supports_streaming = False
    supports_json_schema = True
    max_input_tokens = 4096
    max_output_tokens = 512
    production_eligible = False

    def __init__(
        self,
        *,
        confirm_real_api: bool = False,
        env: Mapping[str, str] | None = None,
        transport: Transport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._env = env if env is not None else os.environ
        self._confirm_real_api = bool(confirm_real_api)
        self._transport = transport or _post_json
        self._timeout_seconds = max(1.0, min(float(timeout_seconds), 60.0))
        candidate = self._env.get(MODEL_ENV, DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
        if not _SAFE_ID.fullmatch(candidate):
            raise ApiProviderError(ApiProviderClassification.DISABLED, "model_id_invalid")
        self.model_id = candidate
        self._last_metadata: dict[str, object] = {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "run_id": None,
            "tool_count": 0,
            "classification": ApiProviderClassification.DISABLED.value,
            "safe_error_code": None,
            "outbound_request_count": 0,
        }

    @property
    def enabled(self) -> bool:
        return (
            self._confirm_real_api
            and self._env.get(SPIKE_FLAG, "").strip() == "1"
            and self._env.get(PROVIDER_FLAG, "").strip() == "1"
        )

    def request_plan(
        self,
        *,
        task: str,
        sandbox_manifest: Mapping[str, object],
        allowed_tools: list[str],
        policy: Mapping[str, object],
        run_id: str,
    ) -> ToolPlan:
        del sandbox_manifest, policy
        self._start_metadata(run_id)
        if not self.enabled:
            self._fail(ApiProviderClassification.DISABLED, "explicit_api_gates_required")
        api_key = self._env.get(API_KEY_ENV, "")
        if not api_key.strip():
            self._fail(ApiProviderClassification.KEY_MISSING, "api_key_missing")
        if not _SAFE_ID.fullmatch(run_id):
            self._fail(ApiProviderClassification.DISABLED, "run_id_invalid")
        if allowed_tools != [
            ToolName.LIST_FILES.value,
            ToolName.READ_FILE.value,
            ToolName.WRITE_FILE.value,
            ToolName.EDIT_FILE_SIMPLE.value,
        ]:
            self._fail(ApiProviderClassification.POLICY_DENIED, "allowed_tools_invalid")
        if not isinstance(task, str) or not task.strip():
            self._fail(ApiProviderClassification.SCHEMA_INVALID, "task_invalid")

        payload = {
            "model": self.model_id,
            "input": [
                {
                    "role": "system",
                    "content": [{"type": "input_text", "text": _planner_prompt(task)}],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "forgex_api_tool_plan",
                    "strict": True,
                    "schema": ToolPlan.api_smoke_json_schema(
                        expected_path=API_SMOKE_PATH,
                        expected_content=API_SMOKE_CONTENT,
                    ),
                }
            },
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        try:
            self._last_metadata["outbound_request_count"] = 1
            response = self._transport(OPENAI_API_URL, payload, api_key, self._timeout_seconds)
        except ApiProviderError as exc:
            self._fail(exc.classification, exc.safe_code)
        except (TimeoutError, socket.timeout):
            self._fail(ApiProviderClassification.TIMEOUT, "provider_timeout")
        except Exception:
            self._fail(ApiProviderClassification.MODEL_ERROR, "provider_request_failed")

        try:
            output_text = _extract_output_text(response)
        except ApiProviderError as exc:
            self._fail(exc.classification, exc.safe_code)
        try:
            decoded = json.loads(output_text)
        except json.JSONDecodeError:
            self._fail(ApiProviderClassification.INVALID_JSON, "model_output_invalid_json")
        try:
            _validate_payload_authority(decoded)
            plan = ToolPlan.parse_api_payload(decoded, max_calls=1)
            _validate_exact_smoke_plan(plan)
        except ApiProviderError as exc:
            self._fail(exc.classification, exc.safe_code)
        except (KeyError, TypeError, ValueError):
            self._fail(ApiProviderClassification.SCHEMA_INVALID, "model_output_schema_invalid")
        self._last_metadata.update(
            tool_count=len(plan.calls),
            classification=ApiProviderClassification.PASS.value,
            safe_error_code=None,
        )
        return plan

    def to_safe_metadata(self) -> dict[str, object]:
        return dict(self._last_metadata)

    def _start_metadata(self, run_id: str) -> None:
        self._last_metadata = {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "run_id": run_id if _SAFE_ID.fullmatch(run_id) else None,
            "tool_count": 0,
            "classification": ApiProviderClassification.DISABLED.value,
            "safe_error_code": None,
            "outbound_request_count": 0,
        }

    def _fail(self, classification: ApiProviderClassification, safe_code: str) -> None:
        self._last_metadata.update(classification=classification.value, safe_error_code=safe_code, tool_count=0)
        raise ApiProviderError(classification, safe_code)


def _planner_prompt(task: str) -> str:
    return (
        "You are a planner only. Return only valid JSON with no markdown, explanations, or code fences. "
        "Return exactly one write_file tool call. Use path FORGEX_API_PROVIDER_SMOKE.txt and content "
        "ForgeX real API provider smoke completed followed by one newline. Do not request shell, network, "
        "dependency installation, build, flash, patch apply, or any other tool. The task is: "
        + task.strip()
    )


def _extract_output_text(response: Mapping[str, object]) -> str:
    output = response.get("output")
    if not isinstance(output, list):
        raise ApiProviderError(ApiProviderClassification.MODEL_ERROR, "provider_response_shape_invalid")
    texts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, Mapping) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif isinstance(part, Mapping) and part.get("type") == "refusal":
                raise ApiProviderError(ApiProviderClassification.MODEL_ERROR, "provider_refused_plan")
    if len(texts) != 1 or len(texts[0].encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ApiProviderError(ApiProviderClassification.MODEL_ERROR, "provider_output_missing_or_oversized")
    return texts[0]


def _validate_exact_smoke_plan(plan: ToolPlan) -> None:
    if len(plan.calls) != 1 or plan.calls[0].tool is not ToolName.WRITE_FILE:
        raise ValueError("smoke_tool_count_invalid")
    arguments = dict(plan.calls[0].arguments)
    if set(arguments) != {"path", "content"}:
        raise ValueError("smoke_arguments_invalid")
    path = arguments["path"]
    content = arguments["content"]
    if not isinstance(path, str) or _unsafe_relative_path(path):
        raise ApiProviderError(ApiProviderClassification.PATH_UNSAFE, "model_output_path_unsafe")
    if path != API_SMOKE_PATH or content != API_SMOKE_CONTENT:
        raise ApiProviderError(ApiProviderClassification.CONTENT_INVALID, "model_output_exact_content_invalid")


def _validate_payload_authority(decoded: object) -> None:
    if not isinstance(decoded, Mapping):
        return
    calls = decoded.get("tool_calls")
    if not isinstance(calls, list):
        return
    forbidden = {"shell", "network", "install_dependencies", "apply_patch", "build", "flash"}
    for call in calls:
        if isinstance(call, Mapping) and call.get("tool") in forbidden:
            raise ApiProviderError(ApiProviderClassification.POLICY_DENIED, "model_requested_forbidden_tool")


def _unsafe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    windows = PureWindowsPath(value)
    return (
        not normalized
        or normalized.startswith("/")
        or normalized == ".."
        or normalized.startswith("../")
        or "/../" in normalized
        or windows.drive != ""
        or windows.is_absolute()
        or Path(value).is_absolute()
    )


def _post_json(
    url: str,
    payload: Mapping[str, object],
    api_key: str,
    timeout_seconds: float,
) -> Mapping[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ApiProviderError(ApiProviderClassification.RATE_LIMITED, "provider_rate_limited") from exc
        if exc.code in {408, 504}:
            raise ApiProviderError(ApiProviderClassification.TIMEOUT, "provider_timeout") from exc
        raise ApiProviderError(ApiProviderClassification.MODEL_ERROR, "provider_http_error") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", None)
        classification = ApiProviderClassification.TIMEOUT if isinstance(reason, (TimeoutError, socket.timeout)) else ApiProviderClassification.MODEL_ERROR
        code = "provider_timeout" if classification is ApiProviderClassification.TIMEOUT else "provider_network_error"
        raise ApiProviderError(classification, code) from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise ApiProviderError(ApiProviderClassification.MODEL_ERROR, "provider_response_oversized")
    try:
        decoded: Any = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiProviderError(ApiProviderClassification.MODEL_ERROR, "provider_response_invalid") from exc
    if not isinstance(decoded, Mapping):
        raise ApiProviderError(ApiProviderClassification.MODEL_ERROR, "provider_response_shape_invalid")
    return decoded
