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
from fastapi.responses import JSONResponse

from ..bridges import (
    AGYBridgeProvider,
    AGYScratchProjectImportService,
    AGYAssistedRunner,
    AntigravitySandboxRunner,
    BridgeDetectionService,
    CodexLoginService,
    CodexOAuthSmokeService,
    BridgeDiffService,
    BridgePatchExportService,
    PatchApplyService,
    PatchPreflightService,
    BridgeReviewStore,
    BridgeSandboxService,
    RollbackRestoreApplyService,
    RollbackRestorePreflightService,
    RollbackSnapshotService,
)
from ..bridges.codex_status import CodexStatusService
from ..bridges.generic import (
    BridgeArtifactValidator,
    BridgeProviderRegistry,
    BridgeRunStore,
    DefaultDenyBridgeRoutingPolicy,
    ExistingBridgeSandboxLifecycle,
    GenericBridgeRunCoordinator,
    InternalBridgeEventTransport,
    ProviderCapabilityRegistry,
    default_provider_capability_registry,
)
from ..bridges.generic.errors import BridgeDomainError
from ..bridges.generic.provider_capabilities import (
    CapabilityStatus,
    ProviderCapabilityEvidence,
    ProviderKind,
    ProviderOperationalState,
    TransportMode,
)
from ..bridges.providers.agy_sdk import AGYSDKProvider
from ..bridges.providers.codex_app_server import CodexAppServerProvider
from ..bridges.agy_execution_router import AGYCutoverFlags, AGYExecutionRouter
from ..bridges.agy_trusted_workspace import AGYTrustedWorkspaceService
from ..bridges.generic.api_policy import GenericRunFeatureFlags
from ..bridges.audit_log import BridgeAuditLog
from ..agent_runtime.product_agent_service import ProductAgentService
from ..agent_runtime.product_provider_registry import ProductProviderRegistry
from ..agent_runtime.unified_agent_service import UnifiedAgentService
from ..agent_runtime.approval_broker import AgentApprovalBroker
from ..core.config import (
    llm_max_attempts,
    llm_retry_backoff_seconds,
    llm_timeout_seconds,
    load_environment,
)
from ..main import execute_prompt
from ..model_router import ModelRouterService, ProviderRegistry, ProviderSettingsStorage, UsageTracker
from ..observability.metrics import MetricsRegistry
from ..observability.runtime_logs import RuntimeLogger
from ..runtime.subprocess_mgr import SubprocessManager
from ..settings import SettingsService
from ..provider_runtime import ProviderRunSummaryStore
from ..services.code_generation_service import CodeGenerationService
from ..services.generation_diagnostics_store import GenerationDiagnosticsStore
from ..services.llm_service import (
    AnthropicService,
    GeminiService,
    LLMService,
    OpenAIService,
    OpenRouterService,
)
from ..services.platformio_service import PlatformIOService
from ..services.project_import_service import ProjectImportService
from ..services.project_service import ProjectService
from ..services.serial_service import SerialConfiguration, SerialService
from ..services.terminal_service import TerminalService
from ..tools.board_detector import BoardDetector
from ..tools.tool_registry import execute_tool
from ..utils.paths import PathManager
from ..workspace.workspace_manager import WorkspaceManager
from .errors import install_error_handlers
from .observability import configure_api_logging, request_context_middleware
from .progress import ExecutionProgressHub
from .routes import agent_runtime, build, devices, execute, files, flash, generic_runs, health, models, monitor, projects, settings, terminal, websocket
from .routes import workspace as workspace_routes


logger = logging.getLogger(__name__)


