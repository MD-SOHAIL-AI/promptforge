"use client";

import { Check, ChevronDown, Cloud, Cpu, LoaderCircle, Search, Zap } from "lucide-react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import type { ModelInfoResponse, ModelProviderResponse, ModelRouteResponse, ProviderModelsResponse } from "@/types";

export interface ChatModelPickerProps {
  providers: ModelProviderResponse[];
  activeRoute?: ModelRouteResponse | null;
  loadProviderModels: (providerId: string) => Promise<ProviderModelsResponse>;
  onSelect: (providerId: string, modelId: string) => Promise<void> | void;
  compact?: boolean;
  disabled?: boolean;
}

type CatalogState = {
  loading: boolean;
  models: ModelInfoResponse[];
  source?: string;
  error?: string | null;
};

type ModelGroup = "free" | "paid" | "local";

const groupMeta: Record<ModelGroup, { label: string; detail: string; icon: typeof Zap }> = {
  free: { label: "Free", detail: "No model charge detected", icon: Zap },
  paid: { label: "Paid", detail: "Uses provider billing", icon: Cloud },
  local: { label: "Local", detail: "Runs on this machine or LAN", icon: Cpu },
};

function modelGroup(model: ModelInfoResponse, provider?: ModelProviderResponse): ModelGroup {
  if (model.local || provider?.local) return "local";
  if (model.free === true || model.model_id.endsWith(":free")) return "free";
  return "paid";
}

function modelLabel(modelId: string, displayName?: string) {
  const label = displayName?.trim() || modelId;
  return label === modelId ? modelId.split("/").at(-1) ?? modelId : label;
}

function fallbackModels(provider: ModelProviderResponse, activeRoute?: ModelRouteResponse | null): ModelInfoResponse[] {
  const ids = new Set<string>();
  if (activeRoute?.provider_id === provider.provider_id && activeRoute.model_id) ids.add(activeRoute.model_id);
  if (provider.default_model) ids.add(provider.default_model);
  return Array.from(ids).map((modelId) => ({
    provider_id: provider.provider_id,
    model_id: modelId,
    display_name: modelLabel(modelId),
    free: modelId.endsWith(":free"),
    local: provider.local,
  }));
}

