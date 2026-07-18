import { CheckCircle2, Loader2 } from "lucide-react";

import type { ModelInfoResponse, ModelProviderResponse } from "@/types";
import type { BusyKey, RouteFormState } from "./types";

const TASK_TYPES = ["code_generation", "planning", "debugging", "documentation", "serial_analysis", "general_chat"];

interface TaskRouteEditorProps {
  routeForms: Record<string, RouteFormState>;
  providers: ModelProviderResponse[];
  providerById: Record<string, ModelProviderResponse>;
  modelsByProvider: Record<string, ModelInfoResponse[]>;
  busy: BusyKey;
  updateRouteForm: (taskType: string, patch: Partial<RouteFormState>) => void;
  saveRoute: (taskType: string) => Promise<void>;
}

export function TaskRouteEditor({ routeForms, providers, providerById, modelsByProvider, busy, updateRouteForm, saveRoute }: TaskRouteEditorProps) {
  return (
    <section className="space-y-2">
      <div className="text-xs font-semibold uppercase text-[var(--fx-text-muted)]">Task Routes</div>
      {TASK_TYPES.map((taskType) => {
        const form = routeForms[taskType] ?? {
          providerId: "openrouter",
          modelId: providerById.openrouter?.default_model ?? "",
          fallbackEnabled: true,
          fallbackProviderId: "",
          localOnly: false,
        };
        const providerModels = modelsByProvider[form.providerId] ?? [];
        return (
          <div key={taskType} className="min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] p-3">
            <div className="mb-2 break-words text-xs font-medium text-[var(--fx-text)]">{taskType}</div>
            <div className="grid gap-2 text-xs">
              <select className="h-8 min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-[var(--fx-text)] outline-none" value={form.providerId} onChange={(event) => updateRouteForm(taskType, { providerId: event.target.value })}>
                {providers.map((provider) => <option key={provider.provider_id} value={provider.provider_id}>{provider.display_name}</option>)}
              </select>
              <input className="h-8 min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-[var(--fx-text)] outline-none" value={form.modelId} list={`route-models-${taskType}`} onChange={(event) => updateRouteForm(taskType, { modelId: event.target.value })} placeholder="Model ID" />
              <datalist id={`route-models-${taskType}`}>
                {providerModels.map((model) => <option key={model.model_id} value={model.model_id} />)}
              </datalist>
              {form.fallbackEnabled ? (
                <select className="h-8 min-w-0 rounded border border-[var(--fx-border)] bg-[var(--fx-input)] px-2 text-[var(--fx-text)] outline-none" value={form.fallbackProviderId} onChange={(event) => updateRouteForm(taskType, { fallbackProviderId: event.target.value })} aria-label={`${taskType} fallback provider`}>
                  <option value="">No fallback provider</option>
                  {providers.filter((candidate) => candidate.provider_id !== form.providerId && candidate.local === Boolean(providerById[form.providerId]?.local) && candidate.configured && candidate.enabled).map((candidate) => (
                    <option key={candidate.provider_id} value={candidate.provider_id}>{candidate.display_name}</option>
                  ))}
                </select>
              ) : null}
            </div>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
              <label className="flex items-center gap-1 text-[11px] text-[var(--fx-code-text)]">
                <input type="checkbox" checked={form.fallbackEnabled} onChange={(event) => updateRouteForm(taskType, { fallbackEnabled: event.target.checked })} />
                Fallback
              </label>
              <label className="flex items-center gap-1 text-[11px] text-[var(--fx-code-text)]">
                <input type="checkbox" checked={form.localOnly} onChange={(event) => updateRouteForm(taskType, { localOnly: event.target.checked })} />
                Local only
              </label>
              <button className="flex h-8 items-center gap-1 rounded border border-[var(--fx-border)] bg-[var(--fx-panel)] px-2 text-xs text-[var(--fx-code-text)] hover:bg-[var(--fx-hover)] disabled:opacity-50" onClick={() => void saveRoute(taskType)} disabled={Boolean(busy)}>
                {busy === `route:${taskType}` ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                Save route
              </button>
            </div>
          </div>
        );
      })}
    </section>
  );
}