def _provider_strategy_for_runtime(
    *,
    agy_sdk_enabled: bool,
    codex_enabled: bool,
) -> ProviderCapabilityRegistry:
    records = list(default_provider_capability_registry().list())

    def replace(record: ProviderCapabilityEvidence) -> None:
        nonlocal records
        records = [item for item in records if item.provider_id != record.provider_id]
        records.append(record)

    if agy_sdk_enabled:
        replace(
            ProviderCapabilityEvidence(
                provider_id="agy",
                display_name="Google Antigravity SDK",
                provider_kind=ProviderKind.LOCAL_SERVER,
                transport_modes=(TransportMode.SDK,),
                headless_mode_available=True,
                permission_status=CapabilityStatus.PASSED,
                workspace_write_status=CapabilityStatus.PASSED,
                server_api_status=CapabilityStatus.PASSED,
                operational_state=ProviderOperationalState.EXPERIMENTAL,
                execution_allowed=True,
                routing_allowed=True,
                production_eligible=False,
                evidence_source="explicit_experimental_sdk_flag",
                safe_failure_reason="live_positive_artifact_validation_required",
            )
        )
    if codex_enabled:
        replace(
            ProviderCapabilityEvidence(
                provider_id="codex",
                display_name="OpenAI Codex App Server",
                provider_kind=ProviderKind.LOCAL_SERVER,
                transport_modes=(TransportMode.APP_SERVER,),
                headless_mode_available=True,
                permission_status=CapabilityStatus.PASSED,
                workspace_write_status=CapabilityStatus.PASSED,
                server_api_status=CapabilityStatus.PASSED,
                operational_state=ProviderOperationalState.EXPERIMENTAL,
                execution_allowed=True,
                routing_allowed=True,
                production_eligible=False,
                evidence_source="explicit_experimental_app_server_flag",
                safe_failure_reason="live_positive_artifact_validation_required",
            )
        )
    return ProviderCapabilityRegistry(records)


