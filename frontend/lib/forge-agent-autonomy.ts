export type ForgeAgentComposerAutonomy = "auto" | "read_only" | "staged_changes" | "plan_only";

export function composerAutonomy(autoMode: boolean, planMode = false): ForgeAgentComposerAutonomy {
  if (planMode) return "plan_only";
  return autoMode ? "auto" : "read_only";
}
