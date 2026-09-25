import { Check, Circle, LoaderCircle, X } from "lucide-react";

import { cn, formatDuration } from "@/lib/utils";
import type { WorkflowStage } from "@/types";

interface WorkflowTimelineProps {
  stages: WorkflowStage[];
}

const statusIcon = {
  pending: Circle,
  active: LoaderCircle,
  success: Check,
  failed: X,
  cancelled: X,
  blocked: Circle,
  skipped: Circle,
  waiting_for_device: Circle,
};

export function WorkflowTimeline({ stages }: WorkflowTimelineProps) {
  return (
    <section className="border-b border-border bg-[#1b1f24] px-3 py-2.5">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#a5adb7]">Workflow</span>
        <span className="font-mono text-[9px] uppercase tracking-wider text-[#59616b]">Live execution pipeline</span>
      </div>
      <div className="grid grid-cols-5 overflow-hidden rounded-[5px] border border-border bg-[#171a1f]">
        {stages.map((stage, index) => {
          const Icon = statusIcon[stage.status];
          return (
            <div
              key={stage.key}
              className={cn(
                "relative min-w-0 px-2.5 py-2",
                index > 0 && "border-l border-border",
                stage.status === "active" && "bg-[#1d2733]",
                stage.status === "failed" && "bg-[#2a1e20]",
                stage.status === "cancelled" && "bg-[#2a2418]",
                (stage.status === "blocked" || stage.status === "skipped" || stage.status === "waiting_for_device") && "bg-[#2a2418]",
              )}
            >
              {stage.status === "active" && <div className="absolute inset-x-0 top-0 h-0.5 animate-pulse-bar bg-[#58a6ff]" />}
              <div className="flex items-center gap-1.5">
                <span
                  className={cn(
                    "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                    stage.status === "pending" && "border-[#484f58] text-[#59616b]",
                    stage.status === "active" && "border-[#58a6ff] bg-[#1f6feb]/15 text-[#58a6ff]",
                    stage.status === "success" && "border-[#3fb950] bg-[#238636]/15 text-[#3fb950]",
                    stage.status === "failed" && "border-[#f85149] bg-[#da3633]/15 text-[#ff7b72]",
                    stage.status === "cancelled" && "border-[#d29922] bg-[#d29922]/15 text-[#d29922]",
                    (stage.status === "blocked" || stage.status === "skipped" || stage.status === "waiting_for_device") && "border-[#d29922] bg-[#d29922]/15 text-[#d29922]",
                  )}
                >
                  <Icon className={cn("h-2.5 w-2.5", stage.status === "active" && "animate-spin")} strokeWidth={2.5} />
                </span>
                <span className="truncate text-[11px] font-medium text-[#c9d1d9]">{stage.label}</span>
              </div>
              <div className="mt-1 truncate pl-[22px] font-mono text-[9px] text-[#6e7681]">
                {stage.durationMs !== undefined ? formatDuration(stage.durationMs) : stage.status}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
