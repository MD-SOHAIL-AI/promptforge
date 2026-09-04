"use client";

import {
  CheckCircle2,
  Cloud,
  Cpu,
  KeyRound,
  Loader2,
  RefreshCw,
  Save,
  Server,
  TestTube2,
  WifiOff,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { promptForgeApi } from "@/lib/api";
import { toErrorMessage } from "@/lib/errors";
import type { ConsoleEntry, ModelInfoResponse, ModelProviderResponse, ModelRouteResponse, ProjectResponse } from "@/types";

interface ModelSettingsPanelProps {
  providers: ModelProviderResponse[];
  routes: ModelRouteResponse[];
  activeProject?: ProjectResponse | null;
  onRefresh: () => Promise<void>;
  onLog?: (entry: Omit<ConsoleEntry, "id" | "timestamp">) => void;
}

export function ModelSettingsPanel({ providers, routes, onRefresh, onLog }: ModelSettingsPanelProps) {
  const codeRoute = routes.find((route) => route.task_type === "code_generation") ?? null;
  const [providerId, setProviderId] = useState(codeRoute?.provider_id ?? providers[0]?.provider_id ?? "");
  const [modelId, setModelId] = useState(codeRoute?.model_id ?? "");
  const [fallbackEnabled, setFallbackEnabled] = useState(codeRoute?.fallback_enabled ?? true);
  const [fallbackProviderId, setFallbackProviderId] = useState(codeRoute?.fallback_provider_id ?? "");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [providerEnabled, setProviderEnabled] = useState(true);
  const [models, setModels] = useState<ModelInfoResponse[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save-provider" | "test" | "models" | "selection" | "refresh" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedProvider = providers.find((provider) => provider.provider_id === providerId) ?? null;
  const sameCategoryProviders = useMemo(
    () => providers.filter((provider) => provider.local === Boolean(selectedProvider?.local) && provider.provider_id !== providerId),
    [providerId, providers, selectedProvider?.local],
  );

  useEffect(() => {
    if (!providerId && codeRoute?.provider_id) setProviderId(codeRoute.provider_id);
    if (!modelId && codeRoute?.model_id) setModelId(codeRoute.model_id);
  }, [codeRoute?.model_id, codeRoute?.provider_id, modelId, providerId]);

  useEffect(() => {
    if (!selectedProvider) return;
    setBaseUrl(selectedProvider.base_url ?? "");
    setDefaultModel(selectedProvider.default_model ?? "");
    setProviderEnabled(selectedProvider.configured ? selectedProvider.enabled : true);
    setApiKey("");
    setModels([]);
    setModelsError(null);
    if (codeRoute?.provider_id === selectedProvider.provider_id) {
      setModelId(codeRoute.model_id);
      setFallbackEnabled(codeRoute.fallback_enabled);
      setFallbackProviderId(codeRoute.fallback_provider_id ?? "");
    } else {
      setModelId(selectedProvider.default_model ?? "");
      setFallbackProviderId("");
    }
  }, [selectedProvider?.provider_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const log = (channel: ConsoleEntry["channel"], message: string) => onLog?.({ channel, message });

  async function refreshAll() {
    setBusy("refresh");
    setError(null);
    try {
      await onRefresh();
      setMessage("Model configuration refreshed.");
    } catch (cause) {
      const text = toErrorMessage(cause, "Model configuration could not be refreshed.");
      setError(text);
      log("error", text);
    } finally {
      setBusy(null);
    }
  }

  async function saveProvider() {
    if (!selectedProvider) return;
    setBusy("save-provider");
    setError(null);
    setMessage(null);
    try {
      const body: { api_key?: string; base_url?: string; default_model?: string; enabled?: boolean } = {
        enabled: providerEnabled,
        default_model: defaultModel.trim() || selectedProvider.default_model,
      };
      if (baseUrl.trim()) body.base_url = baseUrl.trim();
      if (apiKey.trim()) body.api_key = apiKey.trim();
      await promptForgeApi.configureModelProvider(selectedProvider.provider_id, body);
      setApiKey("");
      await onRefresh();
      setMessage(`${selectedProvider.display_name} saved.`);
      log("system", `Saved model provider ${selectedProvider.provider_id}.`);
    } catch (cause) {
      const text = toErrorMessage(cause, "Provider configuration could not be saved.");
      setError(text);
      log("error", text);
    } finally {
      setBusy(null);
    }
  }

  async function testProvider() {
    if (!selectedProvider) return;
    setBusy("test");
    setError(null);
    setMessage(null);
    try {
      const result = await promptForgeApi.testModelProvider(selectedProvider.provider_id);
      const health = result.health;
      setMessage(health.ok ? `${selectedProvider.display_name} is ready${health.latency_ms ? ` · ${health.latency_ms} ms` : ""}.` : health.message || "Provider check failed.");
      await onRefresh();
    } catch (cause) {
      const text = toErrorMessage(cause, "Provider health check failed.");
      setError(text);
      log("error", text);
    } finally {
      setBusy(null);
    }
  }

  async function refreshModels() {
    if (!selectedProvider) return;
    setBusy("models");
    setModelsError(null);
    setError(null);
    try {
      const result = await promptForgeApi.providerModels(selectedProvider.provider_id);
      setModels(Array.isArray(result.models) ? result.models : []);
      if (result.error) setModelsError(result.error);
    } catch (cause) {
      const text = toErrorMessage(cause, "Model list could not be loaded.");
      setModelsError(text);
    } finally {
      setBusy(null);
    }
  }

  async function saveSelection() {
    if (!selectedProvider || !modelId.trim()) return;
    setBusy("selection");
    setError(null);
    setMessage(null);
    try {
      await promptForgeApi.saveModelSelection({
        provider_id: selectedProvider.provider_id,
        model_id: modelId.trim(),
        fallback_enabled: fallbackEnabled,
        fallback_provider_id: fallbackEnabled && fallbackProviderId ? fallbackProviderId : null,
      });
      await onRefresh();
      setMessage(`Active model set to ${selectedProvider.display_name} / ${modelId.trim()}.`);
      log("system", `Active model changed to ${selectedProvider.provider_id}/${modelId.trim()}.`);
    } catch (cause) {
      const text = toErrorMessage(cause, "Active model selection could not be saved.");
      setError(text);
      log("error", text);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="min-h-0">
      <div className="mx-auto w-full max-w-[1120px] space-y-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-[var(--fx-text)]">Models</h2>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-[var(--fx-text-muted)]">
              Configure providers and choose the single model ForgeX uses for code generation and agent planning.
            </p>
          </div>
          <button className="fx-control flex h-9 shrink-0 items-center justify-center gap-1.5 px-3 text-xs text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]" onClick={() => void refreshAll()} disabled={Boolean(busy)}>
            <RefreshCw className={`h-3.5 w-3.5 ${busy === "refresh" ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </div>

        <div className="grid gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
          <div className="min-h-0 overflow-hidden rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] xl:sticky xl:top-0 xl:max-h-[calc(100dvh-230px)]">
            <div className="border-b border-[var(--fx-border)] px-3 py-2 text-[10px] font-medium uppercase tracking-[.12em] text-[var(--fx-text-muted)]">Providers</div>
            <div className="max-h-[260px] overflow-y-auto p-1.5 [scrollbar-gutter:stable] xl:max-h-[calc(100dvh-270px)]">
              {providers.map((provider) => {
                const active = provider.provider_id === providerId;
                return (
                  <button
                    key={provider.provider_id}
                    className={`mb-1 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left ${active ? "bg-[var(--fx-accent-soft)] text-[var(--fx-text)]" : "text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)]"}`}
                    onClick={() => setProviderId(provider.provider_id)}
                  >
                    {provider.local ? <Cpu className="h-4 w-4 shrink-0 text-[var(--fx-info)]" /> : <Cloud className="h-4 w-4 shrink-0 text-[var(--fx-info)]" />}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-xs font-medium">{provider.display_name}</span>
                      <span className="mt-0.5 block truncate text-[9px] text-[var(--fx-text-muted)]">{provider.health_status || (provider.configured ? "configured" : "not configured")}</span>
                    </span>
                    <ProviderDot provider={provider} />
                  </button>
                );
              })}
            </div>
          </div>

          <div className="min-w-0 space-y-4">
            {selectedProvider ? (
              <>
                <section className="rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-4">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-[var(--fx-text)]">{selectedProvider.display_name}</div>
                      <div className="mt-1 text-[10px] text-[var(--fx-text-muted)]">{selectedProvider.local ? "Local provider" : selectedProvider.configured ? "Credential configured" : "Credential required"}</div>
                    </div>
                    <label className="flex items-center gap-2 text-xs text-[var(--fx-code-text)]">
                      <input type="checkbox" checked={providerEnabled} onChange={(event) => setProviderEnabled(event.target.checked)} />
                      Enabled
                    </label>
                  </div>

                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    {selectedProvider.auth_type === "api_key" ? (
                      <label className="sm:col-span-2">
                        <span className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[.1em] text-[var(--fx-text-muted)]"><KeyRound className="h-3 w-3" /> API key</span>
                        <input className="fx-control h-9 w-full px-3 text-xs text-[var(--fx-text)] outline-none" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder={selectedProvider.configured ? "Saved securely · enter only to replace" : "Enter API key"} />
                      </label>
                    ) : null}
                    <label>
                      <span className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[.1em] text-[var(--fx-text-muted)]"><Server className="h-3 w-3" /> Base URL</span>
                      <input className="fx-control h-9 w-full px-3 text-xs text-[var(--fx-text)] outline-none" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="Provider default" />
                    </label>
                    <label>
                      <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-[.1em] text-[var(--fx-text-muted)]">Default model</span>
                      <input className="fx-control h-9 w-full px-3 text-xs text-[var(--fx-text)] outline-none" value={defaultModel} onChange={(event) => setDefaultModel(event.target.value)} />
                    </label>
                  </div>

                  <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                    <button className="fx-control flex h-9 items-center justify-center gap-1.5 px-3 text-xs text-[var(--fx-text)]" onClick={() => void testProvider()} disabled={Boolean(busy) || (!selectedProvider.configured && !selectedProvider.local)}>
                      {busy === "test" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <TestTube2 className="h-3.5 w-3.5" />} Test
                    </button>
                    <button className="flex h-9 items-center justify-center gap-1.5 rounded bg-[var(--fx-accent)] px-3 text-xs font-medium text-white disabled:opacity-50" onClick={() => void saveProvider()} disabled={Boolean(busy)}>
                      {busy === "save-provider" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save provider
                    </button>
                  </div>
                </section>

                <section className="rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-4">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold text-[var(--fx-text)]">Active model</div>
                      <div className="mt-1 text-[10px] text-[var(--fx-text-muted)]">This is the persisted selection; hidden process environment values do not replace it at request time.</div>
                    </div>
                    <button className="fx-control flex h-9 shrink-0 items-center justify-center gap-1.5 px-3 text-xs text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]" onClick={() => void refreshModels()} disabled={Boolean(busy)}>
                      <RefreshCw className={`h-3.5 w-3.5 ${busy === "models" ? "animate-spin" : ""}`} /> Models
                    </button>
                  </div>

                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <label>
                      <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-[.1em] text-[var(--fx-text-muted)]">Provider</span>
                      <select className="fx-control h-9 w-full px-2 text-xs text-[var(--fx-text)] outline-none" value={providerId} onChange={(event) => setProviderId(event.target.value)}>
                        {providers.map((provider) => <option key={provider.provider_id} value={provider.provider_id}>{provider.display_name}</option>)}
                      </select>
                    </label>
                    <label>
                      <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-[.1em] text-[var(--fx-text-muted)]">Model ID</span>
                      <input className="fx-control h-9 w-full px-3 text-xs text-[var(--fx-text)] outline-none" list={`models-${providerId}`} value={modelId} onChange={(event) => setModelId(event.target.value)} placeholder={selectedProvider.default_model} />
                      <datalist id={`models-${providerId}`}>{models.map((model) => <option key={model.model_id} value={model.model_id}>{model.display_name}</option>)}</datalist>
                    </label>
                  </div>

                  {modelsError ? <p className="mt-2 text-[10px] text-[var(--fx-warning)]">{modelsError}</p> : null}

                  <div className="mt-4 rounded-lg border border-[var(--fx-border)] bg-[var(--fx-input)] p-3">
                    <label className="flex items-center gap-2 text-xs text-[var(--fx-code-text)]">
                      <input type="checkbox" checked={fallbackEnabled} onChange={(event) => setFallbackEnabled(event.target.checked)} />
                      Allow fallback to another {selectedProvider.local ? "local" : "cloud"} provider
                    </label>
                    {fallbackEnabled ? (
                      <select className="fx-control mt-2 h-8 w-full px-2 text-xs text-[var(--fx-text)] outline-none" value={fallbackProviderId} onChange={(event) => setFallbackProviderId(event.target.value)}>
                        <option value="">Automatic fallback</option>
                        {sameCategoryProviders.map((provider) => <option key={provider.provider_id} value={provider.provider_id}>{provider.display_name}</option>)}
                      </select>
                    ) : null}
                  </div>

                  <div className="mt-4 flex justify-end">
                    <button className="flex h-9 w-full items-center justify-center gap-1.5 rounded bg-[var(--fx-accent)] px-3 text-xs font-medium text-white disabled:opacity-50 sm:w-auto" onClick={() => void saveSelection()} disabled={Boolean(busy) || !modelId.trim()}>
                      {busy === "selection" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Use this model
                    </button>
                  </div>
                </section>
              </>
            ) : (
              <div className="rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-4 text-xs text-[var(--fx-text-muted)]">No model providers are available.</div>
            )}
          </div>
        </div>

        {message ? <div className="flex items-center gap-2 rounded-lg border border-[var(--fx-success)] bg-[var(--fx-success-soft)] px-3 py-2 text-xs text-[var(--fx-success)]"><CheckCircle2 className="h-3.5 w-3.5" />{message}</div> : null}
        {error ? <div className="flex items-center gap-2 rounded-lg border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-3 py-2 text-xs text-[var(--fx-error)]"><WifiOff className="h-3.5 w-3.5" />{error}</div> : null}
      </div>
    </div>
  );
}

function ProviderDot({ provider }: { provider: ModelProviderResponse }) {
  const ready = provider.configured && provider.enabled && !["error", "offline", "not_configured", "disabled"].includes(provider.health_status ?? "");
  return <span className={`h-2 w-2 shrink-0 rounded-full ${ready ? "bg-[var(--fx-success)]" : provider.configured ? "bg-[var(--fx-warning)]" : "bg-[var(--fx-text-muted)]"}`} aria-label={ready ? "ready" : "not ready"} />;
}
