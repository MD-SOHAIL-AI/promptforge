"use client";

import { ArrowLeft, Check, CircuitBoard, Code2, FileDiff, Hammer, LoaderCircle, Radio, RefreshCw, Rocket, ShieldCheck, TerminalSquare, Usb, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Panel, PanelGroup, PanelResizeHandle, type ImperativePanelHandle } from "react-resizable-panels";
import { useVirtualizer } from "@tanstack/react-virtual";

import { EditorWorkbench } from "@/components/ide/editor-workbench";
import { useEditorPreferences } from "@/hooks/use-editor-settings";
import { IdeExplorer } from "@/components/ide/ide-explorer";
import { BottomPanel } from "@/components/ide/bottom-panel";
import { DiffView } from "@/components/nexus/diff-view";
import { Metric, NexusSurface, SectionEyebrow } from "@/components/nexus/nexus-primitives";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorNote } from "@/components/ui/error-note";
import { Skeleton } from "@/components/ui/skeleton";
import { buildMetrics } from "@/lib/nexus-agent-view";
import { promptForgeApi } from "@/lib/api";
import type { ChangeSetResponse, DetectedBoard, ProductAgentRun } from "@/types";

type Workspace = ReturnType<typeof import("@/hooks/use-promptforge-workspace").usePromptForgeWorkspace>;

export type NexusCanvas = "task" | "code" | "build" | "review" | "hardware" | "serial" | "terminal";

function ExplorerHandle() {
  return <PanelResizeHandle className="group relative w-1.5 shrink-0 transition-colors hover:bg-[color-mix(in_srgb,var(--fx-accent)_12%,transparent)] data-[resize-handle-active]:bg-[color-mix(in_srgb,var(--fx-accent)_20%,transparent)]"><span className="absolute left-1/2 top-1/2 h-10 w-[2px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--fx-border-soft)] transition-colors group-hover:bg-[var(--fx-accent)] group-hover:shadow-[0_0_12px_color-mix(in_srgb,var(--fx-accent)_55%,transparent)]" /></PanelResizeHandle>;
}

function DockHandle() {
  return <PanelResizeHandle className="group relative h-1.5 shrink-0 transition-colors hover:bg-[color-mix(in_srgb,var(--fx-accent)_12%,transparent)] data-[resize-handle-active]:bg-[color-mix(in_srgb,var(--fx-accent)_20%,transparent)]"><span className="absolute left-1/2 top-1/2 h-[2px] w-10 -translate-x-1/2 -translate-y-1/2 rounded-full bg-[var(--fx-border-soft)] transition-colors group-hover:bg-[var(--fx-accent)] group-hover:shadow-[0_0_12px_color-mix(in_srgb,var(--fx-accent)_55%,transparent)]" /></PanelResizeHandle>;
}

function useDockedRegionHeight() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [height, setHeight] = useState(280);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.height;
      if (next && Number.isFinite(next)) setHeight(Math.round(next));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return { ref, height };
}

export function CanvasHeader({ icon: Icon, title, subtitle, onBack, actions }: { icon: typeof Code2; title: string; subtitle?: string; onBack: () => void; actions?: ReactNode }) {
  return <div className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-[var(--fx-border-soft)] px-5">
    <div className="flex min-w-0 items-center gap-3">
      <button onClick={onBack} className="grid h-8 w-8 place-items-center rounded-xl text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]" title="Back to task"><ArrowLeft className="h-4 w-4" /></button>
      <span className="grid h-8 w-8 place-items-center rounded-xl bg-[var(--fx-accent-faint)] text-[var(--fx-accent)]"><Icon className="h-4 w-4" /></span>
      <span className="min-w-0"><span className="block truncate text-[12px] font-semibold text-[var(--fx-text)]">{title}</span>{subtitle ? <span className="block truncate text-[10px] text-[var(--fx-text-muted)]">{subtitle}</span> : null}</span>
    </div>
    <div className="flex items-center gap-2">{actions}</div>
  </div>;
}

