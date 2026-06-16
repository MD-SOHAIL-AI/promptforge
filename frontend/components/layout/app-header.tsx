import {
  Box,
  ChevronDown,
  CircleCheck,
  Cloud,
  Cpu,
  GitBranch,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import type { HealthResponse, ProjectResponse } from "@/types";

interface AppHeaderProps {
  health: HealthResponse | null;
  activeProject: ProjectResponse | null;
  isExecuting: boolean;
  buildStatus: string | null;
}

export function AppHeader({ health, activeProject, isExecuting, buildStatus }: AppHeaderProps) {
  const healthy = health?.status === "healthy";

  return (
    <header className="flex h-14 shrink-0 items-center border-b border-border bg-[#181b20] px-3">
      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-[5px] bg-[#e86f3d] text-white shadow-sm">
          <Cpu className="h-4 w-4" strokeWidth={2.25} />
        </div>
        <div className="mr-2 leading-none">
          <div className="text-[13px] font-semibold tracking-tight text-[#f0f2f5]">PromptForge</div>
          <div className="mt-1 font-mono text-[9px] uppercase tracking-[0.16em] text-muted-foreground">Embedded IDE</div>
        </div>
        <Separator orientation="vertical" className="mx-1 h-6" />
        <Button variant="ghost" size="sm" className="min-w-0 gap-2 text-foreground">
          <Box className="h-3.5 w-3.5 text-[#8b949e]" />
          <span className="max-w-48 truncate">{activeProject?.project_name ?? "reference/weather-station"}</span>
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        </Button>
        <div className="hidden items-center gap-1.5 text-[11px] text-muted-foreground lg:flex">
          <GitBranch className="h-3.5 w-3.5" />
          <span>main</span>
        </div>
      </div>

      <div className="flex items-center gap-1.5">
        <Badge className="hidden gap-1.5 normal-case text-[#aeb6c2] md:flex">
          <Cloud className="h-3 w-3" />
          OpenRouter
          <span className="text-[#666f7c]">/</span>
          gpt-oss-120b
        </Badge>
        <div className="flex h-7 items-center gap-2 rounded-[4px] px-2 text-[11px] text-muted-foreground">
          <span className={`h-1.5 w-1.5 rounded-full ${healthy ? "bg-[#3fb950]" : "bg-[#d29922]"}`} />
          <span className="hidden sm:inline">{health ? `API ${health.status}` : "API offline"}</span>
        </div>
        <div className="flex h-7 items-center gap-1.5 rounded-[4px] border border-border bg-[#1d2127] px-2 text-[11px]">
          {isExecuting ? (
            <LoaderCircle className="h-3.5 w-3.5 animate-spin text-[#58a6ff]" />
          ) : buildStatus === "failed" ? (
            <TriangleAlert className="h-3.5 w-3.5 text-[#f85149]" />
          ) : (
            <CircleCheck className="h-3.5 w-3.5 text-[#3fb950]" />
          )}
          <span className="text-[#c4cad2]">{isExecuting ? "Running" : buildStatus ?? "Ready"}</span>
        </div>
      </div>
    </header>
  );
}
