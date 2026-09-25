"use client";

import { useCallback, useRef, useState } from "react";
import { promptForgeApi } from "@/lib/api";
import type { ComposerModelCatalog } from "@/lib/composer-model-options";
import type { ModelProviderResponse, ModelRouteResponse } from "@/types";

export type ModelCatalogState = "idle" | "loading" | "live" | "cached" | "error";

export function useModelRuntimeState() {
  const loading = useRef(new Set<string>());
  const [providers, setProviders] = useState<ModelProviderResponse[]>([]);
  const [routes, setRoutes] = useState<ModelRouteResponse[]>([]);
  const [catalog, setCatalog] = useState<ComposerModelCatalog>({});
  const [catalogState, setCatalogState] = useState<Record<string, ModelCatalogState>>({});
  const [selecting, setSelecting] = useState(false);

  const refresh = useCallback(async () => {
    const [providerResponse, routeResponse] = await Promise.all([
      promptForgeApi.modelProviders(),
      promptForgeApi.modelRoutes(),
    ]);
    setProviders(providerResponse.providers);
    setRoutes(routeResponse.routes);
    return { providers: providerResponse.providers, routes: routeResponse.routes };
  }, []);

  const loadModels = useCallback(async (providerId: string) => {
    if (!providerId || loading.current.has(providerId)) return;
    loading.current.add(providerId);
    setCatalogState((current) => ({ ...current, [providerId]: "loading" }));
    try {
      const result = await promptForgeApi.providerModels(providerId);
      setCatalog((current) => ({ ...current, [providerId]: result.models ?? [] }));
      setCatalogState((current) => ({
        ...current,
        [providerId]: result.error ? "cached" : result.source === "live" ? "live" : "cached",
      }));
    } catch {
      setCatalog((current) => ({ ...current, [providerId]: current[providerId] ?? [] }));
      setCatalogState((current) => ({ ...current, [providerId]: "error" }));
    } finally {
      loading.current.delete(providerId);
    }
  }, []);

  const saveDefaultSelection = useCallback(async (providerId: string, modelId: string) => {
    setSelecting(true);
    try {
      await promptForgeApi.saveModelSelection({ provider_id: providerId, model_id: modelId });
      await refresh();
    } finally {
      setSelecting(false);
    }
  }, [refresh]);

  return {
    providers,
    routes,
    catalog,
    catalogState,
    selecting,
    refresh,
    loadModels,
    saveDefaultSelection,
  };
}