export function CodeCanvas({ workspace, onBack }: { workspace: Workspace; onBack: () => void }) {
  const [explorerOpen, setExplorerOpen] = useState(true);
  const editorSettings = useEditorPreferences();
  const explorer = <aside className="h-full overflow-hidden border-r border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-panel)_62%,transparent)]"><IdeExplorer activeProject={workspace.activeProject} entries={workspace.entries} selectedPath={workspace.activePath} revealedPaths={workspace.recentlyAddedPaths} onOpenFile={workspace.openFile} onCreateFile={(base) => void workspace.createEntry("file", base)} onCreateFolder={(base) => void workspace.createEntry("folder", base)} onRename={workspace.renameEntry} onDelete={workspace.deleteEntry} onRefresh={() => void workspace.refreshProjectFiles()} /></aside>;
  const workbench = <EditorWorkbench activeProject={workspace.activeProject} selectedFile={workspace.selectedFile} tabs={workspace.tabs} activePath={workspace.activePath} onSelectTab={workspace.openFile} onCloseTab={workspace.closeTab} onChange={workspace.updateTabContent} onSave={(path) => void workspace.saveFile(path)} editorSettings={editorSettings} />;
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={Code2} title="Code canvas" subtitle={workspace.activePath ?? "Workspace source"} onBack={onBack} actions={<button onClick={() => setExplorerOpen((value) => !value)} className="fx-nexus-action">{explorerOpen ? "Hide files" : "Files"}</button>} />
    {explorerOpen ? (
      <PanelGroup direction="horizontal" className="min-h-0 flex-1" autoSaveId="nexus.codeSplit">
        <Panel defaultSize={20} minSize={12} maxSize={45} collapsible collapsedSize={0} onCollapse={() => setExplorerOpen(false)}>{explorer}</Panel>
        <ExplorerHandle />
        <Panel defaultSize={80} minSize={35}><div className="flex h-full min-h-0 flex-col">{workbench}</div></Panel>
      </PanelGroup>
    ) : <div className="flex min-h-0 flex-1 flex-col">{workbench}</div>}
  </div>;
}

export function BuildCanvas({ workspace, run, onBack, onOpenTerminal, onFlash }: { workspace: Workspace; run: ProductAgentRun | null; onBack: () => void; onOpenTerminal: () => void; onFlash: () => void }) {
  const metrics = buildMetrics(run);
  const direct = workspace.buildResult as Record<string, unknown> | null;
  const success = metrics?.success ?? Boolean(direct?.success);
  const size = metrics?.size ?? Number(direct?.build_size_bytes ?? 0);
  const warnings = metrics?.warnings ?? Number(direct?.warnings_count ?? 0);
  const board = metrics?.board || String(direct?.board ?? workspace.activeProject?.target_board ?? "Unknown board");
  const message = metrics?.message || String(direct?.message ?? (success ? "Build verified." : "No verified build yet."));
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={Hammer} title="Build artifact" subtitle={board} onBack={onBack} actions={<><button onClick={onOpenTerminal} className="fx-nexus-action"><TerminalSquare className="h-3.5 w-3.5" /> Raw output</button><button onClick={() => void workspace.buildActiveProject()} className="fx-nexus-action"><RefreshCw className="h-3.5 w-3.5" /> Build</button></>} />
    <div className="min-h-0 flex-1 overflow-auto p-5 md:p-8">
      <div className="mx-auto max-w-[900px] space-y-4">
        <NexusSurface className={`overflow-hidden p-6 ${success ? "is-success" : ""}`} glow={Boolean(workspace.toolAction === "build")}>
          <div className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-4">
              <span className={`grid h-14 w-14 place-items-center rounded-2xl ${success ? "bg-[var(--fx-success-soft)] text-[var(--fx-success)]" : "bg-[var(--fx-panel-elevated)] text-[var(--fx-text-muted)]"}`}>{workspace.toolAction === "build" ? <LoaderCircle className="h-6 w-6 animate-spin" /> : success ? <Check className="h-6 w-6" /> : <Hammer className="h-6 w-6" />}</span>
              <div><SectionEyebrow>{workspace.toolAction === "build" ? "Building" : success ? "Verified build" : "Build status"}</SectionEyebrow><div className="mt-1 text-[20px] font-semibold tracking-[-.03em] text-[var(--fx-text)]">{success ? "Firmware passed verification" : "Ready for the next build"}</div><p className="mt-1 max-w-[580px] text-[11px] leading-5 text-[var(--fx-text-muted)]">{message}</p></div>
            </div>
            {success ? <button onClick={onFlash} className="fx-primary-action"><Rocket className="h-4 w-4" /> Flash device</button> : null}
          </div>
        </NexusSurface>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <NexusSurface className="p-4"><Metric label="Target" value={board || "-"} hint={metrics?.platform || workspace.activeProject?.framework || "PlatformIO"} /></NexusSurface>
          <NexusSurface className="p-4"><Metric label="Firmware" value={size ? `${(size / 1024).toFixed(1)} KB` : "-"} hint="Verified artifact size" /></NexusSurface>
          <NexusSurface className="p-4"><Metric label="Warnings" value={String(warnings)} hint={warnings ? "Review recommended" : "Clean build"} /></NexusSurface>
          <NexusSurface className="p-4"><Metric label="Status" value={success ? "PASSED" : "IDLE"} hint={run?.updated_at ? new Date(run.updated_at).toLocaleTimeString() : "Awaiting build"} /></NexusSurface>
        </div>

        <p className="px-1 text-[10px] text-[var(--fx-text-muted)]">Budget data unavailable after build.</p>
      </div>
    </div>
  </div>;
}

