import { KeyRound, Loader2, RefreshCw, Save, ShieldOff, Wifi } from "lucide-react";
import type { MutableRefObject } from "react";

import type { ModelInfoResponse, ModelProviderResponse } from "@/types";
import type { BusyKey, ProviderFormState } from "./types";
import { healthClass, healthLabel, localHint, providerStatus } from "./utils";

interface ProviderCardListProps {
  providers: ModelProviderResponse[];
  providerForms: Record<string, ProviderFormState>;
  modelsByProvider: Record<string, ModelInfoResponse[]>;
  modelErrors: Record<string, string | null>;
  apiKeyInputs: MutableRefObject<Record<string, HTMLInputElement | null>>;
  busy: BusyKey;
  updateProviderForm: (providerId: string, patch: Partial<ProviderFormState>) => void;
  clearProviderKey: (provider: ModelProviderResponse) => Promise<void>;
  saveProvider: (provider: ModelProviderResponse) => Promise<void>;
  testProvider: (provider: ModelProviderResponse) => Promise<void>;
  refreshModels: (provider: ModelProviderResponse) => Promise<void>;
}

export function ProviderCardList({
  providers,
  providerForms,
  modelsByProvider,
  modelErrors,
  apiKeyInputs,
  busy,
  updateProviderForm,
  clearProviderKey,
  saveProvider,
  testProvider,
  refreshModels,
}: ProviderCardListProps) {
  return (
    <section className="space-y-2">
      <div className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">API Providers and Local Model Servers</div>
      {providers.length === 0 ? (
        <div className="rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3 text-sm text-[var(--fx-text-muted)]">Model router not configured</div>
      ) : (
        providers.map((provider) => {
          const form = providerForms[provider.provider_id] ?? {
            enabled: provider.enabled,
            baseUrl: provider.base_url ?? "",
            defaultModel: provider.default_model ?? "",
          };
          const discoveredModels = modelsByProvider[provider.provider_id] ?? [];
          const datalistId = `models-${provider.provider_id}`;
          const apiKeyRequired = provider.auth_type === "api_key" && form.enabled && !provider.configured;
          return (
            <div key={provider.provider_id} className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
              <div className="flex min-w-0 items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-[var(--fx-text)]">{provider.display_name}</div>
                  <div className="mt-1 flex flex-wrap gap-1 text-[11px]">
                    <span className="rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-1.5 py-0.5 text-[var(--fx-text-muted)]">{providerStatus(provider)}</span>
                    <span className={`rounded border px-1.5 py-0.5 ${healthClass(provider.health_status)}`}>{healthLabel(provider.health_status)}</span>
                  </div>
                </div>
                <label className="flex shrink-0 items-center gap-1 text-[11px] text-[var(--fx-code-text)]">
                  <input type="checkbox" checked={form.enabled} onChange={(event) => updateProviderForm(provider.provider_id, { enabled: event.target.checked })} />
                  Enabled
                </label>
              </div>

              <div className="mt-2 grid min-w-0 grid-cols-1 gap-2 text-xs xl:grid-cols-2">
                {provider.auth_type === "api_key" ? (
                  <label className="min-w-0 text-[var(--fx-text-muted)]">
                    API Key
                    <div className="mt-1 flex h-8 min-w-0 items-center gap-2 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2">
                      <KeyRound className="h-3.5 w-3.5 shrink-0 text-[var(--fx-text-muted)]" />
                      <input
                        ref={(element) => { apiKeyInputs.current[provider.provider_id] = element; }}
                        className="min-w-0 flex-1 bg-transparent text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)]"
                        type="password"
                        autoComplete="off"
                        placeholder={provider.credential_configured ? "Saved securely" : "Paste API key"}
                      />
                      {provider.credential_configured ? (
                        <button className="shrink-0 text-[var(--fx-text-muted)] hover:text-[var(--fx-error)]" onClick={() => void clearProviderKey(provider)} title="Clear saved API key">
                          <ShieldOff className="h-3.5 w-3.5" />
                        </button>
                      ) : null}
                    </div>
                    {apiKeyRequired ? <span className="mt-1 block text-[var(--fx-warning)]">API key required. Paste the key, then Save and Test.</span> : null}
                  </label>
                ) : null}

                <label className="min-w-0 text-[var(--fx-text-muted)]">
                  Default Model
                  <input className="mt-1 h-8 w-full min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-[var(--fx-text)] outline-none" value={form.defaultModel} list={datalistId} onChange={(event) => updateProviderForm(provider.provider_id, { defaultModel: event.target.value })} />
                  <datalist id={datalistId}>
                    {discoveredModels.map((model) => <option key={model.model_id} value={model.model_id} />)}
                  </datalist>
                </label>

                <label className="min-w-0 text-[var(--fx-text-muted)]">
                  Base URL
                  <input className="mt-1 h-8 w-full min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-[var(--fx-text)] outline-none" value={form.baseUrl} onChange={(event) => updateProviderForm(provider.provider_id, { baseUrl: event.target.value })} />
                </label>
              </div>

              <div className="mt-2 space-y-1 break-words text-[11px] text-[var(--fx-text-muted)]">
                <div>Default: <span className="text-[var(--fx-code-text)]">{provider.default_model || "None"}</span></div>
                {provider.base_url ? <div>Base URL: <span className="text-[var(--fx-code-text)]">{provider.base_url}</span></div> : null}
                <div>Last checked: <span className="text-[var(--fx-code-text)]">{provider.last_checked_at ?? "Never"}</span></div>
                {provider.last_error ? <div className="text-[var(--fx-error)]">Error: {provider.last_error}</div> : null}
                {modelErrors[provider.provider_id] ? <div className="text-[var(--fx-error)]">{modelErrors[provider.provider_id]}</div> : null}
                {provider.local && provider.health_status !== "connected" ? <div>{localHint(provider)}</div> : null}
              </div>

              <div className="mt-3 grid min-w-0 grid-cols-1 gap-2 md:grid-cols-3">
                <button className="flex h-8 items-center justify-center gap-1 rounded bg-[var(--fx-accent)] px-2 text-xs font-medium text-white disabled:opacity-50" onClick={() => void saveProvider(provider)} disabled={Boolean(busy)}>
                  {busy === `save:${provider.provider_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                  Save
                </button>
                <button className="flex h-8 items-center justify-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50" onClick={() => void testProvider(provider)} disabled={Boolean(busy)}>
                  {busy === `test:${provider.provider_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Wifi className="h-3.5 w-3.5" />}
                  Test
                </button>
                <button className="flex h-8 items-center justify-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50" onClick={() => void refreshModels(provider)} disabled={Boolean(busy)}>
                  {busy === `models:${provider.provider_id}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                  Models
                </button>
              </div>
            </div>
          );
        })
      )}
    </section>
  );
}
