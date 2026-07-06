"""Codex Agent adapter used exclusively by the code-generation route."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from ...bridges.codex_status import CodexStatusService, build_codex_safe_user_env
from ...provider_runtime import classify_cli_failure
from ..models import ModelInfo, ModelRequest, ModelResponse, ProviderHealth
from .base import BaseModelProvider, elapsed_ms


class CodexAgentProvider(BaseModelProvider):
    """Collect one structured generation response from the local Codex agent.

    The agent runs in a disposable, read-only directory. It can reason about the
    generation request but cannot edit the active project or execute commands.
    """

    provider_id = "codex"

    def __init__(
        self,
        registry,
        *,
        status_service: CodexStatusService | None = None,
    ) -> None:
        super().__init__(registry)
        self.status_service = status_service or CodexStatusService()

    async def health(self) -> ProviderHealth:
        started = time.monotonic()
        status = await asyncio.to_thread(self.status_service.status)
        if not status.oauth_bridge_ready:
            health = ProviderHealth(
                self.provider_id,
                False,
                "authentication_required",
                elapsed_ms(started),
                "CODEX_AUTH_REQUIRED",
                "Sign in with the Codex CLI before using Codex Agent generation.",
            )
            self._persist_health(health)
            return health
        health = ProviderHealth(self.provider_id, True, "connected", elapsed_ms(started))
        self._persist_health(health)
        return health

    async def list_models(self) -> list[ModelInfo]:
        return self.registry.list_models(self.provider_id)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if request.task_type != "code_generation":
            raise ValueError("Codex Agent is restricted to code generation.")

        started = time.monotonic()
        status = await asyncio.to_thread(self.status_service.status)
        if not status.oauth_bridge_ready:
            health = ProviderHealth(
                self.provider_id,
                False,
                "authentication_required",
                elapsed_ms(started),
                "CODEX_AUTH_REQUIRED",
                "Sign in with the Codex CLI before using Codex Agent generation.",
            )
            self._persist_health(health)
            raise RuntimeError("Codex CLI sign-in is required. Run `codex login`, then test Codex Agent in Models.")
        executable = self.status_service.resolve_executable(build_codex_safe_user_env())
        if executable is None:
            raise RuntimeError("Codex Agent executable is unavailable.")

        with tempfile.TemporaryDirectory(prefix="forgex-codex-generation-") as cwd:
            content = await asyncio.to_thread(
                self._run_exec,
                executable,
                request,
                Path(cwd).resolve(),
            )

        if not content.strip():
            raise ValueError("Codex Agent returned an empty generation response.")
        self._persist_health(ProviderHealth(self.provider_id, True, "connected", elapsed_ms(started)))
        return ModelResponse(
            content=content,
            provider_id=self.provider_id,
            model_id=request.model_id or self.registry.default_model(self.provider_id),
            latency_ms=elapsed_ms(started),
        )

    def _persist_health(self, health: ProviderHealth) -> None:
        try:
            self.registry.save_health(self.provider_id, health.to_dict())
        except OSError:
            # A read-only diagnostics location must not replace the actual
            # provider result with a storage error.
            pass

    def _run_exec(self, executable: Any, request: ModelRequest, cwd: Path) -> str:
        """Run Codex as a bounded one-shot process.

        The previous long-lived app-server transport could recurse inside the
        Windows subprocess/protocol stack. ``codex exec`` is the CLI's stable
        non-interactive surface and gives each generation a fresh process.
        """

        output_path = cwd / "last-message.txt"
        instruction = request.prompt
        if request.system_prompt:
            instruction = f"{request.system_prompt}\n\n{request.prompt}"

        command = [
            executable.path,
            *executable.prefix_args,
            "--ask-for-approval",
            "never",
            "exec",
            "--sandbox",
            "read-only",
            "--cd",
            str(cwd),
            "--skip-git-repo-check",
            "--output-last-message",
            str(output_path),
            "-",
        ]
        env = build_codex_safe_user_env()
        suffix = Path(executable.path).suffix.casefold()
        if suffix in {".cmd", ".bat"}:
            comspec = env.get("ComSpec") or env.get("COMSPEC") or "cmd.exe"
            command = [comspec, "/d", "/s", "/c", subprocess.list2cmdline(command)]
        elif suffix == ".ps1":
            command = ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", *command]

        try:
            result = subprocess.run(
                command,
                cwd=str(cwd),
                env=env,
                input=instruction,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                shell=False,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Codex Agent generation timed out after 180 seconds.") from exc
        except OSError as exc:
            raise RuntimeError("Codex Agent could not be started.") from exc

        if result.returncode != 0:
            code = classify_cli_failure(result.stderr or result.stdout or "", returncode=result.returncode)
            raise RuntimeError(code.value)
        try:
            size = output_path.stat().st_size
            if size > 4 * 1024 * 1024:
                raise RuntimeError("Codex Agent returned an oversized generation response.")
            return output_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError as exc:
            raise RuntimeError("Codex Agent did not return a generation response.") from exc

    async def _run_protocol(
        self,
        process: asyncio.subprocess.Process,
        request: ModelRequest,
        *,
        cwd: Path,
    ) -> str:
        await self._send(process, {
            "method": "initialize",
            "id": 1,
            "params": {"clientInfo": {"name": "forgex-generation", "title": "ForgeX Generation", "version": "0.1.0"}},
        })
        await self._wait_for_response(process, 1)
        await self._send(process, {"method": "initialized", "params": {}})
        await self._send(process, {
            "method": "thread/start",
            "id": 2,
            "params": {
                "cwd": str(cwd.resolve()),
                "approvalPolicy": "never",
                "sandbox": "read-only",
                "serviceName": "forgex-generation",
            },
        })
        response = await self._wait_for_response(process, 2)
        thread_id = response.get("result", {}).get("thread", {}).get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("Codex Agent thread did not start.")

        instruction = request.prompt
        if request.system_prompt:
            instruction = f"{request.system_prompt}\n\n{request.prompt}"
        await self._send(process, {
            "method": "turn/start",
            "id": 3,
            "params": {"threadId": thread_id, "input": [{"type": "text", "text": instruction}]},
        })

        message_parts: dict[str, str] = {}
        final_ids: list[str] = []
        while True:
            message = await self._read(process)
            method = message.get("method")
            if "id" in message and method:
                await self._send(process, {"id": message["id"], "result": {"decision": "decline"}})
                continue
            params = message.get("params", {})
            if method == "item/agentMessage/delta" and isinstance(params, dict):
                item_id, delta = params.get("itemId"), params.get("delta")
                if isinstance(item_id, str) and isinstance(delta, str):
                    message_parts[item_id] = message_parts.get(item_id, "") + delta
            elif method == "item/completed" and isinstance(params, dict):
                item = params.get("item", {})
                if isinstance(item, dict) and item.get("type") == "agentMessage":
                    item_id, text = item.get("id"), item.get("text")
                    if isinstance(item_id, str) and isinstance(text, str):
                        message_parts[item_id] = text
                        if item.get("phase") == "final_answer" or not final_ids:
                            final_ids.append(item_id)
            if method == "turn/completed":
                if final_ids:
                    return message_parts.get(final_ids[-1], "")
                return next((text for text in reversed(tuple(message_parts.values())) if text), "")
            if method == "turn/failed" or (message.get("id") == 3 and "error" in message):
                raise RuntimeError("Codex Agent generation failed.")

    async def _wait_for_response(self, process: asyncio.subprocess.Process, request_id: int) -> dict[str, Any]:
        while True:
            message = await self._read(process)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError("Codex Agent request failed.")
                return message

    @staticmethod
    async def _send(process: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
        if process.stdin is None:
            raise RuntimeError("Codex Agent stdin is unavailable.")
        process.stdin.write((json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8"))
        await process.stdin.drain()

    @staticmethod
    async def _read(process: asyncio.subprocess.Process) -> dict[str, Any]:
        if process.stdout is None:
            raise RuntimeError("Codex Agent stdout is unavailable.")
        raw = await process.stdout.readline()
        if not raw or len(raw) > 4 * 1024 * 1024:
            raise RuntimeError("Codex Agent returned an invalid message.")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise RuntimeError("Codex Agent returned an invalid message.")
        return value

    @staticmethod
    async def _drain(stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while await stream.read(8192):
            pass

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
