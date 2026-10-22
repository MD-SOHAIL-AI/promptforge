"use client";

import { ArrowLeft, Check, CircuitBoard, Code2, FileDiff, Hammer, LoaderCircle, Radio, RefreshCw, Rocket, ShieldCheck, TerminalSquare, Usb, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { EditorWorkbench } from "@/components/ide/editor-workbench";
import { IdeExplorer } from "@/components/ide/ide-explorer";
import { BottomPanel } from "@/components/ide/bottom-panel";
import { Metric, NexusSurface, ProgressRail, SectionEyebrow } from "@/components/nexus/nexus-primitives";
import { buildMetrics, pendingWorkspaceChangeCount } from "@/lib/nexus-agent-view";
import { useAgentChangeSet } from "@/hooks/use-agent-change-set";
import { useWorkspacePreferences } from "@/hooks/workspace-preferences";
import type { DetectedBoard, ProductAgentRun } from "@/types";
import {promptForgeApi} from "@/lib/api";
import { canApplyAgentChanges } from "@/lib/agent-recovery";

type Workspace = ReturnType<typeof import("@/hooks/use-promptforge-workspace").usePromptForgeWorkspace>;

export type NexusCanvas = "task" | "code" | "build" | "review" | "evidence" | "hardware" | "serial" | "terminal";

export function CanvasHeader({ icon: Icon, title, subtitle, onBack, actions }: { icon: typeof Code2; title: string; subtitle?: string; onBack: () => void; actions?: ReactNode }) {
  return <div className="fx-canvas-header flex h-11 shrink-0 items-center justify-between gap-3 border-b border-[var(--fx-border-soft)] px-3.5">
    <div className="flex min-w-0 items-center gap-3">
      <button onClick={onBack} className="grid h-7 w-7 place-items-center rounded-md text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]" title="Back to task"><ArrowLeft className="h-4 w-4" /></button>
      <span className="grid h-7 w-7 place-items-center rounded-md bg-[var(--fx-accent-faint)] text-[var(--fx-accent)]"><Icon className="h-4 w-4" /></span>
      <span className="min-w-0"><span className="block truncate text-[12px] font-semibold text-[var(--fx-text)]">{title}</span>{subtitle ? <span className="block truncate text-[10px] text-[var(--fx-text-muted)]">{subtitle}</span> : null}</span>
    </div>
    <div className="flex items-center gap-2">{actions}</div>
  </div>;
}

export function CodeCanvas({
  workspace,
  run,
  applyingChanges = false,
  onBack,
  onOpenReview,
  onApply,
  onApplied,
}: {
  workspace: Workspace;
  run: ProductAgentRun | null;
  applyingChanges?: boolean;
  onBack: () => void;
  onOpenReview: () => void;
  onApply: () => Promise<ProductAgentRun | null> | ProductAgentRun | null;
  onApplied: () => Promise<unknown> | unknown;
}) {
  const [explorerOpen, setExplorerOpen] = useState(true);
  const [applyError, setApplyError] = useState<string | null>(null);
  const { changeSet, loading } = useAgentChangeSet(run);
  const canApply = !loading && canApplyAgentChanges(run, changeSet);
  const preferences = useWorkspacePreferences();
  const editorSettings = { fontSize: Number(preferences["editor.font_size"] ?? 14), tabSize: Number(preferences["editor.tab_size"] ?? 2), wordWrap: preferences["editor.word_wrap"] !== false, minimapEnabled: preferences["editor.minimap_enabled"] === true, showLineNumbers: preferences["editor.show_line_numbers"] !== false, showBreadcrumbs: preferences["editor.show_breadcrumbs"] !== false, formatOnSave: preferences["editor.format_on_save"] === true, autoSave: preferences["editor.auto_save"] === true };
  const pendingCount = run?.classification === "CHANGESET_APPLIED" || changeSet?.status === "applied"
    || changeSet?.status === "discarded" ? 0 : pendingWorkspaceChangeCount(run);
  const applyPendingChanges = async () => {
    if (!canApply || applyingChanges) return;
    setApplyError(null);
    try {
      const applied = await onApply();
      if (!applied) {
        setApplyError("ForgeX could not apply the staged changes. Open Review for details.");
        return;
      }
      await onApplied();
    } catch (error) {
      setApplyError(error instanceof Error ? error.message : "ForgeX could not apply the staged changes.");
    }
  };
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={Code2} title="Code canvas" subtitle={workspace.activePath ?? "Workspace source"} onBack={onBack} actions={<button onClick={() => setExplorerOpen((value) => !value)} className="fx-nexus-action">{explorerOpen ? "Hide files" : "Files"}</button>} />
    <div className="flex min-h-0 flex-1">
      {explorerOpen ? <aside className="w-[190px] shrink-0 border-r border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-panel)_62%,transparent)]"><IdeExplorer activeProject={workspace.activeProject} entries={workspace.entries} selectedPath={workspace.activePath} revealedPaths={workspace.recentlyAddedPaths} onOpenFile={workspace.openFile} onCreateFile={(base) => void workspace.createEntry("file", base)} onCreateFolder={(base) => void workspace.createEntry("folder", base)} onRename={workspace.renameEntry} onDelete={workspace.deleteEntry} onRefresh={() => void workspace.refreshProjectFiles()} /></aside> : null}
      <div className="flex min-w-0 flex-1 flex-col">
        {pendingCount ? <div className="shrink-0 border-b border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-warning)_8%,var(--fx-panel))] px-4 py-3">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2 text-[11px] font-semibold text-[var(--fx-text)]"><FileDiff className="h-4 w-4 text-[var(--fx-warning)]" /> {pendingCount} staged file{pendingCount === 1 ? "" : "s"} waiting for review</div>
              <div className="mt-1 text-[10px] leading-4 text-[var(--fx-text-muted)]">The active workspace is still unchanged, so the explorer can show 0 files until you approve the staged ChangeSet.</div>
              {applyError ? <div className="mt-1 text-[10px] text-[var(--fx-error)]">{applyError}</div> : null}
            </div>
            <div className="flex shrink-0 gap-2">
              <button onClick={onOpenReview} className="fx-nexus-action"><FileDiff className="h-3.5 w-3.5" /> Review files</button>
              {canApply ? <button onClick={() => void applyPendingChanges()} disabled={applyingChanges} className="fx-primary-action disabled:opacity-50">{applyingChanges ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Apply changes</button> : null}
            </div>
          </div>
        </div> : null}
        <div className="min-h-0 flex-1"><EditorWorkbench activeProject={workspace.activeProject} selectedFile={workspace.selectedFile} tabs={workspace.tabs} activePath={workspace.activePath} onSelectTab={workspace.openFile} onCloseTab={workspace.closeTab} onChange={workspace.updateTabContent} onSave={(path, content) => void workspace.saveFile(path, content)} editorSettings={editorSettings} /></div>
      </div>
    </div>
  </div>;
}

export function BuildCanvas({ workspace, run, onBack, onOpenTerminal, onFlash }: { workspace: Workspace; run: ProductAgentRun | null; onBack: () => void; onOpenTerminal: () => void; onFlash: () => void }) {
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const scopedRun = run?.project_id === workspace.activeProject?.project_id ? run : null;
  const [agentEvidence, setAgentEvidence] = useState<{key:string; state:"idle" | "failed" | "passed" | "stale" | "unavailable"; scope:"workspace" | "saved_draft"} | null>(null);
  const evidenceKey = `${scopedRun?.run_id}:${scopedRun?.build_result?.build_id}:${scopedRun?.build_verified}:${workspace.buildEnvironment}:${workspace.sourceEditsRevision}`;
  useEffect(() => {
    if (!scopedRun?.build_result) return;
    const controller = new AbortController();
    void promptForgeApi.agentBuildEvidence(scopedRun.run_id, workspace.buildEnvironment, controller.signal)
      .then(value => {if (!controller.signal.aborted) setAgentEvidence({...value,key:evidenceKey});})
      .catch(() => {if (!controller.signal.aborted) setAgentEvidence({key:evidenceKey,state:"unavailable",scope:"workspace"});});
    return () => controller.abort();
  }, [evidenceKey, scopedRun?.run_id, scopedRun?.build_result, workspace.buildEnvironment]);
  const metrics = buildMetrics(scopedRun);
  const direct = workspace.buildObservation.projectId === workspace.activeProject?.project_id ? workspace.buildResult as Record<string, unknown> | null : null;
  const observation = workspace.buildObservation;
  const hasDirect = observation.projectId === workspace.activeProject?.project_id && observation.state !== "idle";
  const state = hasDirect ? observation.state : metrics ? metrics.success ? workspace.tabs.some(tab => tab.dirty) ? "stale" : agentEvidence?.key === evidenceKey ? agentEvidence.state : "stale" : "failed" : "idle";
  const success = state === "passed";
  const flashReady = success && (hasDirect ? direct?.metadata && typeof direct.metadata === "object" && (direct.metadata as Record<string, unknown>).flash_ready === true : scopedRun?.build_result && (scopedRun.build_result as Record<string, unknown>).metadata && typeof (scopedRun.build_result as Record<string, unknown>).metadata === "object" && ((scopedRun.build_result as Record<string, unknown>).metadata as Record<string, unknown>).flash_ready === true);
  const size = success ? hasDirect ? Number(direct?.build_size_bytes ?? 0) : metrics?.size ?? 0 : 0;
  const warnings = hasDirect ? Number(direct?.warnings_count ?? 0) : metrics?.warnings;
  const board = metrics?.board || String(direct?.board ?? workspace.activeProject?.target_board ?? "Unknown board");
  const message = refreshError || (hasDirect ? observation.message || (state === "running" ? "Compiling the selected project and environment." : "No verified build yet.") : state === "stale" ? "The saved compiler result needs current-source verification." : metrics?.message || "No verified build yet.");
  const metadata = hasDirect ? direct?.metadata : scopedRun?.build_result?.metadata;
  const memory = metadata && typeof metadata === "object" ? metadata as Record<string, unknown> : {};
  const percent = (used: unknown, total: unknown) => typeof used === "number" && typeof total === "number" && total > 0 ? Math.min(100, Math.max(0, used / total * 100)) : null;
  const flashPercent = success ? percent(memory.flash_used_bytes, memory.flash_total_bytes) : null;
  const ramPercent = success ? percent(memory.ram_used_bytes, memory.ram_total_bytes) : null;
  const title = {idle: "Ready for the next build", running: "Compiling firmware", passed: "Firmware compiled successfully", failed: "Firmware build failed", unavailable: "Build result unavailable", cancelled: "Build cancelled", stale: "Build needs verification"}[state];
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={Hammer} title="Build artifact" subtitle={`${board}${!hasDirect && agentEvidence?.scope === "saved_draft" ? " · Saved draft" : ""}`} onBack={onBack} actions={<><button onClick={onOpenTerminal} className="fx-nexus-action"><TerminalSquare className="h-3.5 w-3.5" /> Raw output</button><button onClick={() => {setRefreshError(null); void (hasDirect ? workspace.refreshBuildOperation() : scopedRun ? promptForgeApi.agentBuildEvidence(scopedRun.run_id, workspace.buildEnvironment).then(value => setAgentEvidence({...value,key:evidenceKey})) : Promise.resolve()).catch(cause => setRefreshError(cause instanceof Error ? cause.message : "Build status could not be refreshed."));}} className="fx-nexus-action">Refresh result</button><button disabled={state === "running"} onClick={() => void workspace.buildActiveProject()} className="fx-nexus-action"><RefreshCw className="h-3.5 w-3.5" /> Build</button></>} />
    <div className="min-h-0 flex-1 overflow-auto p-3.5">
      <div className="space-y-3">
        <NexusSurface className={`overflow-hidden p-4 ${success ? "is-success" : ""}`} glow={Boolean(workspace.toolAction === "build")}>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <span className={`grid h-10 w-10 place-items-center rounded-lg ${success ? "bg-[var(--fx-success-soft)] text-[var(--fx-success)]" : "bg-[var(--fx-panel-elevated)] text-[var(--fx-text-muted)]"}`}>{workspace.toolAction === "build" ? <LoaderCircle className="h-4.5 w-4.5 animate-spin" /> : success ? <Check className="h-4.5 w-4.5" /> : <Hammer className="h-4.5 w-4.5" />}</span>
              <div><SectionEyebrow>{state === "running" ? "Building" : `Build ${state}`}</SectionEyebrow><div className="mt-1 text-[15px] font-semibold tracking-[-.02em] text-[var(--fx-text)]">{title}</div><p className="mt-1 max-w-[580px] text-[11px] leading-5 text-[var(--fx-text-muted)]">{message}</p></div>
            </div>
            {flashReady && (hasDirect || !scopedRun?.active_workspace_unchanged) ? <button onClick={onFlash} className="fx-primary-action"><Rocket className="h-4 w-4" /> Flash device</button> : null}
          </div>
        </NexusSurface>

        <div className="grid grid-cols-2 gap-2">
          <NexusSurface className="p-4"><Metric label="Target" value={board || "—"} hint={metrics?.platform || workspace.activeProject?.framework || "PlatformIO"} /></NexusSurface>
          <NexusSurface className="p-4"><Metric label="Firmware" value={size ? `${(size / 1024).toFixed(1)} KB` : "—"} hint="Verified artifact size" /></NexusSurface>
          <NexusSurface className="p-4"><Metric label="Warnings" value={warnings === undefined ? "—" : String(warnings)} hint={warnings ? "Review recommended" : success ? "Clean build" : "Awaiting verified result"} /></NexusSurface>
          <NexusSurface className="p-4"><Metric label="Status" value={state.toUpperCase()} hint={observation.observedAt ? new Date(observation.observedAt).toLocaleTimeString() : scopedRun?.updated_at ? new Date(scopedRun.updated_at).toLocaleTimeString() : "Awaiting build"} /></NexusSurface>
        </div>

        <NexusSurface className="p-4">
          <div className="flex items-center justify-between"><SectionEyebrow>Firmware budget</SectionEyebrow><span className="text-[10px] text-[var(--fx-text-muted)]">Compiler measurements</span></div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <div><div className="mb-2 flex justify-between text-[10px]"><span className="text-[var(--fx-text-muted)]">Flash</span><span className="font-mono text-[var(--fx-text)]">{flashPercent === null ? "Measurement unavailable" : `${flashPercent.toFixed(1)}%`}</span></div>{flashPercent !== null ? <ProgressRail value={flashPercent} tone="accent" /> : null}</div>
            <div><div className="mb-2 flex justify-between text-[10px]"><span className="text-[var(--fx-text-muted)]">RAM</span><span className="font-mono text-[var(--fx-text)]">{ramPercent === null ? "Measurement unavailable" : `${ramPercent.toFixed(1)}%`}</span></div>{ramPercent !== null ? <ProgressRail value={ramPercent} tone="success" /> : null}</div>
          </div>
        </NexusSurface>
      </div>
    </div>
  </div>;
}

export function ReviewCanvas({ run, initialPath = null, applyingChanges = false, onBack, onApply, onApplied, onDiscard }: { run: ProductAgentRun | null; initialPath?: string | null; applyingChanges?: boolean; onBack: () => void; onApply: () => Promise<ProductAgentRun | null> | ProductAgentRun | null; onApplied: () => Promise<unknown> | unknown; onDiscard?: () => Promise<unknown> }) {
  const { changeSet, loading } = useAgentChangeSet(run);
  const canApply = !loading && canApplyAgentChanges(run, changeSet);
  const [selected, setSelected] = useState<string | null>(null);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const discardInFlight = useRef(false);
  const firstChangedPath = changeSet?.changed_files[0]?.path ?? null;
  useEffect(() => {
    setSelected(initialPath ?? firstChangedPath);
  }, [changeSet?.change_set_id, firstChangedPath, initialPath]);
  const current = changeSet?.changed_files.find((file) => file.path === selected) ?? changeSet?.changed_files[0] ?? null;
  const applyPendingChanges = async () => {
    if (!canApply || applyingChanges) return;
    setApplyError(null);
    try {
      const applied = await onApply();
      if (!applied) {
        setApplyError("ForgeX could not apply the staged changes. Check the latest runtime error and try again.");
        return;
      }
      await onApplied();
    } catch (error) {
      setApplyError(error instanceof Error ? error.message : "ForgeX could not apply the staged changes.");
    }
  };
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={FileDiff} title="Forge review" subtitle={changeSet ? `${changeSet.changed_files.length} files changed` : "Staged changes"} onBack={onBack} />
    <div className="flex min-h-0 flex-1">
      <aside className="w-[190px] shrink-0 overflow-auto border-r border-[var(--fx-border-soft)] p-2.5">
        <SectionEyebrow>Changed files</SectionEyebrow>
        <div className="mt-3 space-y-1">{loading ? <div className="p-3 text-[10px] text-[var(--fx-text-muted)]">Loading staged changes…</div> : changeSet?.changed_files.length ? changeSet.changed_files.map((file) => <button key={file.path} onClick={() => setSelected(file.path)} className={`flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left ${current?.path === file.path ? "bg-[var(--fx-accent-faint)] text-[var(--fx-text)]" : "text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)]"}`}><span className={`grid h-5 w-5 place-items-center rounded-md text-[9px] font-bold ${file.change_type === "created" ? "bg-[var(--fx-success-soft)] text-[var(--fx-success)]" : file.change_type === "deleted" ? "bg-[var(--fx-error-soft)] text-[var(--fx-error)]" : "bg-[var(--fx-accent-soft)] text-[var(--fx-accent)]"}`}>{file.change_type[0].toUpperCase()}</span><span className="min-w-0 flex-1 truncate text-[10px]">{file.path}</span></button>) : <div className="rounded-md border border-dashed border-[var(--fx-border-soft)] p-4 text-center text-[10px] text-[var(--fx-text-muted)]">No staged ChangeSet on this run.</div>}</div>
      </aside>
      <div className="min-w-0 flex-1 overflow-auto p-3.5">
        {current ? <div><div className="mb-3 flex items-center justify-between"><div><div className="font-mono text-[12px] text-[var(--fx-text)]">{current.path}</div><div className="mt-1 text-[10px] text-[var(--fx-text-muted)]">{current.change_type} · {current.safe ? "safe" : "review required"}</div></div><span className={`rounded-full border px-2.5 py-1 text-[9px] uppercase tracking-[.12em] ${current.safe ? "border-[color-mix(in_srgb,var(--fx-success)_38%,var(--fx-border))] text-[var(--fx-success)]" : "border-[color-mix(in_srgb,var(--fx-warning)_40%,var(--fx-border))] text-[var(--fx-warning)]"}`}>{current.safe ? "low risk" : "attention"}</span></div><NexusSurface className="overflow-hidden"><pre className="max-h-[64vh] overflow-auto whitespace-pre-wrap p-4 font-mono text-[11px] leading-5 text-[var(--fx-code-text)]">{current.diff_preview || "Diff preview is unavailable for this file."}</pre></NexusSurface></div> : <div className="grid h-full place-items-center text-[11px] text-[var(--fx-text-muted)]">Select a staged file to review.</div>}
      </div>
    </div>
    {changeSet?.status === "draft" ? <div className="flex items-center justify-between gap-3 border-t border-[var(--fx-border-soft)] p-3 text-xs text-[var(--fx-text-muted)]">
      <span>Recovery draft. Resume and build these files before approval.</span>
      {onDiscard ? <button disabled={discarding} onClick={async () => {
        if (discardInFlight.current) return;
        discardInFlight.current = true;
        setDiscarding(true); setApplyError(null);
        try { await onDiscard(); onBack(); }
        catch (cause) { setApplyError(cause instanceof Error ? cause.message : "Draft could not be discarded."); }
        finally { discardInFlight.current = false; setDiscarding(false); }
      }}>{discarding ? "Discarding…" : "Discard draft"}</button> : null}
      {applyError ? <span role="alert">{applyError}</span> : null}
    </div> : null}
    {canApply ? <div className="flex h-16 shrink-0 items-center justify-between gap-4 border-t border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-panel)_84%,transparent)] px-3.5"><div className="min-w-0 text-[10px] text-[var(--fx-text-muted)]"><div className="flex items-center gap-2"><ShieldCheck className="h-4 w-4 shrink-0 text-[var(--fx-success)]" /> Changes are still isolated from the active workspace.</div>{applyError ? <div className="mt-1 truncate text-[var(--fx-error)]">{applyError}</div> : null}</div><button onClick={() => void applyPendingChanges()} disabled={applyingChanges || loading} className="fx-primary-action shrink-0 disabled:opacity-50">{applyingChanges ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />} Apply changes</button></div> : null}
  </div>;
}

export function HardwareCanvas({ workspace, selectedBoard, onSelectBoard, onBack }: { workspace: Workspace; selectedBoard: DetectedBoard | null; onSelectBoard: (port: string) => void; onBack: () => void }) {
  const connected = Boolean(selectedBoard);
  const monitor = workspace.monitorStatus ?? { state: "idle", connected: false, port: null, baudrate: 115200, metrics: {} };
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={CircuitBoard} title="Hardware canvas" subtitle={connected ? `${selectedBoard?.board_type} · ${selectedBoard?.port}` : "No connected device"} onBack={onBack} actions={<button className="fx-nexus-action" onClick={() => void workspace.refreshDetectedBoards()}><RefreshCw className="h-3.5 w-3.5" /> Detect</button>} />
    <div className="min-h-0 flex-1 overflow-auto p-3.5">
      <div className="space-y-3">
        <NexusSurface className="relative overflow-hidden p-4" glow={connected}>
          <div className="fx-device-grid" />
          <div className="relative flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3"><span className={`grid h-11 w-11 place-items-center rounded-lg border ${connected ? "border-[color-mix(in_srgb,var(--fx-info)_40%,var(--fx-border))] bg-[color-mix(in_srgb,var(--fx-info)_9%,var(--fx-panel))] text-[var(--fx-info)] " : "border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] text-[var(--fx-text-muted)]"}`}><CircuitBoard className="h-5 w-5" /></span><div><SectionEyebrow>{connected ? "Connected device" : "Hardware"}</SectionEyebrow><div className="mt-1 text-[15px] font-semibold tracking-[-.02em] text-[var(--fx-text)]">{selectedBoard?.board_type ?? "Connect a board"}</div><div className="mt-1 flex items-center gap-2 text-[10px] text-[var(--fx-text-muted)]">{connected ? <><span className="h-1.5 w-1.5 rounded-full bg-[var(--fx-success)] " /> Live on {selectedBoard?.port}</> : "Forge will detect supported serial devices."}</div></div></div>
            {connected ? <div className="flex gap-2"><button onClick={() => void workspace.startSerialMonitor(selectedBoard)} className="fx-nexus-action"><Radio className="h-3.5 w-3.5" /> Monitor</button><button onClick={() => void workspace.flashActiveProject(selectedBoard)} className="fx-primary-action"><Rocket className="h-4 w-4" /> Flash</button></div> : null}
          </div>
        </NexusSurface>

        <div className="grid grid-cols-3 gap-2"><NexusSurface className="p-4"><Metric label="Port" value={selectedBoard?.port ?? "—"} hint={selectedBoard?.manufacturer ?? "USB / serial"} /></NexusSurface><NexusSurface className="p-4"><Metric label="Environment" value={String(workspace.activeProject?.metadata?.active_environment ?? "auto")} hint={workspace.activeProject?.framework ?? "PlatformIO"} /></NexusSurface><NexusSurface className="p-4"><Metric label="Serial" value={monitor.connected ? `${monitor.baudrate} baud` : "Idle"} hint={monitor.connected ? "Monitor active" : "Ready"} /></NexusSurface></div>

        <NexusSurface className="p-4"><div className="flex items-center justify-between"><SectionEyebrow>Detected hardware</SectionEyebrow><span className="text-[10px] text-[var(--fx-text-muted)]">{workspace.detectedBoards.length} devices</span></div><div className="mt-3 grid gap-2">{workspace.detectedBoards.map((board) => <button key={board.port} onClick={() => onSelectBoard(board.port)} className={`flex items-center gap-3 rounded-md border p-2.5 text-left ${selectedBoard?.port === board.port ? "border-[color-mix(in_srgb,var(--fx-accent)_40%,var(--fx-border))] bg-[var(--fx-accent-faint)]" : "border-[var(--fx-border-soft)] hover:bg-[var(--fx-hover)]"}`}><Usb className="h-4 w-4 text-[var(--fx-info)]" /><span className="min-w-0 flex-1"><span className="block truncate text-[11px] font-medium text-[var(--fx-text)]">{board.board_type}</span><span className="block truncate text-[9px] text-[var(--fx-text-muted)]">{board.port} · {board.description ?? "serial device"}</span></span>{selectedBoard?.port === board.port ? <Check className="h-3.5 w-3.5 text-[var(--fx-success)]" /> : null}</button>)}</div></NexusSurface>
      </div>
    </div>
  </div>;
}

export function SerialCanvas({ workspace, selectedBoard, onBack }: { workspace: Workspace; selectedBoard: DetectedBoard | null; onBack: () => void }) {
  const monitor = workspace.monitorStatus ?? { state: "idle", connected: false, port: null, baudrate: 115200, metrics: {} };
  const serialLogs = useMemo(() => [...workspace.logs.filter((log) => log.channel === "serial").map((log) => log.message), ...workspace.workspaceLogs.filter((log) => log.log_type === "monitor").map((log) => log.content.trim())].filter(Boolean).slice(-120), [workspace.logs, workspace.workspaceLogs]);
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={Radio} title="Live device" subtitle={monitor.connected ? `${monitor.port} · ${monitor.baudrate}` : "Serial monitor idle"} onBack={onBack} actions={monitor.connected ? <button className="fx-nexus-action text-[var(--fx-error)]" onClick={() => void workspace.stopSerialMonitor()}><X className="h-3.5 w-3.5" /> Stop</button> : <button className="fx-primary-action" disabled={!selectedBoard} onClick={() => selectedBoard && void workspace.startSerialMonitor(selectedBoard)}><Radio className="h-4 w-4" /> Start monitor</button>} />
    <div className="grid min-h-0 flex-1 gap-3 overflow-auto p-3.5">
      <NexusSurface className="min-h-[360px] overflow-hidden"><div className="flex items-center justify-between border-b border-[var(--fx-border-soft)] px-4 py-3"><SectionEyebrow>Serial stream</SectionEyebrow><div className="flex items-center gap-1.5 text-[9px] uppercase tracking-[.12em] text-[var(--fx-text-muted)]"><span className={`h-1.5 w-1.5 rounded-full ${monitor.connected ? "bg-[var(--fx-success)] fx-live-dot" : "bg-[var(--fx-text-muted)]"}`} /> {monitor.connected ? "live" : "offline"}</div></div><div className="h-[calc(100%-42px)] overflow-auto p-4 font-mono text-[11px] leading-5 text-[var(--fx-terminal-text)]">{serialLogs.length ? serialLogs.map((line, index) => <div key={index} className="border-b border-[color-mix(in_srgb,var(--fx-border-soft)_40%,transparent)] py-1"><span className="mr-3 text-[8px] text-[var(--fx-text-muted)]">{String(index + 1).padStart(3, "0")}</span>{line}</div>) : <div className="grid h-full place-items-center text-center font-sans text-[10px] text-[var(--fx-text-muted)]"><div><Radio className="mx-auto mb-3 h-6 w-6 opacity-40" />No serial telemetry yet.<br />Start the monitor to stream device output.</div></div>}</div></NexusSurface>
      <div className="grid gap-2 sm:grid-cols-2"><NexusSurface className="p-3.5" glow={monitor.connected}><SectionEyebrow>Forge insight</SectionEyebrow><p className="mt-2 text-[11px] leading-5 text-[var(--fx-text)]">{serialLogs.length ? "Forge has live runtime evidence available. Ask about reboots, sensor values, stack traces, or abnormal output and the current serial stream can be included as context." : "Once the device begins streaming, Forge can use the output as runtime evidence during debugging."}</p></NexusSurface><NexusSurface className="p-3.5"><SectionEyebrow>Connection</SectionEyebrow><div className="mt-3 space-y-3"><Metric label="Device" value={selectedBoard?.board_type ?? "—"} /><Metric label="Port" value={monitor.port ?? selectedBoard?.port ?? "—"} /><Metric label="Baud" value={String(monitor.baudrate || 115200)} /></div></NexusSurface></div>
    </div>
  </div>;
}

export function TerminalCanvas({ workspace, onBack }: { workspace: Workspace; onBack: () => void }) {
  return <div className="flex h-full min-h-0 flex-col"><CanvasHeader icon={TerminalSquare} title="Engineering console" subtitle="Terminal, problems and serial output" onBack={onBack} /><div className="min-h-0 flex-1"><BottomPanel logs={workspace.logs} workspaceLogs={workspace.workspaceLogs} stages={workspace.stages} activeProject={workspace.activeProject} height="100%" collapsed={false} maximized onClearLogs={workspace.clearLogs} /></div></div>;
}
