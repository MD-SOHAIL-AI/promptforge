import type { ConsoleEntry, ForgeXSettingSchemaItem, ForgeXSettingValue, ModelProviderResponse, ModelRouteResponse, ProjectResponse } from "@/types";

export type SettingsCategory =
  | "general"
  | "appearance"
  | "models"
  | "editor"
  | "terminal"
  | "workspace"
  | "hardware"
  | "security"
  | "about";

export interface SettingsSectionProps {
  settings: Record<string, ForgeXSettingValue>;
  schema: Record<string, ForgeXSettingSchemaItem>;
  saving: boolean;
  onUpdate: (key: string, value: ForgeXSettingValue) => Promise<Record<string, ForgeXSettingValue>>;
}

export interface ModelSettingsSectionProps {
  providers: ModelProviderResponse[];
  routes: ModelRouteResponse[];
  activeProject?: ProjectResponse | null;
  onRefresh: () => Promise<void>;
  onLog?: (entry: Omit<ConsoleEntry, "id" | "timestamp">) => void;
}
