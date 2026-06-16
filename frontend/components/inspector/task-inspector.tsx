import { Activity, Box, Braces, Clock3, Cpu, FolderCog, Gauge, Hash, Timer, Waypoints, Zap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { formatBytes, formatDuration } from "@/lib/utils";
import type { ProjectResponse, WorkflowStage } from "@/types";

interface TaskInspectorProps {
  taskId: string | null;
  executionId: string | null;
  stages: WorkflowStage[];
  activeProject: ProjectResponse | null;
  executionTime?: number;
  generatedProject: Record<string, unknown> | null;
  buildResult: Record<string, unknown> | null;
}

function Field({ icon: Icon, label, value, mono = false }: { icon: typeof Hash; label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="grid grid-cols-[16px_88px_minmax(0,1fr)] items-start gap-1.5 px-3 py-1.5 text-[10px]">
      <Icon className="mt-0.5 h-3 w-3 text-[#6e7681]" />
      <span className="text-[#7d8590]">{label}</span>
      <span className={`min-w-0 break-all text-right text-[#c9d1d9] ${mono ? "font-mono text-[9px]" : ""}`}>{value}</span>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <div className="flex h-7 items-center border-y border-border bg-[#1b1f24] px-3 text-[9px] font-semibold uppercase tracking-[0.12em] text-[#8b949e]">{title}</div>
      <div className="py-1">{children}</div>
    </section>
  );
}

export function TaskInspector({ taskId, executionId, stages, activeProject, executionTime, generatedProject, buildResult }: TaskInspectorProps) {
  const currentStage = stages.find((stage) => stage.status === "active") ?? [...stages].reverse().find((stage) => stage.status === "success" || stage.status === "failed");
  const buildSize = typeof buildResult?.build_size_bytes === "number" ? buildResult.build_size_bytes : null;
  const warnings = typeof buildResult?.warnings_count === "number" ? buildResult.warnings_count : null;
  const projectPath = typeof generatedProject?.project_path === "string" ? generatedProject.project_path : activeProject?.project_path;

  return (
    <aside className="task-inspector min-h-0 overflow-y-auto border-l border-border bg-[#181b20]">
      <div className="flex h-9 items-center justify-between border-b border-border px-3">
        <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#9aa3ad]">Inspector</span>
        <Badge className="normal-case">Live</Badge>
      </div>

      <Section title="Execution">
        <Field icon={Hash} label="Task ID" value={taskId ?? "Not started"} mono />
        <Field icon={Waypoints} label="Stage" value={currentStage?.label ?? "Idle"} />
        <Field icon={Activity} label="Status" value={currentStage?.status ?? "ready"} />
        <Field icon={Braces} label="Execution ID" value={executionId ?? "--"} mono />
        <Field icon={Timer} label="Elapsed" value={formatDuration(executionTime)} mono />
      </Section>

      <Section title="Model">
        <Field icon={Cpu} label="Provider" value="OpenRouter" />
        <Field icon={Zap} label="Model" value="gpt-oss-120b:free" mono />
        <Field icon={Gauge} label="Token usage" value="Not reported" />
      </Section>

      <Section title="Build Metrics">
        <Field icon={Box} label="Target" value={activeProject?.target_board ?? "ESP32"} mono />
        <Field icon={Braces} label="Framework" value={activeProject?.framework ?? "PlatformIO"} />
        <Field icon={Gauge} label="Firmware" value={formatBytes(buildSize)} mono />
        <Field icon={Activity} label="Warnings" value={warnings ?? "--"} mono />
      </Section>

      <Section title="Workspace">
        <Field icon={FolderCog} label="Project" value={activeProject?.project_name ?? "Reference workspace"} />
        <div className="px-3 pb-2 pt-1">
          <div className="mb-1.5 flex items-center gap-1.5 text-[9px] uppercase tracking-wider text-[#6e7681]"><Clock3 className="h-3 w-3" /> Project path</div>
          <div className="rounded-[3px] border border-border bg-[#111419] px-2 py-1.5 font-mono text-[9px] leading-4 text-[#8b949e] break-all">{projectPath ?? ".promptforge/projects"}</div>
        </div>
      </Section>
      <Separator />
    </aside>
  );
}
