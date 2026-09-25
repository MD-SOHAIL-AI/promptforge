"""Typed application service graph for the ForgeX API.

Routes obtain dependencies from one composition object instead of independently
addressing arbitrary ``app.state`` attributes. Mutable runtime services (notably
serial) are replaced through this object so all consumers observe the same
instance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ForgeXServices:
    execute_prompt: Any
    code_generation_service: Any
    model_router_service: Any
    project_service: Any
    project_import_service: Any
    subprocess_manager: Any
    platformio_service: Any
    llm_service: Any
    serial_service: Any
    serial_runtime: Any
    serial_service_factory: Any
    monitor_lock: Any
    board_detector: Any
    tool_executor: Any
    project_flash_service: Any
    progress_hub: Any
    workspace_manager: Any
    metrics_registry: Any
    runtime_logger: Any
    configuration_error: Any
    settings_service: Any
    terminal_service: Any
    change_set_service: Any
    product_agent_service: Any
    artifact_service: Any
    agent_orchestrator: Any
    agent_session_store: Any
    chat_service: Any
    agent_event_store: Any
    agent_activity_broker: Any
    local_api_token: str | None
    local_auth_fingerprint: str | None
    harness_client: Any = None

    def replace_serial_service(self, service: Any) -> None:
        self.serial_service = service
        self.serial_runtime = service
