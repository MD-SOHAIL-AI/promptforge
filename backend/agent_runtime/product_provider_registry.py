"""Unified compatibility registry for fake, API, paused, and reference planners."""

from __future__ import annotations

import os
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..bridges.codex_status import CodexAlignedStatus, CodexStatusService
from ..model_router.registry import ProviderRegistry as ModelProviderRegistry
from .api_planner_provider import API_PLANNER_CONFIGS, ApiPlannerProvider, ApiTransport, safe_provider_status
from .product_providers import FakeProductPlanner, ProductPlanner, VerifiedTemplatePlanner
from ..provider_runtime.templates import match_template


@dataclass(frozen=True, slots=True)
class ProductProviderEntry:
    provider_id: str
    display_name: str
    kind: str
    state: str
    routeable: bool
    enabled_by_default: bool = False
    provider_flag: str | None = None
    provider_flag_enabled: bool = False
    api_key_env: str | None = None
    model_env: str | None = None
    default_model: str | None = None
    base_url_configured: bool = False
    supports_toolplan: bool = False
    supports_streaming: bool = False
    key_present: bool = False
    model_configured: bool = False
    model_id: str | None = None
    last_classification: str | None = None
    experimental: bool = False
    review_eligible: bool = False
    production_eligible: bool = False
    product_routing_enabled: bool = False
    qa_only: bool = False
    paused_reason: str | None = None
    execution_mode: str | None = None
    workspace_mode: str | None = None
    auth_mode: str | None = None
    detected: bool = False
    authenticated: bool = False
    oauth_bridge_ready: bool = False
    sandbox_smoke_passed: bool = False

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id, "display_name": self.display_name,
            "kind": self.kind, "provider_kind": self.kind, "state": self.state,
            "routeable": self.routeable, "enabled_by_default": self.enabled_by_default,
            "provider_flag": self.provider_flag, "provider_flag_enabled": self.provider_flag_enabled,
            "api_key_env": self.api_key_env, "model_env": self.model_env,
            "default_model": self.default_model, "base_url_configured": self.base_url_configured,
            "supports_toolplan": self.supports_toolplan, "supports_streaming": self.supports_streaming,
            "key_present": self.key_present, "model_configured": self.model_configured,
            "model_id": self.model_id, "last_classification": self.last_classification,
            "experimental": self.experimental, "review_eligible": self.review_eligible,
            "production_eligible": self.production_eligible,
            "product_routing_enabled": self.product_routing_enabled,
            "qa_only": self.qa_only, "paused_reason": self.paused_reason,
            "execution_mode": self.execution_mode, "workspace_mode": self.workspace_mode,
            "auth_mode": self.auth_mode,
            "detected": self.detected, "authenticated": self.authenticated,
            "oauth_bridge_ready": self.oauth_bridge_ready,
            "sandbox_smoke_passed": self.sandbox_smoke_passed,
        }


