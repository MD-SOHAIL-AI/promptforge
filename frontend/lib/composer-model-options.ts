import type { ModelInfoResponse, ModelProviderResponse, ModelRouteResponse } from "@/types";

export interface ComposerModelOption {
  key: string;
  providerId: string;
  modelId: string;
  label: string;
  group: "Free" | "Paid" | "Other";
  agentCompatibility: "unverified" | "unsupported" | "unavailable";
}

export const COMPOSER_MODEL_GROUPS = ["Free", "Paid", "Other"] as const;

export type ComposerModelCatalog = Record<string, ModelInfoResponse[] | undefined>;

export function activeComposerProviderId(
  providers: ModelProviderResponse[] = [],
  routes: ModelRouteResponse[] = [],
): string {
  const codeRoute = selectedCodeGenerationRoute(routes);
  if (codeRoute?.provider_id) return codeRoute.provider_id;
  return composerProviderOptions(providers)[0]?.provider_id ?? "";
}

export function composerProviderOptions(providers: ModelProviderResponse[] = []): ModelProviderResponse[] {
  return providers.filter((provider) => provider.enabled && provider.configured);
}

export function composerModelOptions(
  providers: ModelProviderResponse[] = [],
  routes: ModelRouteResponse[] = [],
  catalog: ComposerModelCatalog = {},
  providerId = activeComposerProviderId(providers, routes),
): ComposerModelOption[] {
  const codeRoute = selectedCodeGenerationRoute(routes);
  const options = new Map<string, ComposerModelOption>();
  const provider = providers.find((item) => item.provider_id === providerId) ?? null;
  if (!provider?.enabled || !provider.configured) return [];
  const models = catalog[providerId] ?? [];

  if (codeRoute?.provider_id === providerId && codeRoute.model_id) {
    addOption(options, providerId, codeRoute.model_id, modelDisplayName(models, codeRoute.model_id), modelGroup(models, codeRoute.model_id), modelCompatibility(models, codeRoute.model_id));
  }

  if (provider?.default_model) {
    addOption(options, providerId, provider.default_model, modelDisplayName(models, provider.default_model), modelGroup(models, provider.default_model), modelCompatibility(models, provider.default_model));
  }

  for (const model of models) {
    if (!model.model_id) continue;
    addOption(options, providerId, model.model_id, model.display_name || model.model_id, groupForModel(model), modelCompatibility(models, model.model_id));
  }

  return Array.from(options.values());
}

export function filterComposerModelOptions(
  options: ComposerModelOption[],
  query: string,
): ComposerModelOption[] {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return options;
  return options.filter((option) => {
    const searchable = `${option.label} ${option.modelId} ${option.group}`.toLocaleLowerCase();
    return terms.every((term) => searchable.includes(term));
  });
}

export function activeComposerModelKey(
  providers: ModelProviderResponse[] = [],
  routes: ModelRouteResponse[] = [],
  catalog: ComposerModelCatalog = {},
  providerId = activeComposerProviderId(providers, routes),
): string {
  const codeRoute = selectedCodeGenerationRoute(routes);
  if (codeRoute?.provider_id === providerId && codeRoute.model_id) return composerModelKey(codeRoute.provider_id, codeRoute.model_id);
  const first = composerModelOptions(providers, routes, catalog, providerId)[0];
  return first?.key ?? "";
}

export function parseComposerModelKey(value: string): { providerId: string; modelId: string } | null {
  const [providerId, ...modelParts] = value.split("::");
  const modelId = modelParts.join("::");
  if (!providerId || !modelId) return null;
  return { providerId, modelId };
}

function selectedCodeGenerationRoute(routes: ModelRouteResponse[]) {
  return routes.find((route) => route.task_type === "code_generation") ?? routes[0] ?? null;
}

function composerModelKey(providerId: string, modelId: string) {
  return `${providerId}::${modelId}`;
}

function addOption(options: Map<string, ComposerModelOption>, providerId: string, modelId: string, label: string, group: ComposerModelOption["group"], agentCompatibility: ComposerModelOption["agentCompatibility"]) {
  const key = composerModelKey(providerId, modelId);
  if (options.has(key)) return;
  options.set(key, { key, providerId, modelId, label, group, agentCompatibility });
}

function modelCompatibility(models: ModelInfoResponse[], modelId: string): ComposerModelOption["agentCompatibility"] {
  if (models.find((model) => model.model_id === modelId)?.availability === "retired") return "unavailable";
  return models.find((model) => model.model_id === modelId)?.capabilities?.text_generation === false
    ? "unsupported" : "unverified";
}

function modelDisplayName(models: ModelInfoResponse[], modelId: string) {
  return models.find((model) => model.model_id === modelId)?.display_name || modelId;
}

function modelGroup(models: ModelInfoResponse[], modelId: string): ComposerModelOption["group"] {
  const model = models.find((item) => item.model_id === modelId);
  if (model) return groupForModel(model);
  return modelId.endsWith(":free") ? "Free" : "Other";
}

function groupForModel(model: ModelInfoResponse): ComposerModelOption["group"] {
  if (model.free === true || model.model_id.endsWith(":free")) return "Free";
  if (model.free === false) return "Paid";
  return "Other";
}
