export type BusyKey = string | null;

export type DiagnosticsFilter = "all" | "success" | "failed" | "incomplete" | "repair" | "fallback";

export interface ProviderFormState {
  enabled: boolean;
  baseUrl: string;
  defaultModel: string;
}

export interface RouteFormState {
  providerId: string;
  modelId: string;
  fallbackEnabled: boolean;
  fallbackProviderId: string;
  localOnly: boolean;
}
