"use client";

import {
  CheckCircle2,
  Cloud,
  Cpu,
  KeyRound,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Server,
  TestTube2,
  Trash2,
  WifiOff,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { promptForgeApi } from "@/lib/api";
import { toErrorMessage } from "@/lib/errors";
import { providerPresentationStatus, providerStatusLabel } from "@/lib/model-provider-status";
import { roleBindingSource, splitModelReference, type ModelRoleDraft } from "@/lib/model-role-bindings";
import type { ConsoleEntry, ModelInfoResponse, ModelProviderResponse, ModelRole, ModelRoleBinding, ModelRouteResponse, ProjectResponse } from "@/types";

const AGENT_ROLES: { role: Exclude<ModelRole, "main">; label: string; description: string }[] = [
  { role: "explore", label: "Explorer", description: "Repository discovery and focused evidence gathering." },
  { role: "review", label: "Reviewer", description: "ChangeSet, risk, and correctness review." },
  { role: "verify", label: "Verifier", description: "Build and validation evidence analysis." },
  { role: "compact", label: "Compactor", description: "Long-context checkpoint and evidence compression." },
  { role: "title", label: "Title", description: "Short session and task naming." },
];

type AgentRole = Exclude<ModelRole, "main">;

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
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [providerEnabled, setProviderEnabled] = useState(true);
  const [models, setModels] = useState<ModelInfoResponse[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save-provider" | "create-provider" | "delete-provider" | "test" | "models" | "selection" | "refresh" | null>(null);
  const [showCreateProvider, setShowCreateProvider] = useState(false);
  const [newProvider, setNewProvider] = useState<{
    provider_id: string;
    display_name: string;
    protocol: "openai-completions" | "openai-responses" | "anthropic-messages";
    base_url: string;
    default_model: string;
    api_key: string;
    local: boolean;
  }>({ provider_id: "", display_name: "", protocol: "openai-completions", base_url: "", default_model: "", api_key: "", local: false });
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [roleBindings, setRoleBindings] = useState<Partial<Record<AgentRole, ModelRoleBinding>>>({});
  const [roleDrafts, setRoleDrafts] = useState<Partial<Record<AgentRole, ModelRoleDraft>>>({});
  const [roleBusy, setRoleBusy] = useState<AgentRole | "load" | null>(null);

  const selectedProvider = providers.find((provider) => provider.provider_id === providerId) ?? null;
  const externallyAuthenticated = selectedProvider?.auth_type === "oauth_external";
  const providerGroups = useMemo(() => {
    const groups = [
      { label: "Cloud APIs", providers: providers.filter((provider) => !provider.local && provider.auth_type === "api_key") },
      { label: "Local models", providers: providers.filter((provider) => provider.local) },
      { label: "External bridges", providers: providers.filter((provider) => !provider.local && provider.auth_type === "oauth_external") },
    ];
    return groups.filter((group) => group.providers.length > 0);
  }, [providers]);

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

    } else {
      setModelId(selectedProvider.default_model ?? "");
    }
  }, [selectedProvider?.provider_id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (selectedProvider?.provider_id !== "cerebras" || !selectedProvider.configured) return;
    let cancelled = false;
    void promptForgeApi.providerModels("cerebras").then((result) => {
      if (cancelled) return;
      setModels(Array.isArray(result.models) ? result.models : []);
      setModelsError(result.error ?? null);
    }).catch((cause) => {
      if (!cancelled) setModelsError(toErrorMessage(cause, "Cerebras models could not be loaded."));
    });
    return () => { cancelled = true; };
  }, [selectedProvider?.provider_id, selectedProvider?.configured]);

  useEffect(() => {
    void loadRoleBindings();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const log = (channel: ConsoleEntry["channel"], message: string) => onLog?.({ channel, message });

  async function refreshAll() {
    setBusy("refresh");
    setError(null);
    try {
      await Promise.all([onRefresh(), loadRoleBindings()]);
      setMessage("Model configuration refreshed.");
    } catch (cause) {
      const text = toErrorMessage(cause, "Model configuration could not be refreshed.");
      setError(text);
      log("error", text);
    } finally {
      setBusy(null);
    }
  }

  async function loadRoleBindings() {
    setRoleBusy("load");
    try {
      const result = await promptForgeApi.modelRoleBindings();
      const bindings: Partial<Record<AgentRole, ModelRoleBinding>> = {};
      const drafts: Partial<Record<AgentRole, ModelRoleDraft>> = {};
      for (const option of AGENT_ROLES) {
        const binding = result.bindings[option.role];
        bindings[option.role] = binding;
        drafts[option.role] = splitModelReference(
          binding?.model,
          providers,
          codeRoute?.provider_id ?? providers[0]?.provider_id ?? "",
          codeRoute?.model_id ?? providers[0]?.default_model ?? "",
        );
      }
      setRoleBindings(bindings);
      setRoleDrafts(drafts);
    } catch (cause) {
      setError(toErrorMessage(cause, "Agent role model bindings could not be loaded."));
    } finally {
      setRoleBusy(null);
    }
  }

  function updateRoleDraft(role: AgentRole, patch: Partial<ModelRoleDraft>) {
    setRoleDrafts((current) => ({
      ...current,
      [role]: { providerId: "", modelId: "", ...current[role], ...patch },
    }));
  }

  async function saveRoleBinding(role: AgentRole) {
    const draft = roleDrafts[role];
    if (!draft?.providerId || !draft.modelId.trim()) return;
    setRoleBusy(role);
    setError(null);
    setMessage(null);
    try {
      await promptForgeApi.saveModelRoleBinding({
        provider_id: draft.providerId,
        model_id: draft.modelId.trim(),
        role,
      });
      await loadRoleBindings();
      setMessage(`${AGENT_ROLES.find((item) => item.role === role)?.label ?? role} model binding saved.`);
    } catch (cause) {
      setError(toErrorMessage(cause, `The ${role} model binding could not be saved.`));
    } finally {
      setRoleBusy(null);
    }
  }

  async function clearRoleBinding(role: AgentRole) {
    setRoleBusy(role);
    setError(null);
    setMessage(null);
    try {
      await promptForgeApi.clearModelRoleBinding(role);
      await loadRoleBindings();
      setMessage(`${AGENT_ROLES.find((item) => item.role === role)?.label ?? role} now inherits the main model.`);
    } catch (cause) {
      setError(toErrorMessage(cause, `The ${role} model binding could not be cleared.`));
    } finally {
      setRoleBusy(null);
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
      if (!externallyAuthenticated && baseUrl.trim()) body.base_url = baseUrl.trim();
      if (apiKey.trim()) body.api_key = apiKey.trim();
      await promptForgeApi.configureModelProvider(selectedProvider.provider_id, body);
      setApiKey("");
      await onRefresh();
      if (selectedProvider.provider_id === "cerebras") {
        try {
          const result = await promptForgeApi.providerModels("cerebras");
          setModels(Array.isArray(result.models) ? result.models : []);
          setModelsError(result.error ?? null);
        } catch (cause) {
          setModelsError(toErrorMessage(cause, "Cerebras models could not be loaded."));
        }
      }
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

  async function createProvider() {
    setBusy("create-provider");
    setError(null);
    setMessage(null);
    try {
      const result = await promptForgeApi.createModelProvider({
        ...newProvider,
        provider_id: newProvider.provider_id.trim().toLowerCase(),
        display_name: newProvider.display_name.trim(),
        base_url: newProvider.base_url.trim(),
        default_model: newProvider.default_model.trim(),
        api_key: newProvider.api_key.trim() || undefined,
      });
      await onRefresh();
      setProviderId(result.provider.provider_id);
      setShowCreateProvider(false);
      setNewProvider({ provider_id: "", display_name: "", protocol: "openai-completions", base_url: "", default_model: "", api_key: "", local: false });
      setMessage(`${result.provider.display_name} added.`);
    } catch (cause) {
      setError(toErrorMessage(cause, "Custom provider could not be added."));
    } finally {
      setBusy(null);
    }
  }

  async function deleteProvider() {
    if (!selectedProvider?.custom || !window.confirm(`Remove ${selectedProvider.display_name}? This cannot be undone.`)) return;
    setBusy("delete-provider");
    setError(null);
    setMessage(null);
    try {
      await promptForgeApi.deleteModelProvider(selectedProvider.provider_id);
      setProviderId(providers.find((provider) => provider.provider_id !== selectedProvider.provider_id)?.provider_id ?? "");
      await onRefresh();
      setMessage(`${selectedProvider.display_name} removed.`);
    } catch (cause) {
      setError(toErrorMessage(cause, "Custom provider could not be removed. Change any routes that use it first."));
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
      if (health.ok) {
        setMessage(`${selectedProvider.display_name} is ready${health.latency_ms ? ` · ${health.latency_ms} ms` : ""}.`);
      } else {
        setError(health.message || "Provider readiness check found a blocking issue.");
      }
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
    <div className="h-full min-h-0 overflow-y-auto p-4">
      <div className="mx-auto max-w-5xl space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold text-[var(--fx-text)]">Models</h2>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-[var(--fx-text-muted)]">
              Configure providers and choose the single model ForgeX uses for code generation and agent planning.
            </p>
          </div>
          <div className="flex gap-2">
            <button className="fx-control flex h-8 items-center gap-1.5 px-2.5 text-xs text-[var(--fx-text)]" onClick={() => setShowCreateProvider((value) => !value)} disabled={Boolean(busy)}>
              <Plus className="h-3.5 w-3.5" /> Add provider
            </button>
            <button className="fx-control flex h-8 items-center gap-1.5 px-2.5 text-xs text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]" onClick={() => void refreshAll()} disabled={Boolean(busy)}>
              <RefreshCw className={`h-3.5 w-3.5 ${busy === "refresh" ? "animate-spin" : ""}`} /> Refresh
            </button>
          </div>
        </div>

        {showCreateProvider ? (
          <section className="rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-4">
            <div className="text-xs font-semibold text-[var(--fx-text)]">Custom API provider</div>
            <p className="mt-1 text-[10px] text-[var(--fx-text-muted)]">Connect an OpenAI-compatible Responses or Chat Completions endpoint, or an Anthropic Messages endpoint.</p>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <input className="fx-control h-9 px-3 text-xs" value={newProvider.display_name} onChange={(event) => setNewProvider({ ...newProvider, display_name: event.target.value })} placeholder="Display name" />
              <input className="fx-control h-9 px-3 text-xs" value={newProvider.provider_id} onChange={(event) => setNewProvider({ ...newProvider, provider_id: event.target.value.replace(/[^a-zA-Z0-9_-]/g, "_") })} placeholder="provider_id" />
              <select className="fx-control h-9 px-2 text-xs" value={newProvider.protocol} onChange={(event) => setNewProvider({ ...newProvider, protocol: event.target.value as typeof newProvider.protocol })}>
                <option value="openai-completions">OpenAI Chat Completions</option>
                <option value="openai-responses">OpenAI Responses</option>
                <option value="anthropic-messages">Anthropic Messages</option>
              </select>
              <input className="fx-control h-9 px-3 text-xs" value={newProvider.default_model} onChange={(event) => setNewProvider({ ...newProvider, default_model: event.target.value })} placeholder="Default model ID" />
              <input className="fx-control h-9 px-3 text-xs sm:col-span-2" value={newProvider.base_url} onChange={(event) => setNewProvider({ ...newProvider, base_url: event.target.value })} placeholder="https://gateway.example/v1" />
              <input className="fx-control h-9 px-3 text-xs" type="password" autoComplete="off" value={newProvider.api_key} onChange={(event) => setNewProvider({ ...newProvider, api_key: event.target.value })} placeholder="API key (optional for local)" />
              <label className="flex items-center gap-2 text-xs text-[var(--fx-code-text)]"><input type="checkbox" checked={newProvider.local} onChange={(event) => setNewProvider({ ...newProvider, local: event.target.checked })} /> Local endpoint</label>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button className="fx-control h-8 px-3 text-xs" onClick={() => setShowCreateProvider(false)}>Cancel</button>
              <button className="flex h-8 items-center gap-1.5 rounded bg-[var(--fx-accent)] px-3 text-xs font-medium text-white disabled:opacity-50" onClick={() => void createProvider()} disabled={Boolean(busy) || !newProvider.provider_id.trim() || !newProvider.display_name.trim() || !newProvider.base_url.trim() || !newProvider.default_model.trim()}>
                {busy === "create-provider" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />} Add provider
              </button>
            </div>
          </section>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
          <div className="overflow-hidden rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)]">
            <div className="border-b border-[var(--fx-border)] px-3 py-2 text-[10px] font-medium uppercase tracking-[.12em] text-[var(--fx-text-muted)]">Providers</div>
            <div className="p-1.5">
              {providerGroups.map((group) => (
                <div key={group.label} className="mb-2 last:mb-0">
                  <div className="px-2.5 pb-1 pt-1.5 text-[9px] font-semibold uppercase tracking-[.12em] text-[var(--fx-text-muted)]">{group.label}</div>
                  {group.providers.map((provider) => {
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
                          <span className="mt-0.5 block truncate text-[9px] text-[var(--fx-text-muted)]">{providerStatusLabel(providerPresentationStatus(provider))}</span>
                        </span>
                        <ProviderDot provider={provider} />
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-4">
            {selectedProvider ? (
              <>
                <section className="rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-xs font-semibold text-[var(--fx-text)]">{selectedProvider.display_name}</div>
                      <div className="mt-1 text-[10px] text-[var(--fx-text-muted)]">
                        {externallyAuthenticated ? (selectedProvider.configured ? "External OAuth ready" : "External OAuth disabled") : selectedProvider.local ? "Local provider" : selectedProvider.configured ? "Credential configured" : "Credential required"}
                      </div>
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
                    {!externallyAuthenticated ? (
                      <label>
                        <span className="mb-1.5 flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[.1em] text-[var(--fx-text-muted)]"><Server className="h-3 w-3" /> Base URL</span>
                        <input className="fx-control h-9 w-full px-3 text-xs text-[var(--fx-text)] outline-none" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="Provider default" />
                      </label>
                    ) : null}
                    <label>
                      <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-[.1em] text-[var(--fx-text-muted)]">Default model</span>
                      <input className="fx-control h-9 w-full px-3 text-xs text-[var(--fx-text)] outline-none" value={defaultModel} onChange={(event) => setDefaultModel(event.target.value)} />
                    </label>
                  </div>

                  <div className="mt-4 flex flex-wrap justify-end gap-2">
                    {selectedProvider.custom ? (
                      <button className="fx-control flex h-8 items-center gap-1.5 px-3 text-xs text-[var(--fx-error)]" onClick={() => void deleteProvider()} disabled={Boolean(busy)}>
                        {busy === "delete-provider" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />} Remove
                      </button>
                    ) : null}
                    <button className="fx-control flex h-8 items-center gap-1.5 px-3 text-xs text-[var(--fx-text)]" onClick={() => void testProvider()} disabled={Boolean(busy) || (!externallyAuthenticated && !selectedProvider.configured && !selectedProvider.local)}>
                      {busy === "test" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <TestTube2 className="h-3.5 w-3.5" />} {externallyAuthenticated ? "Check readiness" : "Test"}
                    </button>
                    <button className="flex h-8 items-center gap-1.5 rounded bg-[var(--fx-accent)] px-3 text-xs font-medium text-white disabled:opacity-50" onClick={() => void saveProvider()} disabled={Boolean(busy)}>
                      {busy === "save-provider" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save provider
                    </button>
                  </div>
                </section>

                <section className="rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-4">
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <div className="text-xs font-semibold text-[var(--fx-text)]">Active model</div>
                      <div className="mt-1 text-[10px] text-[var(--fx-text-muted)]">This is the persisted selection; hidden process environment values do not replace it at request time.</div>
                    </div>
                    <button className="fx-control flex h-8 items-center gap-1.5 px-2.5 text-xs text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]" onClick={() => void refreshModels()} disabled={Boolean(busy)}>
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
                      <span className="mb-1.5 block text-[10px] font-medium uppercase tracking-[.1em] text-[var(--fx-text-muted)]">{selectedProvider.display_name} model ID</span>
                      <input className="fx-control h-9 w-full px-3 text-xs text-[var(--fx-text)] outline-none" list={`models-${providerId}`} value={modelId} onChange={(event) => setModelId(event.target.value)} placeholder={selectedProvider.default_model} />
                      <datalist id={`models-${providerId}`}>{models.map((model) => <option key={model.model_id} value={model.model_id}>{model.display_name}</option>)}</datalist>
                    </label>
                  </div>

                  {modelsError ? <p className="mt-2 text-[10px] text-[var(--fx-warning)]">{modelsError}</p> : null}

                  <div className="mt-4 rounded-lg border border-[var(--fx-border)] bg-[var(--fx-input)] p-3 text-xs text-[var(--fx-code-text)]">
                    <strong>Deterministic model execution</strong>
                    <p className="mt-1 text-[10px] text-[var(--fx-text-muted)]">ForgeX keeps this provider/model selected. If it is rate limited or unavailable, the run pauses until you retry or explicitly choose another model.</p>
                  </div>

                  <div className="mt-4 flex justify-end">
                    <button className="flex h-8 items-center gap-1.5 rounded bg-[var(--fx-accent)] px-3 text-xs font-medium text-white disabled:opacity-50" onClick={() => void saveSelection()} disabled={Boolean(busy) || !modelId.trim()}>
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

        <section className="rounded-xl border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs font-semibold text-[var(--fx-text)]">Agent role models</div>
              <p className="mt-1 max-w-2xl text-[10px] leading-4 text-[var(--fx-text-muted)]">
                Bind specialist roles to explicit models. Unbound roles inherit the main selection; ForgeX never switches providers silently after a failure.
              </p>
            </div>
            {roleBusy === "load" ? <Loader2 className="h-4 w-4 animate-spin text-[var(--fx-text-muted)]" aria-label="Loading role bindings" /> : null}
          </div>

          <div className="mt-4 divide-y divide-[var(--fx-border)]">
            {AGENT_ROLES.map((option) => {
              const binding = roleBindings[option.role];
              const draft = roleDrafts[option.role] ?? {
                providerId: codeRoute?.provider_id ?? providers[0]?.provider_id ?? "",
                modelId: codeRoute?.model_id ?? providers[0]?.default_model ?? "",
              };
              const provider = providers.find((item) => item.provider_id === draft.providerId);
              const roleModels = draft.providerId === providerId ? models : [];
              return (
                <div key={option.role} className="grid gap-3 py-3 first:pt-0 last:pb-0 lg:grid-cols-[180px_minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-end">
                  <div className="self-center">
                    <div className="text-xs font-medium text-[var(--fx-text)]">{option.label}</div>
                    <div className="mt-0.5 text-[9px] leading-3 text-[var(--fx-text-muted)]">{option.description}</div>
                    <div className="mt-1 text-[9px] text-[var(--fx-info)]">{roleBindingSource(binding?.source)}</div>
                  </div>
                  <label>
                    <span className="mb-1 block text-[9px] font-medium uppercase tracking-[.1em] text-[var(--fx-text-muted)]">Provider</span>
                    <select
                      className="fx-control h-9 w-full px-2 text-xs text-[var(--fx-text)] outline-none"
                      value={draft.providerId}
                      onChange={(event) => {
                        const next = providers.find((item) => item.provider_id === event.target.value);
                        updateRoleDraft(option.role, {
                          providerId: event.target.value,
                          modelId: next?.default_model ?? "",
                        });
                      }}
                      disabled={Boolean(roleBusy)}
                    >
                      {providers.map((item) => <option key={item.provider_id} value={item.provider_id}>{item.display_name}</option>)}
                    </select>
                  </label>
                  <label>
                    <span className="mb-1 block text-[9px] font-medium uppercase tracking-[.1em] text-[var(--fx-text-muted)]">Model ID</span>
                    <input
                      className="fx-control h-9 w-full px-3 text-xs text-[var(--fx-text)] outline-none"
                      list={`role-models-${option.role}`}
                      value={draft.modelId}
                      onChange={(event) => updateRoleDraft(option.role, { modelId: event.target.value })}
                      placeholder={provider?.default_model ?? "Model ID"}
                      disabled={Boolean(roleBusy)}
                    />
                    <datalist id={`role-models-${option.role}`}>{roleModels.map((item) => <option key={item.model_id} value={item.model_id}>{item.display_name}</option>)}</datalist>
                  </label>
                  <div className="flex gap-2 lg:justify-end">
                    <button
                      className="fx-control h-8 px-2.5 text-[10px] text-[var(--fx-text-muted)] disabled:opacity-40"
                      onClick={() => void clearRoleBinding(option.role)}
                      disabled={Boolean(roleBusy) || binding?.source !== "role"}
                    >
                      Inherit main
                    </button>
                    <button
                      className="flex h-8 items-center gap-1.5 rounded bg-[var(--fx-accent)] px-3 text-[10px] font-medium text-white disabled:opacity-50"
                      onClick={() => void saveRoleBinding(option.role)}
                      disabled={Boolean(roleBusy) || !draft.providerId || !draft.modelId.trim()}
                    >
                      {roleBusy === option.role ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {message ? <div className="flex items-center gap-2 rounded-lg border border-[var(--fx-success)] bg-[var(--fx-success-soft)] px-3 py-2 text-xs text-[var(--fx-success)]"><CheckCircle2 className="h-3.5 w-3.5" />{message}</div> : null}
        {error ? <div className="flex items-center gap-2 rounded-lg border border-[var(--fx-error)] bg-[var(--fx-error-soft)] px-3 py-2 text-xs text-[var(--fx-error)]"><WifiOff className="h-3.5 w-3.5" />{error}</div> : null}
      </div>
    </div>
  );
}

function ProviderDot({ provider }: { provider: ModelProviderResponse }) {
  const status = providerPresentationStatus(provider);
  const ready = status === "ready";
  return <span className={`h-2 w-2 shrink-0 rounded-full ${ready ? "bg-[var(--fx-success)]" : ["rate_limited", "quota_exhausted", "degraded"].includes(status) ? "bg-[var(--fx-warning)]" : "bg-[var(--fx-text-muted)]"}`} aria-label={providerStatusLabel(status)} />;
}
