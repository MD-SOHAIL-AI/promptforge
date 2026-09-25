"use client";

import { ForgeXTerminal } from "@/components/terminal/forgex-terminal";
import type { ProjectResponse } from "@/types";

interface IntegratedTerminalProps {
  activeProject: ProjectResponse | null;
}

export function IntegratedTerminal({ activeProject }: IntegratedTerminalProps) {
  return <ForgeXTerminal activeProject={activeProject} />;
}
