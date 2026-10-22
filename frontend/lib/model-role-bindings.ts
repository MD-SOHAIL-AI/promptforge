import type { ModelProviderResponse, ModelRoleBinding } from "@/types";

export interface ModelRoleDraft {
  providerId: string;
  modelId: string;
}

export function splitModelReference(
  model: string | null | undefined,
  providers: ModelProviderResponse[],
  fallbackProviderId: string,
  fallbackModelId: string,
): ModelRoleDraft {
  if (model) {
    // Provider IDs are controlled identifiers, while model IDs may contain any
    // number of slashes. Match a known provider prefix instead of splitting.
    const provider = [...providers]
      .sort((left, right) => right.provider_id.length - left.provider_id.length)
      .find((item) => model.startsWith(`${item.provider_id}/`));
    if (provider) {
      return {
        providerId: provider.provider_id,
        modelId: model.slice(provider.provider_id.length + 1),
      };
    }
  }
  return { providerId: fallbackProviderId, modelId: fallbackModelId };
}

export function roleBindingSource(source: ModelRoleBinding["source"] | undefined) {
  if (source === "role") return "Role override";
  if (source === "application") return "Inherits application model";
  if (source === "task_route") return "Inherits task route";
  if (source === "session_role") return "Session role override";
  if (source === "session") return "Inherits session model";
  return "Loading binding";
}