export function ReviewCanvas({ run, onBack, onApply, onApplied }: { run: ProductAgentRun | null; onBack: () => void; onApply: () => Promise<unknown> | unknown; onApplied: () => void }) {
  const [changeSet, setChangeSet] = useState<ChangeSetResponse | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetchTokenRef = useRef(0);
  const load = useCallback(() => {
    if (!run?.run_id || !run.change_set_id) {
      fetchTokenRef.current += 1;
      setChangeSet(null);
      setError(null);
      setLoading(false);
      return;
    }
    const token = ++fetchTokenRef.current;
    setLoading(true);
    setError(null);
    void promptForgeApi.agentRuntimeChanges(run.run_id).then((result) => {
      if (fetchTokenRef.current !== token) return;
      setChangeSet(result.change_set);
      setSelected(result.change_set.changed_files[0]?.path ?? null);
    }).catch(() => {
      if (fetchTokenRef.current !== token) return;
      setChangeSet(null);
      setError("Staged changes could not be loaded.");
    }).finally(() => {
      if (fetchTokenRef.current === token) setLoading(false);
    });
  }, [run?.change_set_id, run?.run_id]);
  useEffect(() => { load(); }, [load]);
  const current = changeSet?.changed_files.find((file) => file.path === selected) ?? changeSet?.changed_files[0] ?? null;
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={FileDiff} title="Forge review" subtitle={changeSet ? `${changeSet.changed_files.length} files changed` : "Staged changes"} onBack={onBack} />
    <PanelGroup direction="horizontal" className="min-h-0 flex-1" autoSaveId="nexus.reviewSplit">
      <Panel defaultSize={22} minSize={12} maxSize={45} collapsible collapsedSize={0}>
        <aside className="h-full overflow-auto border-r border-[var(--fx-border-soft)] p-3">
          <SectionEyebrow>Changed files</SectionEyebrow>
          <div className="mt-3 space-y-1">{loading ? <div className="space-y-2 pt-1">{[0, 1, 2, 3].map((index) => <Skeleton key={index} className="h-8 w-full rounded-xl" />)}</div> : changeSet?.changed_files.length ? changeSet.changed_files.map((file) => <button key={file.path} onClick={() => setSelected(file.path)} className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left ${current?.path === file.path ? "bg-[var(--fx-accent-faint)] text-[var(--fx-text)]" : "text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)]"}`}><span className={`grid h-5 w-5 place-items-center rounded-md text-[9px] font-bold ${file.change_type === "created" ? "bg-[var(--fx-success-soft)] text-[var(--fx-success)]" : file.change_type === "deleted" ? "bg-[var(--fx-error-soft)] text-[var(--fx-error)]" : "bg-[var(--fx-accent-soft)] text-[var(--fx-accent)]"}`}>{file.change_type[0].toUpperCase()}</span><span className="min-w-0 flex-1 truncate text-[10px]">{file.path}</span></button>) : <div className="pt-4"><EmptyState icon={FileDiff} title="No staged changes" hint="This run has no staged ChangeSet to review yet." action={<button onClick={onBack} className="fx-nexus-action">Back to task</button>} /></div>}</div>
        </aside>
      </Panel>
      <ExplorerHandle />
      <Panel minSize={35}>
        <div className="h-full overflow-auto p-5">
          {error ? <div className="mx-auto max-w-[980px] pt-6"><ErrorNote tone="error" message={error} onRetry={load} /></div> : current ? <div className="mx-auto max-w-[980px]"><div className="mb-3 flex items-center justify-between"><div><div className="font-mono text-[12px] text-[var(--fx-text)]">{current.path}</div><div className="mt-1 text-[10px] text-[var(--fx-text-muted)]">{current.change_type} - {current.safe ? "safe" : "review required"}</div></div><span className={`rounded-full border px-2.5 py-1 text-[9px] uppercase tracking-[.12em] ${current.safe ? "border-[color-mix(in_srgb,var(--fx-success)_38%,var(--fx-border))] text-[var(--fx-success)]" : "border-[color-mix(in_srgb,var(--fx-warning)_40%,var(--fx-border))] text-[var(--fx-warning)]"}`}>{current.safe ? "low risk" : "attention"}</span></div><NexusSurface className="overflow-hidden">{current.diff_preview ? <DiffView diff={current.diff_preview} className="max-h-[64vh] overflow-auto" /> : <div className="p-4 text-[11px] text-[var(--fx-text-muted)]">Diff preview is unavailable for this file.</div>}</NexusSurface></div> : loading ? null : <div className="grid h-full place-items-center"><EmptyState icon={FileDiff} title="Select a staged file" hint="Pick a changed file on the left to inspect its diff." /></div>}
        </div>
      </Panel>
    </PanelGroup>
    {changeSet?.status === "pending" ? <div className="flex h-16 shrink-0 items-center justify-between border-t border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-panel)_84%,transparent)] px-5"><div className="flex items-center gap-2 text-[10px] text-[var(--fx-text-muted)]"><ShieldCheck className="h-4 w-4 text-[var(--fx-success)]" /> Changes are still isolated from the active workspace.</div><button onClick={() => void Promise.resolve(onApply()).then(() => onApplied())} className="fx-primary-action"><Check className="h-4 w-4" /> Apply changes</button></div> : null}
  </div>;
}

export function HardwareCanvas({ workspace, selectedBoard, onSelectBoard, onBack }: { workspace: Workspace; selectedBoard: DetectedBoard | null; onSelectBoard: (port: string) => void; onBack: () => void }) {
  const connected = Boolean(selectedBoard);
  const monitor = workspace.monitorStatus ?? { state: "idle", connected: false, port: null, baudrate: 115200, metrics: {} };
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={CircuitBoard} title="Hardware canvas" subtitle={connected ? `${selectedBoard?.board_type} - ${selectedBoard?.port}` : "No connected device"} onBack={onBack} actions={<button className="fx-nexus-action" onClick={() => void workspace.refreshDetectedBoards()}><RefreshCw className="h-3.5 w-3.5" /> Detect</button>} />
    <div className="min-h-0 flex-1 overflow-auto p-5 md:p-8">
      <div className="mx-auto max-w-[980px] space-y-4">
        <NexusSurface className="relative overflow-hidden p-6" glow={connected}>
          <div className="fx-device-grid" />
          <div className="relative flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-5"><span className={`grid h-20 w-20 place-items-center rounded-[24px] border ${connected ? "border-[color-mix(in_srgb,var(--fx-info)_40%,var(--fx-border))] bg-[color-mix(in_srgb,var(--fx-info)_9%,var(--fx-panel))] text-[var(--fx-info)] shadow-[0_0_40px_color-mix(in_srgb,var(--fx-info)_14%,transparent)]" : "border-[var(--fx-border)] bg-[var(--fx-panel-elevated)] text-[var(--fx-text-muted)]"}`}><CircuitBoard className="h-9 w-9" /></span><div><SectionEyebrow>{connected ? "Connected device" : "Hardware"}</SectionEyebrow><div className="mt-1 text-[22px] font-semibold tracking-[-.035em] text-[var(--fx-text)]">{selectedBoard?.board_type ?? "Connect a board"}</div><div className="mt-1 flex items-center gap-2 text-[10px] text-[var(--fx-text-muted)]">{connected ? <><span className="h-1.5 w-1.5 rounded-full bg-[var(--fx-success)] shadow-[0_0_10px_var(--fx-success)]" /> Live on {selectedBoard?.port}</> : "Forge will detect supported serial devices."}</div></div></div>
            {connected ? <div className="flex gap-2"><button onClick={() => void workspace.startSerialMonitor(selectedBoard)} className="fx-nexus-action"><Radio className="h-3.5 w-3.5" /> Monitor</button><button onClick={() => void workspace.flashActiveProject(selectedBoard)} className="fx-primary-action"><Rocket className="h-4 w-4" /> Flash</button></div> : null}
          </div>
        </NexusSurface>

        <div className="grid gap-3 md:grid-cols-3"><NexusSurface className="p-4"><Metric label="Port" value={selectedBoard?.port ?? "-"} hint={selectedBoard?.manufacturer ?? "USB / serial"} /></NexusSurface><NexusSurface className="p-4"><Metric label="Environment" value={String(workspace.activeProject?.metadata?.active_environment ?? "auto")} hint={workspace.activeProject?.framework ?? "PlatformIO"} /></NexusSurface><NexusSurface className="p-4"><Metric label="Serial" value={monitor.connected ? `${monitor.baudrate} baud` : "Idle"} hint={monitor.connected ? "Monitor active" : "Ready"} /></NexusSurface></div>

        <NexusSurface className="p-5"><div className="flex items-center justify-between"><SectionEyebrow>Detected hardware</SectionEyebrow><span className="text-[10px] text-[var(--fx-text-muted)]">{workspace.detectedBoards.length} devices</span></div><div className="mt-3 grid gap-2 md:grid-cols-2">{workspace.detectedBoards.map((board) => <button key={board.port} onClick={() => onSelectBoard(board.port)} className={`flex items-center gap-3 rounded-xl border p-3 text-left ${selectedBoard?.port === board.port ? "border-[color-mix(in_srgb,var(--fx-accent)_40%,var(--fx-border))] bg-[var(--fx-accent-faint)]" : "border-[var(--fx-border-soft)] hover:bg-[var(--fx-hover)]"}`}><Usb className="h-4 w-4 text-[var(--fx-info)]" /><span className="min-w-0 flex-1"><span className="block truncate text-[11px] font-medium text-[var(--fx-text)]">{board.board_type}</span><span className="block truncate text-[9px] text-[var(--fx-text-muted)]">{board.port} - {board.description ?? "serial device"}</span></span>{selectedBoard?.port === board.port ? <Check className="h-3.5 w-3.5 text-[var(--fx-success)]" /> : null}</button>)}</div></NexusSurface>
      </div>
    </div>
  </div>;
}

export function SerialCanvas({ workspace, selectedBoard, onBack }: { workspace: Workspace; selectedBoard: DetectedBoard | null; onBack: () => void }) {
  const monitor = workspace.monitorStatus ?? { state: "idle", connected: false, port: null, baudrate: 115200, metrics: {} };
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const serialLogs = useMemo(() => workspace.serialMonitor.events.map((event) => ({
    key: event.sequence,
    line: event.line,
    source: event.source,
  })).slice(-500), [workspace.serialMonitor.events]);
  const rows = useVirtualizer({
    count: serialLogs.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 29,
    overscan: 12,
  });
  useEffect(() => {
    if (serialLogs.length > 0) rows.scrollToIndex(serialLogs.length - 1, { align: "end" });
  }, [rows, serialLogs.length]);
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={Radio} title="Live device" subtitle={monitor.connected ? `${monitor.port} - ${monitor.baudrate}` : "Serial monitor idle"} onBack={onBack} actions={monitor.connected ? <button className="fx-nexus-action text-[var(--fx-error)]" onClick={() => void workspace.stopSerialMonitor()}><X className="h-3.5 w-3.5" /> Stop</button> : <button className="fx-primary-action" disabled={!selectedBoard} onClick={() => selectedBoard && void workspace.startSerialMonitor(selectedBoard)}><Radio className="h-4 w-4" /> Start monitor</button>} />
    <PanelGroup direction="horizontal" className="min-h-0 flex-1" autoSaveId="nexus.serialSplit">
      <Panel defaultSize={70} minSize={45}>
        <div className="h-full p-5 pr-3">
          <NexusSurface className="h-full overflow-hidden">
            <div className="flex items-center justify-between border-b border-[var(--fx-border-soft)] px-4 py-3">
              <SectionEyebrow>Serial stream</SectionEyebrow>
              <div className="flex items-center gap-1.5 text-[9px] uppercase tracking-[.12em] text-[var(--fx-text-muted)]">
                <span className={`h-1.5 w-1.5 rounded-full ${monitor.connected && workspace.serialMonitor.streaming ? "bg-[var(--fx-success)] fx-live-dot" : "bg-[var(--fx-text-muted)]"}`} />
                {monitor.connected && workspace.serialMonitor.streaming ? "live" : "offline"}
              </div>
            </div>
            <div ref={scrollRef} className="h-[calc(100%-42px)] overflow-auto p-4 font-mono text-[11px] leading-5 text-[var(--fx-terminal-text)]">
              {serialLogs.length ? <div className="relative w-full" style={{ height: rows.getTotalSize() }}>
                {rows.getVirtualItems().map((row) => {
                  const item = serialLogs[row.index];
                  return <div
                    key={item.key}
                    className={`absolute left-0 top-0 w-full truncate border-b border-[color-mix(in_srgb,var(--fx-border-soft)_40%,transparent)] py-1 ${item.source === "OVERFLOW" ? "text-[var(--fx-warning)]" : item.source === "RECONNECT" ? "text-[var(--fx-info)]" : ""}`}
                    style={{ transform: `translateY(${row.start}px)` }}
                    title={item.line}
                  >
                    <span className="mr-3 text-[8px] text-[var(--fx-text-muted)]">{String(row.index + 1).padStart(3, "0")}</span>
                    {item.line}
                  </div>;
                })}
              </div> : <div className="grid h-full place-items-center"><EmptyState icon={Radio} title="No serial telemetry yet" hint="Start the monitor to stream device output into Forge." action={!monitor.connected && selectedBoard ? <button onClick={() => void workspace.startSerialMonitor(selectedBoard)} className="fx-primary-action"><Radio className="h-4 w-4" /> Start monitor</button> : undefined} /></div>}
            </div>
          </NexusSurface>
        </div>
      </Panel>
      <ExplorerHandle />
      <Panel defaultSize={30} minSize={18}>
        <div className="flex h-full flex-col gap-3 overflow-auto p-5 pl-3">
          <NexusSurface className="shrink-0 p-4" glow={monitor.connected}><SectionEyebrow>Forge insight</SectionEyebrow><p className="mt-2 text-[11px] leading-5 text-[var(--fx-text)]">{serialLogs.length ? "Forge has live runtime evidence available. Ask about reboots, sensor values, stack traces, or abnormal output and the current serial stream can be included as context." : "Once the device begins streaming, Forge can use the output as runtime evidence during debugging."}</p></NexusSurface>
          <NexusSurface className="shrink-0 p-4"><SectionEyebrow>Connection</SectionEyebrow><div className="mt-3 space-y-3"><Metric label="Device" value={selectedBoard?.board_type ?? "-"} /><Metric label="Port" value={monitor.port ?? selectedBoard?.port ?? "-"} /><Metric label="Baud" value={String(monitor.baudrate || 115200)} /></div></NexusSurface>
        </div>
      </Panel>
    </PanelGroup>
  </div>;
}

export function TerminalCanvas({ workspace, onBack }: { workspace: Workspace; onBack: () => void }) {
  const consolePanelRef = useRef<ImperativePanelHandle | null>(null);
  const [consoleCollapsed, setConsoleCollapsed] = useState(false);
  const dock = useDockedRegionHeight();
  return <div className="flex h-full min-h-0 flex-col">
    <CanvasHeader icon={TerminalSquare} title="Engineering console" subtitle="Terminal, problems and serial output" onBack={onBack} />
    <PanelGroup direction="vertical" className="min-h-0 flex-1" autoSaveId="nexus.consoleSplit">
      <Panel minSize={25}>
        <div className="grid h-full place-items-center overflow-auto p-6">
          <EmptyState icon={TerminalSquare} title="Console workspace" hint="Your last working canvas lives here; the terminal, problems and serial monitor stay docked below." action={<button onClick={onBack} className="fx-nexus-action"><ArrowLeft className="h-3.5 w-3.5" /> Back to task</button>} />
        </div>
      </Panel>
      <DockHandle />
      <Panel ref={consolePanelRef} defaultSize={35} minSize={15} collapsible collapsedSize={6} onCollapse={() => setConsoleCollapsed(true)} onExpand={() => setConsoleCollapsed(false)}>
        <div ref={dock.ref} className="h-full min-h-0"><BottomPanel logs={workspace.logs} workspaceLogs={workspace.workspaceLogs} serialEvents={workspace.serialMonitor.events} stages={workspace.stages} activeProject={workspace.activeProject} height={dock.height || 280} collapsed={consoleCollapsed} maximized={false} onToggleCollapsed={() => consoleCollapsed || consolePanelRef.current?.collapse()} onToggleMaximized={() => undefined} onClearLogs={workspace.clearLogs} /></div>
      </Panel>
    </PanelGroup>
  </div>;
}
