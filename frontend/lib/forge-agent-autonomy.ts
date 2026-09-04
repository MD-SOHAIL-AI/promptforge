export type ForgeAgentComposerAutonomy = "auto" | "staged_changes" | "plan_only";

export function composerAutonomy(autoMode: boolean, planMode = false): ForgeAgentComposerAutonomy {
  if (planMode) return "plan_only";
  return autoMode ? "auto" : "staged_changes";
}
