"use client";

import { motion } from "framer-motion";
import { ArrowUpRight, CircuitBoard, Clock3, Code2, FolderOpen, Hammer, Plus, Radio, Sparkles, Wrench } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";

import { EmptyState } from "@/components/ui/empty-state";
import { ForgeCore } from "@/components/nexus/forge-core";
import { ActionTile, Metric, NexusSurface, SectionEyebrow } from "@/components/nexus/nexus-primitives";
import { UniversalComposer, type ForgeMode } from "@/components/nexus/universal-composer";
import type { useForgeAgentSession } from "@/hooks/use-forge-agent-session";
import type { ModelProviderResponse, ModelRouteResponse, ProjectResponse, ProviderModelsResponse } from "@/types";

type Agent = ReturnType<typeof useForgeAgentSession>;

type Workspace = ReturnType<typeof import("@/hooks/use-promptforge-workspace").usePromptForgeWorkspace>;

function Entrance({ active, index, className, children }: { active: boolean; index: number; className?: string; children: ReactNode }) {
  return (
    <motion.div
      className={className}
      initial={active ? { opacity: 0, y: 16 } : false}
      animate={{ opacity: 1, y: 0 }}
      transition={active ? { duration: 0.5, delay: 0.06 * index, ease: "easeOut" } : { duration: 0 }}
    >
      {children}
    </motion.div>
  );
}

