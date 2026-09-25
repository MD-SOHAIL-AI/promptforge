import type { ModelProviderResponse } from "@/types";

export type ProviderPresentationStatus =
  | "ready"
  | "needs_key"
  | "rate_limited"
  | "quota_exhausted"
  | "degraded"
  | "unavailable"
  | "disabled"
  | "unknown";

export function providerPresentationStatus(provider: ModelProviderResponse): ProviderPresentationStatus {
  if (!provider.enabled) return "disabled";
  if (provider.auth_type === "api_key" && !provider.configured) return "needs_key";
  const status = (provider.health_status ?? "").toLowerCase();
  const error = (provider.last_error ?? provider.blocking_code ?? "").toLowerCase();
  if (status.includes("quota") || error.includes("quota")) return "quota_exhausted";
  if (status.includes("rate") || error.includes("rate")) return "rate_limited";
  if (["error", "offline", "unavailable"].some((value) => status.includes(value))) return "unavailable";
  if (status.includes("degraded")) return "degraded";
  if (provider.configured || provider.auth_type !== "api_key") return "ready";
  return "unknown";
}

export function providerStatusLabel(status: ProviderPresentationStatus): string {
  return {
    ready: "Ready",
    needs_key: "Needs API key",
    rate_limited: "Rate limited",
    quota_exhausted: "Quota exhausted",
    degraded: "Degraded",
    unavailable: "Unavailable",
    disabled: "Disabled",
    unknown: "Not checked",
  }[status];
}