export function ChatModelPicker({ providers, activeRoute, loadProviderModels, onSelect, compact = false, disabled = false }: ChatModelPickerProps) {
  const availableProviders = useMemo(() => providers.filter((provider) => provider.enabled || provider.configured || provider.local), [providers]);
  const activeProvider = useMemo(
    () => availableProviders.find((provider) => provider.provider_id === activeRoute?.provider_id) ?? availableProviders[0] ?? null,
    [activeRoute?.provider_id, availableProviders],
  );
  const [open, setOpen] = useState(false);
  const [selectedProviderId, setSelectedProviderId] = useState<string | null>(activeProvider?.provider_id ?? null);
  const [catalogs, setCatalogs] = useState<Record<string, CatalogState>>({});
  const [query, setQuery] = useState("");
  const [saving, setSaving] = useState(false);
  const [selectError, setSelectError] = useState<string | null>(null);
  const [highlighted, setHighlighted] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setSelectedProviderId((current) => current ?? activeProvider?.provider_id ?? null);
  }, [activeProvider?.provider_id]);

  const selectedProvider = useMemo(
    () => availableProviders.find((provider) => provider.provider_id === selectedProviderId) ?? activeProvider,
    [activeProvider, availableProviders, selectedProviderId],
  );

  useEffect(() => {
    if (!open || !selectedProvider || catalogs[selectedProvider.provider_id]?.loading || catalogs[selectedProvider.provider_id]?.models.length) return;
    setCatalogs((current) => ({
      ...current,
      [selectedProvider.provider_id]: {
        loading: true,
        models: current[selectedProvider.provider_id]?.models ?? [],
        source: current[selectedProvider.provider_id]?.source,
        error: null,
      },
    }));
    loadProviderModels(selectedProvider.provider_id)
      .then((response) => {
        setCatalogs((current) => ({
          ...current,
          [selectedProvider.provider_id]: {
            loading: false,
            models: Array.isArray(response.models) ? response.models : [],
            source: response.source,
            error: response.error,
          },
        }));
      })
      .catch((error: unknown) => {
        setCatalogs((current) => ({
          ...current,
          [selectedProvider.provider_id]: {
            loading: false,
            models: fallbackModels(selectedProvider, activeRoute),
            source: "fallback",
            error: error instanceof Error ? error.message : "Could not fetch model list.",
          },
        }));
      });
  }, [activeRoute, catalogs, loadProviderModels, open, selectedProvider]);

  useEffect(() => {
    if (open) {
      setHighlighted(0);
      window.setTimeout(() => searchRef.current?.focus(), 0);
    }
  }, [open]);

  useEffect(() => {
    rootRef.current?.querySelector(`[data-model-index="${highlighted}"]`)?.scrollIntoView({ block: "nearest" });
  }, [highlighted]);

  if (!activeProvider || !availableProviders.length || !activeRoute) return null;

  const activeProviderName = activeProvider.display_name || activeProvider.provider_id;
  const activeModelId = activeRoute.model_id || activeProvider.default_model;
  const catalog = selectedProvider ? catalogs[selectedProvider.provider_id] : undefined;
  const rawModels = selectedProvider
    ? (catalog?.models.length ? catalog.models : fallbackModels(selectedProvider, activeRoute))
    : [];
  const filteredModels = rawModels.filter((model) => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return `${model.model_id} ${model.display_name}`.toLowerCase().includes(needle);
  });
  const grouped = (["local", "free", "paid"] as ModelGroup[])
    .map((group) => ({
      group,
      models: filteredModels.filter((model) => modelGroup(model, selectedProvider) === group),
    }))
    .filter((item) => item.models.length)
    .reduce<Array<{ group: ModelGroup; models: ModelInfoResponse[]; startIndex: number }>>((acc, item) => {
      const startIndex = acc.length ? acc[acc.length - 1].startIndex + acc[acc.length - 1].models.length : 0;
      acc.push({ ...item, startIndex });
      return acc;
    }, []);
  const flatModels = grouped.flatMap((section) => section.models);
  const catalogLoading = Boolean(catalog?.loading) && !rawModels.length;

  const choose = async (model: ModelInfoResponse) => {
    if (!selectedProvider || model.agent_compatible === false) return;
    setSaving(true);
    setSelectError(null);
    try {
      await onSelect(selectedProvider.provider_id, model.model_id);
      setOpen(false);
      setQuery("");
    } catch (error) {
      setSelectError(error instanceof Error ? error.message : "Model selection could not be saved.");
    } finally {
      setSaving(false);
    }
  };

  const moveHighlight = (delta: number) => {
    if (!flatModels.length) return;
    setHighlighted((current) => (current + delta + flatModels.length) % flatModels.length);
  };

  const handleMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveHighlight(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      moveHighlight(-1);
    } else if (event.key === "Home") {
      event.preventDefault();
      setHighlighted(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setHighlighted(Math.max(0, flatModels.length - 1));
    } else if (event.key === "Enter" && event.target === searchRef.current) {
      const model = flatModels[highlighted];
      if (model) {
        event.preventDefault();
        void choose(model);
      }
    }
  };

  const modelRowClass = (active: boolean, index: number) =>
    `relative flex w-full items-center gap-2 rounded-lg px-2.5 py-2 pl-3 text-left hover:bg-[var(--fx-hover)] disabled:cursor-not-allowed disabled:opacity-60 ${active ? "bg-[var(--fx-accent-faint)]" : ""} ${index === highlighted ? "ring-1 ring-inset ring-[color-mix(in_srgb,var(--fx-accent)_45%,transparent)]" : ""}`;

  return (
    <div
      ref={rootRef}
      className="relative min-w-0"
      onBlur={(event) => {
        if (rootRef.current?.contains(event.relatedTarget as Node | null)) return;
        setOpen(false);
      }}
    >
      <button
        type="button"
        disabled={disabled || saving}
        onClick={() => {
          setOpen((value) => !value);
          setSelectedProviderId(activeProvider.provider_id);
        }}
        className={`flex min-w-0 max-w-[240px] items-center gap-1.5 rounded-lg px-2 py-1 text-[10px] font-medium text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-50 ${compact ? "max-w-[180px]" : ""}`}
        title={`${activeProviderName} · ${activeModelId}`}
      >
        {activeProvider.local ? <Cpu className="h-3.5 w-3.5 text-[var(--fx-info)]" /> : <Cloud className="h-3.5 w-3.5 text-[var(--fx-accent)]" />}
        <span className="hidden shrink-0 text-[9px] uppercase tracking-[.12em] sm:inline">Model</span>
        <span className="min-w-0 truncate font-mono">{activeModelId}</span>
        {saving ? <LoaderCircle className="h-3 w-3 animate-spin" /> : <ChevronDown className="h-3 w-3 shrink-0" />}
      </button>

      {open ? (
        <div className="fx-composer-menu fx-model-picker-menu bottom-9 left-0 w-[min(88vw,660px)]" role="dialog" aria-label="Chat model picker" onKeyDown={handleMenuKeyDown}>
          <div className="shrink-0 border-b border-[var(--fx-border-soft)] px-3 py-2">
            <div className="text-[9px] font-semibold uppercase tracking-[.16em] text-[var(--fx-text-muted)]">Chat model</div>
            <div className="mt-1 truncate text-[11px] text-[var(--fx-text)]">{activeProviderName} · <span className="font-mono">{activeModelId}</span></div>
          </div>
          <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden sm:grid-cols-[190px_minmax(0,1fr)]">
            <div className="min-h-0 border-b border-[var(--fx-border-soft)] p-2 sm:border-b-0 sm:border-r">
              <div className="mb-1 px-2 text-[9px] font-semibold uppercase tracking-[.14em] text-[var(--fx-text-muted)]">Provider</div>
              <div role="listbox" aria-label="Model providers" className="fx-model-picker-list max-h-[136px] space-y-1 pr-1 sm:max-h-none">
                {availableProviders.map((provider) => {
                  const active = provider.provider_id === selectedProvider?.provider_id;
                  return (
                    <button
                      key={provider.provider_id}
                      type="button"
                      role="option"
                      aria-selected={active}
                      onClick={() => {
                        setSelectedProviderId(provider.provider_id);
                        setQuery("");
                        setHighlighted(0);
                      }}
                      className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left outline-none hover:bg-[var(--fx-hover)] focus-visible:bg-[var(--fx-hover)] ${active ? "bg-[var(--fx-accent-faint)] text-[var(--fx-text)]" : "text-[var(--fx-text-muted)]"}`}
                    >
                      {provider.local ? <Cpu className="h-3.5 w-3.5 shrink-0 text-[var(--fx-info)]" /> : <Cloud className="h-3.5 w-3.5 shrink-0 text-[var(--fx-accent)]" />}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-[10px] font-semibold">{provider.display_name}</span>
                        <span className="mt-0.5 block truncate text-[8px] uppercase tracking-[.1em]">{provider.configured || provider.local ? "available" : "not configured"}</span>
                      </span>
                      {provider.provider_id === activeRoute.provider_id ? <Check className="h-3 w-3 shrink-0 text-[var(--fx-success)]" /> : null}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="flex min-h-0 flex-col overflow-hidden p-2">
              <label className="mb-2 flex h-9 items-center gap-2 rounded-lg border border-[var(--fx-border-soft)] bg-[var(--fx-panel-elevated)] px-2.5">
                <Search className="h-3.5 w-3.5 text-[var(--fx-text-muted)]" />
                <input
                  ref={searchRef}
                  value={query}
                  onChange={(event) => {
                    setQuery(event.target.value);
                    setHighlighted(0);
                  }}
                  placeholder="Search models..."
                  className="min-w-0 flex-1 bg-transparent text-[11px] text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)]"
                />
              </label>
              {catalog?.error ? <div className="mb-2 rounded-lg border border-[color-mix(in_srgb,var(--fx-warning)_30%,var(--fx-border))] bg-[var(--fx-warning-soft)] px-2.5 py-2 text-[9px] leading-4 text-[var(--fx-text-muted)]">{catalog.error}</div> : null}
              {selectError ? <div className="mb-2 rounded-lg border border-[color-mix(in_srgb,var(--fx-error)_34%,var(--fx-border))] bg-[var(--fx-error-soft)] px-2.5 py-2 text-[9px] leading-4 text-[var(--fx-error)]">{selectError}</div> : null}
              <div className="fx-model-picker-list min-h-0 flex-1 pr-1" role="listbox" aria-label="Models">
                {catalogLoading ? (
                  <div className="space-y-2 py-1" aria-hidden>
                    {[0, 1, 2, 3, 4].map((row) => <Skeleton key={row} className="h-[46px] w-full rounded-lg" />)}
                  </div>
                ) : grouped.length ? grouped.map(({ group, models, startIndex }) => {
                  const meta = groupMeta[group];
                  const Icon = meta.icon;
                  return (
                    <section key={group} className="mb-3 last:mb-0">
                      <div className="sticky top-0 z-10 flex items-center gap-2 bg-[color-mix(in_srgb,var(--fx-panel)_96%,transparent)] px-1 py-1.5">
                        <Icon className="h-3.5 w-3.5 text-[var(--fx-accent)]" />
                        <span className="text-[9px] font-semibold uppercase tracking-[.14em] text-[var(--fx-text-muted)]">{meta.label}</span>
                        <span className="text-[8px] text-[var(--fx-text-muted)]">{models.length}</span>
                        <span className="ml-auto hidden text-[8px] text-[var(--fx-text-muted)] md:inline">{meta.detail}</span>
                      </div>
                      <div className="space-y-1">
                        {models.map((model, offset) => {
                          const index = startIndex + offset;
                          const active = model.provider_id === activeRoute.provider_id && model.model_id === activeRoute.model_id;
                          return (
                            <button
                              key={`${model.provider_id}:${model.model_id}`}
                              type="button"
                              role="option"
                              aria-selected={active}
                              data-model-index={index}
                              disabled={saving || model.agent_compatible === false}
                              title={model.agent_compatible === false ? "This model does not support Forge Agent structured output or tool calling." : undefined}
                              onMouseMove={() => setHighlighted(index)}
                              onClick={() => void choose(model)}
                              className={modelRowClass(active, index)}
                            >
                              {active ? <span aria-hidden className="absolute inset-y-1.5 left-0 w-[2.5px] rounded-full bg-[var(--fx-accent)] shadow-[0_0_8px_var(--fx-glow)]" /> : null}
                              <span className="min-w-0 flex-1">
                                <span className="block truncate text-[11px] font-semibold text-[var(--fx-text)]">{modelLabel(model.model_id, model.display_name)}</span>
                                <span className="mt-0.5 block truncate font-mono text-[9px] text-[var(--fx-text-muted)]">{model.model_id}</span>
                              </span>
                              {model.context_window ? <span className="hidden rounded-md border border-[var(--fx-border-soft)] px-1.5 py-0.5 text-[8px] text-[var(--fx-text-muted)] sm:inline">{Math.round(model.context_window / 1000)}k</span> : null}
                              {model.agent_compatible === false ? <span className="shrink-0 text-[8px] font-medium text-[var(--fx-warning)]">Chat only</span> : null}
                              {active ? <Check className="h-3.5 w-3.5 shrink-0 text-[var(--fx-success)]" /> : null}
                            </button>
                          );
                        })}
                      </div>
                    </section>
                  );
                }) : <div className="rounded-xl border border-dashed border-[var(--fx-border-soft)] p-5 text-center text-[10px] text-[var(--fx-text-muted)]">No models match this provider/search.</div>}
              </div>
              <div className="mt-2 border-t border-[var(--fx-border-soft)] pt-2 text-[8px] text-[var(--fx-text-muted)]">
                {catalog?.source === "provider" ? "Live provider catalog" : "Fallback catalog"} · free models are detected from provider metadata or OpenRouter <span className="font-mono">:free</span> IDs.
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