class ProductProviderRegistry:
    def __init__(
        self,
        *,
        fake_enabled: bool = False,
        env: Mapping[str, str] | None = None,
        confirm_real_api: bool = False,
        transport: ApiTransport | None = None,
        codex_status_service: CodexStatusService | None = None,
        model_provider_registry: ModelProviderRegistry | None = None,
    ) -> None:
        self._env = env if env is not None else os.environ
        self._confirmed = bool(confirm_real_api)
        self._transport = transport
        self._fake_enabled = bool(fake_enabled)
        self._codex_status_service = codex_status_service or CodexStatusService(env=self._env)
        self._model_provider_registry = model_provider_registry
        self.last_planner: ProductPlanner | None = None

    def list(self) -> tuple[ProductProviderEntry, ...]:
        effective_env = self._effective_env()
        codex_classification = _codex_subscription_classification(effective_env)
        codex_review_eligible = codex_classification == "CODEX_SUBSCRIPTION_BRIDGE_PASS"
        oauth_state = _codex_oauth_smoke_state(effective_env, self._codex_status_service.status())
        agy_import_classification = _agy_scratch_import_classification(effective_env)
        agy_import_enabled = effective_env.get("FORGEX_ENABLE_AGY_SCRATCH_IMPORT", "").strip() == "1"
        assisted_classification = _agy_assisted_classification(effective_env)
        assisted_enabled = effective_env.get("FORGEX_ENABLE_AGY_ASSISTED_RUNNER", "").strip() == "1" and agy_import_enabled
        entries = [
            ProductProviderEntry("fake_planner", "ForgeX Fake Planner", "fake", "enabled" if self._fake_enabled else "disabled", self._fake_enabled),
            ProductProviderEntry("verified_template", "Verified template", "template_provider", "ready", True, enabled_by_default=True, review_eligible=True, production_eligible=True, product_routing_enabled=True, execution_mode="forge_owned_tools", workspace_mode="managed_sandbox", auth_mode="none"),
        ]
        for config in API_PLANNER_CONFIGS.values():
            status = safe_provider_status(config, effective_env, confirmed=self._confirmed)
            entries.append(ProductProviderEntry(
                config.provider_id, config.display_name, "api_planner", str(status["status"]),
                bool(status["routeable"]), bool(status["enabled_by_default"]),
                str(status["provider_flag"]), bool(status["provider_flag_enabled"]),
                str(status["api_key_env"]), str(status["model_env"]),
                status["default_model"] if isinstance(status["default_model"], str) else None,
                bool(status["base_url_configured"]), bool(status["supports_toolplan"]),
                bool(status["supports_streaming"]), bool(status["key_present"]),
                bool(status["model_configured"]), status["model_id"] if isinstance(status["model_id"], str) else None,
            ))
        entries.extend((
            ProductProviderEntry(
                "agy_scratch_runner", "AGY Assisted Scratch Generator", "local_cli_generator",
                "experimental" if assisted_enabled else "disabled", False,
                provider_flag="FORGEX_ENABLE_AGY_ASSISTED_RUNNER",
                provider_flag_enabled=assisted_enabled,
                last_classification=assisted_classification,
                experimental=True,
                review_eligible=assisted_classification == "AGY_ASSISTED_IMPORT_PASS",
                production_eligible=False, product_routing_enabled=False, qa_only=False,
                paused_reason=None if assisted_classification == "AGY_ASSISTED_IMPORT_PASS" else assisted_classification,
                execution_mode="assisted_scratch_generation", workspace_mode="agy_scratch_import", auth_mode="official_cli_auth",
            ),
            ProductProviderEntry(
                "agy_scratch_import", "AGY Scratch Import", "manual_artifact",
                "enabled" if agy_import_enabled else "disabled", False,
                provider_flag="FORGEX_ENABLE_AGY_SCRATCH_IMPORT",
                provider_flag_enabled=agy_import_enabled,
                last_classification=agy_import_classification,
                review_eligible=agy_import_classification == "AGY_SCRATCH_IMPORT_PASS",
                production_eligible=False, product_routing_enabled=False, qa_only=False,
                paused_reason=None if agy_import_classification == "AGY_SCRATCH_IMPORT_PASS" else agy_import_classification,
                execution_mode="manual_import", workspace_mode="managed_import_sandbox", auth_mode="none",
            ),
            ProductProviderEntry(
                "codex_cli_oauth_bridge", "Codex CLI OAuth Bridge", "local_cli",
                "qa_only", False, last_classification=oauth_state["classification"],
                experimental=True, review_eligible=oauth_state["passed"], production_eligible=False,
                product_routing_enabled=False, qa_only=True,
                paused_reason=None if oauth_state["passed"] else oauth_state["classification"],
                execution_mode="sandboxed_oauth_smoke", workspace_mode="external_disposable_sandbox",
                auth_mode="official_codex_cli_oauth", detected=oauth_state["detected"],
                authenticated=oauth_state["authenticated"], oauth_bridge_ready=oauth_state["ready"],
                sandbox_smoke_passed=oauth_state["passed"],
            ),
            ProductProviderEntry(
                "codex_cli_subscription", "Codex CLI Subscription (Experimental)",
                "cli_subscription_bridge", "qa_only", False,
                last_classification=codex_classification,
                experimental=True, review_eligible=codex_review_eligible,
                production_eligible=False, product_routing_enabled=False, qa_only=True,
                paused_reason=None if codex_review_eligible else codex_classification,
            ),
            ProductProviderEntry("agy", "AGY Local CLI", "local_cli", "paused", False),
            ProductProviderEntry("codex", "Codex Local CLI", "local_cli", "paused", False),
            ProductProviderEntry("opencode", "OpenCode", "reference", "reference_only", False),
        ))
        return tuple(sorted(entries, key=lambda item: item.provider_id))

    def resolve(self, provider_id: str) -> ProductPlanner:
        if provider_id == "verified_template":
            self.last_planner = VerifiedTemplatePlanner()
            return self.last_planner
        if provider_id == "fake_planner" and self._fake_enabled:
            self.last_planner = FakeProductPlanner()
            return self.last_planner
        config = API_PLANNER_CONFIGS.get(provider_id)
        if config is not None:
            effective_env = self._effective_env()
            status = safe_provider_status(config, effective_env, confirmed=self._confirmed)
            if not bool(status["routeable"]):
                raise ValueError(str(status["status"]))
            self.last_planner = ApiPlannerProvider(config, env=effective_env, confirmed=self._confirmed, transport=self._transport)
            return self.last_planner
        raise ValueError("PRODUCT_PROVIDER_NOT_ROUTEABLE")

    def safe_statuses(self) -> tuple[dict[str, object], ...]:
        return tuple(item.to_safe_dict() for item in self.list())

    def configured_fallback(self, provider_id: str) -> str | None:
        registry = self._model_provider_registry
        if registry is None:
            return None
        route = registry.route_for_task("code_generation")
        fallback_id = route.fallback_provider_id
        if not route.fallback_enabled or route.provider_id != provider_id or not fallback_id:
            return None
        primary = registry.provider(provider_id)
        fallback = registry.provider(fallback_id)
        if primary.local != fallback.local or not fallback.configured or not fallback.enabled:
            return None
        if fallback.health_status.casefold() not in {"connected", "ready"}:
            return None
        return fallback_id

    def fallback_for_instruction(self, provider_id: str, instruction: str) -> str | None:
        configured = self.configured_fallback(provider_id)
        if configured:
            return configured
        if provider_id != "verified_template" and match_template(instruction) is not None:
            return "verified_template"
        return None

    def _effective_env(self) -> dict[str, str]:
        values = dict(self._env)
        registry = self._model_provider_registry
        if registry is None:
            return values
        for provider_id, config in API_PLANNER_CONFIGS.items():
            if provider_id not in registry.provider_ids():
                continue
            provider = registry.provider(provider_id)
            if not provider.configured or not provider.enabled:
                continue
            key = registry.api_key(provider_id)
            if not key:
                continue
            values[config.api_key_env] = key
            values[config.model_env] = registry.default_model(provider_id)
            values[config.provider_flag] = "1"
            # Saving and enabling a provider in the backend-owned settings is
            # the explicit operator action that enables API generation. The
            # agent runtime itself remains independently feature-gated.
            values["FORGEX_ENABLE_API_PROVIDERS"] = "1"
        return values


