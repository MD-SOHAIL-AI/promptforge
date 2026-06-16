"""FastAPI application composition for PromptForge AI."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from ..core.config import (
    llm_max_attempts,
    llm_retry_backoff_seconds,
    llm_timeout_seconds,
    load_environment,
)
from ..main import execute_prompt
from ..observability.metrics import MetricsRegistry
from ..observability.runtime_logs import RuntimeLogger
from ..runtime.subprocess_mgr import SubprocessManager
from ..services.code_generation_service import CodeGenerationService
from ..services.llm_service import (
    AnthropicService,
    GeminiService,
    LLMService,
    OpenAIService,
    OpenRouterService,
)
from ..services.platformio_service import PlatformIOService
from ..services.project_service import ProjectService
from ..services.serial_service import SerialConfiguration, SerialService
from ..tools.board_detector import BoardDetector
from ..tools.tool_registry import execute_tool
from ..utils.paths import PathManager
from ..workspace.workspace_manager import WorkspaceManager
from .errors import install_error_handlers
from .observability import configure_api_logging, request_context_middleware
from .progress import ExecutionProgressHub
from .routes import build, execute, files, flash, health, monitor, projects, websocket
from .routes import workspace as workspace_routes


logger = logging.getLogger(__name__)


def create_app(
    *,
    code_generation_service: CodeGenerationService | None = None,
    project_service: ProjectService | None = None,
    subprocess_manager: SubprocessManager | None = None,
    platformio_service: PlatformIOService | None = None,
    llm_service: LLMService | None = None,
    serial_service: SerialService | None = None,
    serial_service_factory: Callable[[SerialConfiguration], SerialService] = SerialService,
    board_detector: BoardDetector | None = None,
    tool_executor: Callable[..., Any] = execute_tool,
    execute_prompt_fn: Callable[..., Any] = execute_prompt,
    progress_hub: ExecutionProgressHub | None = None,
    workspace_manager: WorkspaceManager | None = None,
    metrics_registry: MetricsRegistry | None = None,
    runtime_logger: RuntimeLogger | None = None,
    version: str | None = None,
) -> FastAPI:
    load_environment()
    configure_api_logging()
    manager = subprocess_manager or SubprocessManager()
    paths = PathManager(os.getenv("PROMPTFORGE_ROOT") or None)
    runtime_logs = runtime_logger or RuntimeLogger()
    metrics = metrics_registry or MetricsRegistry()
    workspace = workspace_manager or WorkspaceManager(
        paths,
        runtime_logger=runtime_logs,
    )
    platformio = platformio_service or PlatformIOService(manager)
    owned_llm = llm_service
    generation = code_generation_service
    configuration_error: str | None = None
    if generation is None:
        try:
            timeout_s = (
                owned_llm.timeout_s
                if owned_llm is not None and hasattr(owned_llm, "timeout_s")
                else llm_timeout_seconds()
            )
            owned_llm = owned_llm or _llm_from_environment(timeout_s=timeout_s)
            generation = CodeGenerationService(
                owned_llm,
                timeout_s=timeout_s,
                max_attempts=llm_max_attempts(),
                retry_backoff_s=llm_retry_backoff_seconds(),
            )
        except ValueError as exc:
            configuration_error = str(exc)
    projects_root = Path(
        os.getenv(
            "PROMPTFORGE_PROJECTS_ROOT",
            str(paths.workspace_projects_directory()),
        )
    )
    projects_service = project_service or ProjectService(
        projects_root,
        code_generation_service=generation,
        platformio_service=platformio,
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        yield
        current_serial = getattr(application.state, "serial_service", None)
        if current_serial is not None:
            try:
                await current_serial.disconnect()
            except Exception:
                pass
        await manager.kill_all()
        if owned_llm is not None:
            close = getattr(owned_llm, "aclose", None)
            if callable(close):
                await close()

    application = FastAPI(
        title="PromptForge AI API",
        description="API integration for PromptForge embedded engineering workflows.",
        version=version or os.getenv("PROMPTFORGE_VERSION", "0.1.0"),
        lifespan=lifespan,
    )
    if configuration_error is not None:
        # fix: warn at startup when LLM configuration is unavailable.
        logger.warning(
            "PromptForge configuration error: %s — LLM features will be unavailable",
            configuration_error,
        )
    application.state.execute_prompt = execute_prompt_fn
    application.state.code_generation_service = generation
    application.state.project_service = projects_service
    application.state.subprocess_manager = manager
    application.state.platformio_service = platformio
    application.state.llm_service = owned_llm
    application.state.serial_service = serial_service
    application.state.serial_runtime = serial_service
    application.state.serial_service_factory = serial_service_factory
    application.state.monitor_lock = asyncio.Lock()
    application.state.board_detector = board_detector or BoardDetector()
    application.state.tool_executor = tool_executor
    application.state.progress_hub = progress_hub or ExecutionProgressHub()
    application.state.workspace_manager = workspace
    application.state.metrics_registry = metrics
    application.state.runtime_logger = runtime_logs
    application.state.configuration_error = configuration_error

    application.middleware("http")(request_context_middleware)
    install_error_handlers(application)
    application.include_router(health.router)
    application.include_router(execute.router)
    application.include_router(projects.router)
    application.include_router(files.router)
    application.include_router(build.router)
    application.include_router(flash.router)
    application.include_router(monitor.router)
    application.include_router(workspace_routes.router)
    application.include_router(websocket.router)
    return application


def _llm_from_environment(*, timeout_s: float | None = None) -> LLMService:
    provider = os.getenv("PROMPTFORGE_LLM_PROVIDER", "").strip().upper()
    if not provider:
        raise ValueError("PROMPTFORGE_LLM_PROVIDER is required")
    configurations: dict[str, tuple[type[LLMService], str]] = {
        "OPENAI": (OpenAIService, "OPENAI_API_KEY"),
        "OPENROUTER": (OpenRouterService, "OPENROUTER_API_KEY"),
        "ANTHROPIC": (AnthropicService, "ANTHROPIC_API_KEY"),
        "GEMINI": (GeminiService, "GEMINI_API_KEY"),
    }
    try:
        service_type, key_name = configurations[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported PROMPTFORGE_LLM_PROVIDER: {provider}") from exc
    api_key = os.getenv(key_name, "").strip()
    if not api_key:
        raise ValueError(f"{key_name} is required")
    model = os.getenv("PROMPTFORGE_MODEL", "").strip()
    if not model:
        raise ValueError("PROMPTFORGE_MODEL is required")
    resolved_timeout_s = llm_timeout_seconds() if timeout_s is None else timeout_s
    return service_type(
        api_key=api_key,
        model=model,
        timeout_s=resolved_timeout_s,
    )


app = create_app()