def create_app(
    *,
    code_generation_service: CodeGenerationService | None = None,
    project_service: ProjectService | None = None,
    subprocess_manager: SubprocessManager | None = None,
    platformio_service: PlatformIOService | None = None,
    llm_service: LLMService | None = None,
    model_router_service: ModelRouterService | None = None,
    serial_service: SerialService | None = None,
    serial_service_factory: Callable[[SerialConfiguration], SerialService] = SerialService,
    board_detector: BoardDetector | None = None,
    tool_executor: Callable[..., Any] = execute_tool,
    execute_prompt_fn: Callable[..., Any] = execute_prompt,
    progress_hub: ExecutionProgressHub | None = None,
    workspace_manager: WorkspaceManager | None = None,
    project_import_service: ProjectImportService | None = None,
    metrics_registry: MetricsRegistry | None = None,
    runtime_logger: RuntimeLogger | None = None,
    settings_service: SettingsService | None = None,
    terminal_service: TerminalService | None = None,
    bridge_detection_service: BridgeDetectionService | None = None,
    codex_login_service: CodexLoginService | None = None,
    codex_oauth_smoke_service: CodexOAuthSmokeService | None = None,
    bridge_diff_service: BridgeDiffService | None = None,
    bridge_patch_export_service: BridgePatchExportService | None = None,
    bridge_patch_apply_service: PatchApplyService | None = None,
    bridge_patch_preflight_service: PatchPreflightService | None = None,
    bridge_rollback_snapshot_service: RollbackSnapshotService | None = None,
    bridge_rollback_restore_preflight_service: RollbackRestorePreflightService | None = None,
    bridge_rollback_restore_apply_service: RollbackRestoreApplyService | None = None,
    bridge_agy_runner: AntigravitySandboxRunner | None = None,
    generic_bridge_registry: BridgeProviderRegistry | None = None,
    agy_generic_provider: AGYBridgeProvider | None = None,
    provider_capability_registry: ProviderCapabilityRegistry | None = None,
    agy_scratch_import_service: AGYScratchProjectImportService | None = None,
    agy_assisted_runner: AGYAssistedRunner | None = None,
    version: str | None = None,
) -> FastAPI:
    load_environment()
    configure_api_logging()
    manager = subprocess_manager or SubprocessManager()
    paths = PathManager(os.getenv("PROMPTFORGE_ROOT") or None)
    runtime_logs = runtime_logger or RuntimeLogger()
    metrics = metrics_registry or MetricsRegistry()
    app_settings = settings_service or SettingsService()
    terminal_sessions = terminal_service or TerminalService()
    bridge_detection = bridge_detection_service or BridgeDetectionService()
    codex_status = CodexStatusService()
    codex_login = codex_login_service or CodexLoginService(status_service=codex_status)
    codex_oauth_smoke = codex_oauth_smoke_service or CodexOAuthSmokeService(
        login_service=codex_login,
        repository_root=paths.project_root(),
    )
    workspace = workspace_manager or WorkspaceManager(
        paths,
        runtime_logger=runtime_logs,
    )
    platformio = platformio_service or PlatformIOService(manager)
    router_service = model_router_service or ModelRouterService(
        ProviderRegistry(ProviderSettingsStorage()),
        UsageTracker(),
    )
    generation_diagnostics = GenerationDiagnosticsStore()
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
            owned_llm = owned_llm or router_service
            owned_llm.timeout_s = timeout_s
            generation = CodeGenerationService(
                owned_llm,
                timeout_s=timeout_s,
                max_attempts=llm_max_attempts(),
                retry_backoff_s=llm_retry_backoff_seconds(),
                diagnostics_store=generation_diagnostics,
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
    imports_service = project_import_service or ProjectImportService(
        workspace.workspace_root / "external-projects.json",
    )
    bridge_state_dir = paths.state_directory()
    bridge_audit_log = BridgeAuditLog(bridge_state_dir / "bridge-review-audit.jsonl")
    bridge_reviews = bridge_diff_service or BridgeDiffService(
        audit_log=bridge_audit_log,
        store=BridgeReviewStore(
            snapshots_path=bridge_state_dir / "bridge-snapshots.jsonl",
            reviews_path=bridge_state_dir / "bridge-reviews.jsonl",
        ),
    )
    bridge_sandboxes = BridgeSandboxService(bridge_state_dir / "bridge-sandboxes")
    agy_trusted_workspaces = AGYTrustedWorkspaceService(bridge_state_dir.parent / "agy-trusted-workspaces")
    agy_runner = bridge_agy_runner or AntigravitySandboxRunner(
        sandbox_service=bridge_sandboxes,
        review_service=bridge_reviews,
        audit_log=bridge_audit_log,
    )
    cutover_flags = AGYCutoverFlags.from_environment()
    generic_run_flags = GenericRunFeatureFlags.from_environment()
    cutover_flags = AGYCutoverFlags(
        legacy_enabled=agy_runner.is_enabled(),
        generic_routing_enabled=cutover_flags.generic_routing_enabled,
        generic_provider_enabled=cutover_flags.generic_provider_enabled,
        cutover_enabled=cutover_flags.cutover_enabled,
    )
    generic_cutover_enabled = cutover_flags.effective_mode().value == "generic"
    agy_adapter = agy_generic_provider or AGYBridgeProvider(
        detector=lambda: bridge_detection.detect_provider("antigravity_cli_bridge"),
        runner=agy_runner,
        artifact_root=bridge_state_dir,
        execution_enabled=generic_cutover_enabled,
        availability_override=generic_cutover_enabled,
    )
    agy_sdk_enabled = os.getenv("FORGEX_ENABLE_AGY_SDK_PROVIDER", "").strip() == "1"
    codex_provider_enabled = os.getenv("FORGEX_ENABLE_CODEX_PROVIDER", "").strip() == "1"
    agy_sdk_provider = AGYSDKProvider(
        review_service=bridge_reviews,
        managed_root=bridge_state_dir,
        execution_enabled=agy_sdk_enabled,
    )
    agent_approval_broker = AgentApprovalBroker()
    codex_provider = CodexAppServerProvider(
        status_service=codex_status,
        review_service=bridge_reviews,
        managed_root=bridge_state_dir,
        approval_broker=agent_approval_broker,
        execution_enabled=codex_provider_enabled,
    )
    bridge_provider_registry = generic_bridge_registry or BridgeProviderRegistry()
    registered_ids = {item.provider_id for item in bridge_provider_registry.list_registered()}
    selected_agy_provider = agy_sdk_provider if agy_sdk_enabled else agy_adapter
    if "agy" not in registered_ids:
        bridge_provider_registry.register(selected_agy_provider)
    if "codex" not in registered_ids:
        bridge_provider_registry.register(codex_provider)
    provider_strategy = provider_capability_registry or _provider_strategy_for_runtime(
        agy_sdk_enabled=agy_sdk_enabled,
        codex_enabled=codex_provider_enabled,
    )
    generic_run_store = BridgeRunStore(bridge_state_dir / "generic-bridge-runs.json")
    generic_event_transport = InternalBridgeEventTransport()
    generic_coordinator = GenericBridgeRunCoordinator(
        registry=bridge_provider_registry,
        routing_policy=DefaultDenyBridgeRoutingPolicy(
            global_enabled=(
                cutover_flags.generic_routing_enabled
                or agy_sdk_enabled
                or codex_provider_enabled
            ),
            provider_permissions={
                "agy": generic_cutover_enabled or agy_sdk_enabled,
                "codex": codex_provider_enabled,
            },
            capability_registry=provider_strategy,
            allow_experimental=agy_sdk_enabled or codex_provider_enabled,
        ),
        run_store=generic_run_store,
        event_transport=generic_event_transport,
        sandbox_lifecycle=ExistingBridgeSandboxLifecycle(bridge_sandboxes),
        artifact_validator=BridgeArtifactValidator(bridge_state_dir),
    )
    agy_execution_router = AGYExecutionRouter(
        flags=cutover_flags,
        legacy_runner=agy_runner,
        generic_coordinator=generic_coordinator,
        sandbox_service=bridge_sandboxes,
        review_service=bridge_reviews,
        audit_log=bridge_audit_log,
        trusted_workspace_service=agy_trusted_workspaces,
    )
    patch_exports = bridge_patch_export_service or BridgePatchExportService(
        review_service=bridge_reviews,
        patch_directory=bridge_state_dir / "bridge-patches",
        audit_log=bridge_audit_log,
    )
    patch_preflight = bridge_patch_preflight_service or PatchPreflightService(
        patch_store=patch_exports.patch_store,
        review_service=bridge_reviews,
        audit_log=bridge_audit_log,
    )
    rollback_snapshots = bridge_rollback_snapshot_service or RollbackSnapshotService(
        rollback_directory=patch_exports.patch_directory.parent / "patch-rollback",
        preflight_service=patch_preflight,
        audit_log=bridge_audit_log,
    )
    rollback_restore_preflight = bridge_rollback_restore_preflight_service or RollbackRestorePreflightService(
        rollback_service=rollback_snapshots,
        audit_log=bridge_audit_log,
    )
    rollback_restore_apply = bridge_rollback_restore_apply_service or RollbackRestoreApplyService(
        restore_directory=patch_exports.patch_directory.parent / "rollback-restores",
        rollback_service=rollback_snapshots,
        restore_preflight_service=rollback_restore_preflight,
        audit_log=bridge_audit_log,
    )
    patch_apply = bridge_patch_apply_service or PatchApplyService(
        apply_directory=patch_exports.patch_directory.parent / "patch-applies",
        patch_store=patch_exports.patch_store,
        review_service=bridge_reviews,
        preflight_service=patch_preflight,
        rollback_service=rollback_snapshots,
        restore_apply_service=rollback_restore_apply,
        audit_log=bridge_audit_log,
    )
    provider_run_summaries = ProviderRunSummaryStore(bridge_state_dir / "provider-run-summaries.json")
    product_agent_service = ProductAgentService(
        managed_sandbox_root=bridge_state_dir / "agent-runtime-sandboxes",
        review_service=bridge_reviews,
        provider_registry=ProductProviderRegistry(
            fake_enabled=os.getenv("FORGEX_ENABLE_AGENT_RUNTIME_FAKE_PROVIDER", "").strip() == "1",
            codex_status_service=codex_status,
            model_provider_registry=router_service.registry,
            confirm_real_api=True,
        ),
        enabled=os.getenv("FORGEX_ENABLE_AGENT_RUNTIME", "").strip() == "1",
        summary_store=provider_run_summaries,
    )
    unified_agent_service = UnifiedAgentService(
        product_service=product_agent_service,
        bridge_registry=bridge_provider_registry,
        bridge_coordinator=generic_coordinator,
        sandbox_service=bridge_sandboxes,
        approval_broker=agent_approval_broker,
        enabled=os.getenv("FORGEX_ENABLE_AGENT_RUNTIME", "").strip() == "1",
        summary_store=provider_run_summaries,
    )
    agy_project_import = agy_scratch_import_service or AGYScratchProjectImportService(
        repository_root=paths.project_root(),
        active_workspace_root=workspace.workspace_root,
        managed_sandbox_root=paths.project_root() / ".promptforge" / "agy-import-sandboxes",
        review_service=bridge_reviews,
        status_path=bridge_state_dir / "agy-scratch-project-import-status.json",
    )
    assisted_agy = agy_assisted_runner or AGYAssistedRunner(
        repository_root=paths.project_root(),
        active_workspace_root=workspace.workspace_root,
        import_service=agy_project_import,
        feature_enabled=(
            os.getenv("FORGEX_ENABLE_AGY_ASSISTED_RUNNER", "").strip() == "1"
            and os.getenv("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "").strip() == "1"
        ),
        status_path=bridge_state_dir / "agy-assisted-runner-status.json",
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.generic_run_store_status = "ready"
        try:
            await generic_coordinator.reconcile_incomplete_runs()
        except BridgeDomainError as exc:
            # Persisted generic-run metadata must not take the whole desktop
            # backend offline. Routes that need the corrupt store still fail
            # closed with a sanitized API error.
            application.state.generic_run_store_status = "unavailable"
            logger.warning("generic_run_reconciliation_skipped code=%s", exc.code.value)
        yield
        current_serial = getattr(application.state, "serial_service", None)
        if current_serial is not None:
            try:
                await current_serial.disconnect()
            except Exception:
                pass
        await manager.kill_all()
        await terminal_sessions.stop_all()
        if owned_llm is not None:
            close = getattr(owned_llm, "aclose", None)
            if callable(close):
                await close()
        await unified_agent_service.close()
        await generic_event_transport.close()

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
    application.state.model_router_service = router_service
    application.state.project_service = projects_service
    application.state.project_import_service = imports_service
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
    application.state.settings_service = app_settings
    application.state.terminal_service = terminal_sessions
    application.state.bridge_detection_service = bridge_detection
    application.state.codex_login_service = codex_login
    application.state.codex_oauth_smoke_service = codex_oauth_smoke
    application.state.bridge_diff_service = bridge_reviews
    application.state.bridge_patch_export_service = patch_exports
    application.state.bridge_patch_apply_service = patch_apply
    application.state.bridge_patch_preflight_service = patch_preflight
    application.state.bridge_rollback_snapshot_service = rollback_snapshots
    application.state.bridge_rollback_restore_preflight_service = rollback_restore_preflight
    application.state.bridge_rollback_restore_apply_service = rollback_restore_apply
    application.state.bridge_agy_runner = agy_runner
    application.state.generic_bridge_registry = bridge_provider_registry
    application.state.provider_capability_registry = provider_strategy
    application.state.agy_generic_provider = agy_adapter
    application.state.generic_bridge_run_store = generic_run_store
    application.state.generic_bridge_event_transport = generic_event_transport
    application.state.generic_bridge_coordinator = generic_coordinator
    application.state.product_agent_service = product_agent_service
    application.state.unified_agent_service = unified_agent_service
    application.state.agent_approval_broker = agent_approval_broker
    application.state.agy_sdk_provider = agy_sdk_provider
    application.state.codex_app_server_provider = codex_provider
    application.state.agy_execution_router = agy_execution_router
    application.state.agy_trusted_workspace_service = agy_trusted_workspaces
    application.state.agy_scratch_import_service = agy_project_import
    application.state.agy_assisted_runner = assisted_agy
    application.state.generic_run_feature_flags = generic_run_flags
    application.state.generic_run_submit_lock = asyncio.Lock()

    @application.middleware("http")
    async def generic_run_body_limit(request, call_next):
        if request.method == "POST" and request.url.path in {
            "/agent-runtime/runs",
            "/agent-runtime/agy-scratch-import",
            "/agent-runtime/agy-assisted-runs",
            "/agent-runtime/providers/codex-oauth/login/launch",
            "/agent-runtime/providers/codex-oauth/standalone-smoke",
            "/models/bridges/runs",
            "/models/bridges/generic/runs",
        }:
            raw_length = request.headers.get("content-length")
            try:
                content_length = int(raw_length) if raw_length is not None else 0
            except ValueError:
                content_length = 32_769
            if content_length > 32_768:
                return JSONResponse(
                    status_code=413,
                    content={
                        "code": "REQUEST_TOO_LARGE",
                        "message": "Generic run request body is too large.",
                        "details": {},
                    },
                )
        return await call_next(request)

    application.middleware("http")(request_context_middleware)
    install_error_handlers(application)
    application.include_router(health.router)
    application.include_router(settings.router)
    application.include_router(agent_runtime.router)
    application.include_router(generic_runs.router)
    application.include_router(models.router)
    application.include_router(execute.router)
    application.include_router(projects.router)
    application.include_router(files.router)
    application.include_router(build.router)
    application.include_router(flash.router)
    application.include_router(devices.router)
    application.include_router(monitor.router)
    application.include_router(terminal.router)
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
