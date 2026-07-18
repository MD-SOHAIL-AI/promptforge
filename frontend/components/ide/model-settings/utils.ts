import type {
  BridgeDetectionResponse,
  BridgePatchExportResponse,
  ModelProviderResponse,
  ModelRouteResponse,
  PatchApplyResult,
} from "@/types";

export function safeProviders(value: ModelProviderResponse[]) {
  return Array.isArray(value) ? value : [];
}

export function safeRoutes(value: ModelRouteResponse[]) {
  return Array.isArray(value) ? value : [];
}

export function providerStatus(provider: ModelProviderResponse) {
  if (!provider.enabled) return "Disabled";
  if (provider.local) return "Local";
  return provider.configured ? "Configured" : "Not configured";
}

export function healthLabel(status?: string | null) {
  if (status === "connected") return "Healthy";
  if (status === "ready") return "Ready";
  if (status === "missing_api_key" || status === "not_configured") return "Missing API key";
  if (status === "not_logged_in" || status === "authentication_required") return "Not logged in";
  if (status === "cli_not_found") return "CLI not found";
  if (status === "rate_limited") return "Rate limited";
  if (status === "usage_limit_reached") return "Usage limit reached";
  if (status === "unavailable") return "Unavailable";
  if (status === "disabled") return "Disabled";
  if (status === "offline") return "Not running";
  if (status === "error") return "Unhealthy";
  return "Unknown";
}

export function healthClass(status?: string | null) {
  if (status === "connected") return "border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-[var(--fx-success)]";
  if (status === "offline" || status === "error") return "border-[var(--fx-error)] bg-[var(--fx-error-soft)] text-[var(--fx-error)]";
  return "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text-muted)]";
}

export function shortModel(modelId: string) {
  return modelId.split("/").pop() || modelId;
}

export function localHint(provider: ModelProviderResponse) {
  if (provider.provider_id === "ollama") return "Start Ollama, then click Refresh Models.";
  if (provider.provider_id === "lmstudio") return "Start LM Studio local server, then click Refresh Models.";
  return "Could not fetch model list. Enter model ID manually.";
}

export function bridgeStatusClass(bridge: BridgeDetectionResponse) {
  if (bridge.installed) return "border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-[var(--fx-success)]";
  return "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text-muted)]";
}

export function bridgeAuthClass(status: BridgeDetectionResponse["auth_status"]) {
  if (status === "authenticated") return "border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-[var(--fx-success)]";
  if (status === "unauthenticated" || status === "error") return "border-[var(--fx-error)] bg-[var(--fx-error-soft)] text-[var(--fx-error)]";
  return "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text-muted)]";
}

export function bridgeConfidenceClass(confidence: BridgeDetectionResponse["status_confidence"]) {
  if (confidence === "high") return "border-[var(--fx-success)] bg-[var(--fx-success-soft)] text-[var(--fx-success)]";
  if (confidence === "medium") return "border-[var(--fx-warning)] bg-[var(--fx-warning-soft)] text-[var(--fx-warning)]";
  return "border-[var(--fx-border)] bg-[var(--fx-input)] text-[var(--fx-text-muted)]";
}

export function bridgeSetupLabel(action: BridgeDetectionResponse["setup_action"]) {
  if (action === "open_docs") return "Open docs";
  if (action === "run_official_login_manually") return "Run official login manually";
  return "None";
}

export function bridgeCommandLabel(bridge: BridgeDetectionResponse) {
  if (bridge.provider_id === "antigravity_cli_bridge") return "agy";
  if (bridge.provider_id === "codex_cli_oauth_bridge") return "codex";
  if (bridge.provider_id === "claude_code_bridge") return "claude";
  return bridge.provider_id;
}

export function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export function integrityClass(status: BridgePatchExportResponse["integrity_status"]) {
  if (status === "valid") return "text-[var(--fx-success)]";
  if (status === "modified" || status === "missing") return "text-[var(--fx-warning)]";
  return "text-[var(--fx-text-muted)]";
}

export function patchLabel(patch: BridgePatchExportResponse) {
  const provider = patch.provider_id === "antigravity_cli_bridge" ? "AGY patch" : patch.provider_id;
  return `${provider} - ${patch.changed_file_count} file${patch.changed_file_count === 1 ? "" : "s"}`;
}

export function applyLabel(apply: PatchApplyResult) {
  const provider = apply.provider_id === "antigravity_cli_bridge" ? "AGY patch apply" : `${apply.provider_id} apply`;
  const changed = apply.files_created + apply.files_modified + apply.files_deleted;
  const parts = [];
  if (apply.files_created) parts.push(`${apply.files_created} created`);
  if (apply.files_modified) parts.push(`${apply.files_modified} modified`);
  if (apply.files_deleted) parts.push(`${apply.files_deleted} deleted`);
  return `${provider} - ${parts.join(", ") || `${changed} changed`}`;
}

export function formatRelativeTime(value: string) {
  const timestamp = Date.parse(value);
  if (Number.isNaN(timestamp)) return value;
  const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  return `${days}d ago`;
}