def _codex_subscription_classification(env: Mapping[str, str]) -> str | None:
    root = Path(env.get("PROMPTFORGE_ROOT") or Path(__file__).resolve().parents[2])
    status_path = root / ".promptforge" / "state" / "codex-subscription-bridge-status.json"
    try:
        value = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    classification = value.get("classification") if isinstance(value, dict) else None
    if not isinstance(classification, str) or not classification.startswith("CODEX_") or len(classification) > 80:
        return None
    return classification


def _codex_oauth_smoke_state(env: Mapping[str, str], status: CodexAlignedStatus) -> dict[str, object]:
    root = Path(env.get("PROMPTFORGE_ROOT") or Path(__file__).resolve().parents[2])
    status_path = root / ".promptforge" / "state" / "codex-oauth-smoke-status.json"
    try:
        value = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    classification = value.get("classification") if isinstance(value, dict) else None
    safe = classification if isinstance(classification, str) and classification.startswith("CODEX_OAUTH_SMOKE_") and len(classification) <= 80 else None
    passed = safe == "CODEX_OAUTH_SMOKE_PASS"
    return {
        "classification": safe if passed else status.bridge_classification,
        "passed": passed,
        "detected": status.codex_installed,
        "authenticated": status.auth_status == "signed_in",
        "ready": status.oauth_bridge_ready,
    }


def _agy_scratch_import_classification(env: Mapping[str, str]) -> str | None:
    root = Path(env.get("PROMPTFORGE_ROOT") or Path(__file__).resolve().parents[2])
    status_path = root / ".promptforge" / "state" / "agy-scratch-project-import-status.json"
    try:
        value = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    classification = value.get("classification") if isinstance(value, dict) else None
    if not isinstance(classification, str) or not classification.startswith("AGY_SCRATCH_") or len(classification) > 80:
        return None
    return classification


def _agy_assisted_classification(env: Mapping[str, str]) -> str | None:
    root = Path(env.get("PROMPTFORGE_ROOT") or Path(__file__).resolve().parents[2])
    status_path = root / ".promptforge" / "state" / "agy-assisted-runner-status.json"
    try:
        value = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    classification = value.get("classification") if isinstance(value, dict) else None
    if not isinstance(classification, str) or not classification.startswith("AGY_ASSISTED_") or len(classification) > 80:
        return None
    return classification