export function NexusHome({
  agent,
  workspace,
  selectedBoardLabel,
  modelProviders,
  modelRoute,
  loadProviderModels,
  onModelChange,
  onBeginTask,
  onOpenProject,
  onOpenCode,
  onOpenHardware,
}: {
  agent: Agent;
  workspace: Workspace;
  selectedBoardLabel: string;
  modelProviders: ModelProviderResponse[];
  modelRoute: ModelRouteResponse | null;
  loadProviderModels: (providerId: string) => Promise<ProviderModelsResponse>;
  onModelChange: (providerId: string, modelId: string) => Promise<void> | void;
  onBeginTask: (prompt?: string) => void;
  onOpenProject: () => void;
  onOpenCode: () => void;
  onOpenHardware: () => void;
}) {
  const [mode, setMode] = useState<ForgeMode>("auto");
  const [entrance, setEntrance] = useState(false);
  const recentSessions = agent.sessions.slice(0, 4);
  const projects = workspace.projects.slice(0, 4);
  const board = workspace.detectedBoards[0] ?? null;
  const activeName = workspace.activeProject?.project_name ?? "No project open";
  const buildHistory = workspace.buildHistory.slice(0, 1);
  const buildHint = buildHistory[0] ? `${buildHistory[0].status} · ${buildHistory[0].board}` : "No build history yet";

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!media.matches && document.documentElement.dataset.motion !== "off") setEntrance(true);
  }, []);

  const autonomy = mode === "plan" ? "plan_only" : mode === "ask" ? "staged_changes" : "auto";
  const submit = async (value: string) => {
    if (!workspace.activeProject) return;
    onBeginTask();
    await agent.sendMessage(value, undefined, { autonomy, boardPort: board?.port ?? null, boardType: board?.board_type ?? null, environment: typeof workspace.activeProject.metadata?.active_environment === "string" ? workspace.activeProject.metadata.active_environment : null });
  };

  const greeting = useMemo(() => {
    const hour = new Date().getHours();
    return hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  }, []);

  return <div className="fx-nexus-home h-full min-h-0 overflow-y-auto">
    <div className="mx-auto flex min-h-full max-w-[1120px] flex-col px-5 pb-10 pt-[8vh] md:px-8">
      <div className="mx-auto w-full max-w-[780px] text-center">
        <ForgeCore state={workspace.health ? "idle" : "offline"} size="lg" label={false} />
        <div className="mt-6 text-[10px] font-semibold uppercase tracking-[.2em] text-[var(--fx-text-muted)]">{greeting}</div>
        <h1 className="mt-2 text-[32px] font-semibold tracking-[-.055em] text-[var(--fx-text)] md:text-[42px]">What are we building?</h1>
        <p className="mx-auto mt-3 max-w-[600px] text-[11px] leading-5 text-[var(--fx-text-muted)]">Forge orchestrates code, builds, verification, and hardware around your engineering objective. The workspace appears only when the work needs it.</p>
        <div className="mt-7 text-left"><UniversalComposer mode={mode} onModeChange={setMode} onSubmit={(value) => void submit(value)} submitDisabled={!workspace.activeProject} placeholder={workspace.activeProject ? `Work on ${workspace.activeProject.project_name}…` : "Open a project before starting a task…"} modelProviders={modelProviders} modelRoute={modelRoute} loadProviderModels={loadProviderModels} onModelChange={onModelChange} /></div>
        {!workspace.activeProject ? <button onClick={onOpenProject} className="mt-3 inline-flex items-center gap-2 rounded-xl border border-[var(--fx-border-soft)] px-3 py-2 text-[10px] text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"><FolderOpen className="h-3.5 w-3.5" /> Open an existing project</button> : null}
      </div>

      <div className="mt-12 grid gap-3 lg:grid-cols-[1.35fr_.85fr]">
        <Entrance active={entrance} index={0} className="min-w-0">
          <NexusSurface className="h-full p-4 md:p-5">
            <div className="flex items-center justify-between"><div><SectionEyebrow>Active project</SectionEyebrow><div className="mt-1 text-[16px] font-semibold tracking-[-.025em] text-[var(--fx-text)]">{activeName}</div></div><button onClick={onOpenCode} disabled={!workspace.activeProject} className="fx-nexus-action"><Code2 className="h-3.5 w-3.5" /> Workspace</button></div>
            <div className="mt-5 grid gap-3 sm:grid-cols-3"><Metric label="Target" value={workspace.activeProject?.target_board ?? "—"} hint={workspace.activeProject?.framework ?? "No framework"} /><Metric label="Files" value={String(workspace.activeProject?.file_count ?? 0)} hint="Project source" /><Metric label="Latest build" value={buildHistory[0]?.status?.toUpperCase() ?? "—"} hint={buildHint} /></div>
            <div className="mt-5 grid gap-2 sm:grid-cols-3"><ActionTile icon={Sparkles} title="Build a feature" detail="Plan, implement and verify" accent onClick={() => onBeginTask("Add a useful feature to this project, then build and verify it.")} /><ActionTile icon={Wrench} title="Debug" detail="Investigate current problems" onClick={() => onBeginTask("Find the most important current problem, repair it safely, and verify the result.")} /><ActionTile icon={Hammer} title="Build" detail="Verify current firmware" onClick={() => void workspace.buildActiveProject()} /></div>
          </NexusSurface>
        </Entrance>

        <Entrance active={entrance} index={1} className="min-w-0">
          <NexusSurface className="h-full p-4 md:p-5" glow={Boolean(board)}>
            <div className="flex items-center justify-between"><SectionEyebrow>Hardware</SectionEyebrow><span className="flex items-center gap-1.5 text-[9px] uppercase tracking-[.13em] text-[var(--fx-text-muted)]"><span className={`h-1.5 w-1.5 rounded-full ${board ? "bg-[var(--fx-success)] shadow-[0_0_10px_var(--fx-success)]" : "bg-[var(--fx-text-muted)]"}`} /> {board ? "connected" : "offline"}</span></div>
            <div className="mt-4 flex items-center gap-4"><span className="grid h-12 w-12 place-items-center rounded-2xl bg-[color-mix(in_srgb,var(--fx-info)_10%,var(--fx-panel-elevated))] text-[var(--fx-info)]"><CircuitBoard className="h-6 w-6" /></span><div className="min-w-0"><div className="truncate text-[13px] font-semibold text-[var(--fx-text)]">{board?.board_type ?? "No device detected"}</div><div className="mt-0.5 truncate font-mono text-[9px] text-[var(--fx-text-muted)]">{selectedBoardLabel}</div></div></div>
            <div className="mt-5 grid grid-cols-2 gap-2"><button onClick={onOpenHardware} className="fx-nexus-action justify-center"><CircuitBoard className="h-3.5 w-3.5" /> Hardware</button><button disabled={!board} onClick={() => board && void workspace.startSerialMonitor(board)} className="fx-nexus-action justify-center"><Radio className="h-3.5 w-3.5" /> Monitor</button></div>
          </NexusSurface>
        </Entrance>
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        <Entrance active={entrance} index={2} className="min-w-0">
          <NexusSurface className="h-full p-4 md:p-5"><div className="flex items-center justify-between"><SectionEyebrow>Recent tasks</SectionEyebrow><button onClick={() => onBeginTask()} className="fx-nexus-icon"><Plus className="h-4 w-4" /></button></div><div className="mt-3 space-y-1">{recentSessions.length ? recentSessions.map((session) => <button key={session.session_id} onClick={() => void agent.resumeSession(session.session_id).then(() => onBeginTask())} className="group flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left hover:bg-[var(--fx-hover)]"><span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-[var(--fx-accent-faint)] text-[var(--fx-accent)]"><Clock3 className="h-3.5 w-3.5" /></span><span className="min-w-0 flex-1"><span className="block truncate text-[11px] font-medium text-[var(--fx-text)]">{session.title}</span><span className="mt-0.5 block text-[9px] text-[var(--fx-text-muted)]">{session.message_count} messages · {new Date(session.updated_at).toLocaleString()}</span></span><ArrowUpRight className="h-3.5 w-3.5 text-[var(--fx-text-muted)] opacity-0 group-hover:opacity-100" /></button>) : <EmptyState icon={Clock3} title="No tasks yet" hint="Your Forge tasks will appear here." action={<button onClick={() => onBeginTask()} className="fx-nexus-action"><Plus className="h-3.5 w-3.5" /> New task</button>} />}</div></NexusSurface>
        </Entrance>

        <Entrance active={entrance} index={3} className="min-w-0">
          <NexusSurface className="h-full p-4 md:p-5"><div className="flex items-center justify-between"><SectionEyebrow>Projects</SectionEyebrow><button onClick={onOpenProject} className="fx-nexus-action"><FolderOpen className="h-3.5 w-3.5" /> Open</button></div><div className="mt-3 grid gap-2 sm:grid-cols-2">{projects.length ? projects.map((project: ProjectResponse) => <button key={project.project_id} onClick={() => workspace.setActiveProject(project)} className={`rounded-xl border p-3 text-left ${workspace.activeProject?.project_id === project.project_id ? "border-[color-mix(in_srgb,var(--fx-accent)_36%,var(--fx-border))] bg-[var(--fx-accent-faint)]" : "border-[var(--fx-border-soft)] hover:bg-[var(--fx-hover)]"}`}><div className="truncate text-[11px] font-semibold text-[var(--fx-text)]">{project.project_name}</div><div className="mt-1 flex items-center justify-between gap-2 text-[9px] text-[var(--fx-text-muted)]"><span className="truncate">{project.target_board}</span><span>{project.file_count} files</span></div></button>) : <div className="col-span-2"><EmptyState icon={FolderOpen} title="No projects yet" hint="Open your first embedded project." action={<button onClick={onOpenProject} className="fx-nexus-action"><FolderOpen className="h-3.5 w-3.5" /> Open project</button>} /></div>}</div></NexusSurface>
        </Entrance>
      </div>
    </div>
  </div>;
}
