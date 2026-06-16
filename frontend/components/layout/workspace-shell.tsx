"use client";

import { useMemo, useRef, useState } from "react";
import { Panel, PanelGroup, PanelResizeHandle, type ImperativePanelHandle } from "react-resizable-panels";

import { CodeViewer } from "@/components/code/code-viewer";
import { BuildConsole } from "@/components/console/build-console";
import { FileExplorer } from "@/components/explorer/file-explorer";
import { TaskInspector } from "@/components/inspector/task-inspector";
import { AppHeader } from "@/components/layout/app-header";
import { StatusBar } from "@/components/layout/status-bar";
import { PromptCommand } from "@/components/prompt/prompt-command";
import { WorkspaceSidebar } from "@/components/sidebar/workspace-sidebar";
import { TooltipProvider } from "@/components/ui/tooltip";
import { WorkflowTimeline } from "@/components/workflow/workflow-timeline";
import { usePromptForgeWorkspace } from "@/hooks/use-promptforge-workspace";

export function WorkspaceShell() {
  const workspace = usePromptForgeWorkspace();
  const [consoleCollapsed, setConsoleCollapsed] = useState(false);
  const consolePanelRef = useRef<ImperativePanelHandle>(null);
  const buildStatus = useMemo(() => {
    const build = workspace.stages.find((stage) => stage.key === "build");
    if (build?.status === "failed") return "failed";
    if (build?.status === "success") return "Build passed";
    return null;
  }, [workspace.stages]);

  const toggleConsole = () => {
    const panel = consolePanelRef.current;
    if (!panel) return;
    if (consoleCollapsed) panel.expand();
    else panel.collapse();
    setConsoleCollapsed((value) => !value);
  };

  return (
    <TooltipProvider delayDuration={350}>
      <main className="flex h-dvh min-w-[720px] flex-col overflow-hidden bg-background text-foreground">
        <AppHeader health={workspace.health} activeProject={workspace.activeProject} isExecuting={workspace.isExecuting} buildStatus={buildStatus} />
        <div className="workspace-grid min-h-0 flex-1">
          <WorkspaceSidebar
            health={workspace.health}
            projects={workspace.projects}
            activeProject={workspace.activeProject}
            activeView={workspace.activeView}
            buildHistory={workspace.buildHistory}
            workspaceLogs={workspace.workspaceLogs}
            onViewChange={workspace.setActiveView}
            onSelectProject={workspace.setActiveProject}
            onRefreshProjects={() => void workspace.refreshProjects()}
            onDeleteProject={() => void workspace.deleteProject()}
            onRefreshBuildHistory={() => void workspace.refreshBuildHistory()}
            onRefreshLogs={() => void workspace.refreshWorkspaceLogs()}
          />

          <section className="min-h-0 min-w-0 bg-[#12151a]">
            <PanelGroup direction="vertical" className="h-full">
              <Panel defaultSize={72} minSize={42}>
                <div className="flex h-full min-h-0 flex-col">
                  <PromptCommand isExecuting={workspace.isExecuting} onExecute={workspace.execute} />
                  <WorkflowTimeline stages={workspace.stages} />
                  <div className="grid min-h-0 flex-1 grid-cols-[320px_minmax(0,1fr)]">
                    <FileExplorer
                      entries={workspace.entries}
                      selectedPath={workspace.activePath}
                      onOpenFile={(entry) => void workspace.openFile(entry)}
                      onCreateFile={(basePath) => void workspace.createEntry("file", basePath)}
                      onCreateFolder={(basePath) => void workspace.createEntry("folder", basePath)}
                      onRename={(entry) => void workspace.renameEntry(entry)}
                      onDelete={(entry) => void workspace.deleteEntry(entry)}
                      onRefresh={() => void workspace.refreshProjectFiles()}
                    />
                    <CodeViewer
                      tabs={workspace.tabs}
                      activePath={workspace.activePath}
                      onSelectTab={(path) => {
                        void workspace.openFile(path);
                      }}
                      onCloseTab={workspace.closeTab}
                      onChange={workspace.updateTabContent}
                      onSave={(path) => void workspace.saveFile(path)}
                    />
                  </div>
                </div>
              </Panel>
              <PanelResizeHandle className="group relative h-1 bg-[#171a1f] hover:bg-[#30363d] data-[resize-handle-active]:bg-[#58a6ff]">
                <div className="absolute inset-x-0 -top-1 h-3" />
              </PanelResizeHandle>
              <Panel ref={consolePanelRef} defaultSize={28} minSize={12} collapsible collapsedSize={4} onCollapse={() => setConsoleCollapsed(true)} onExpand={() => setConsoleCollapsed(false)}>
                <BuildConsole logs={workspace.logs} collapsed={consoleCollapsed} onToggle={toggleConsole} onClear={workspace.clearLogs} />
              </Panel>
            </PanelGroup>
          </section>

          <TaskInspector
            taskId={workspace.taskId}
            executionId={workspace.executionId}
            stages={workspace.stages}
            activeProject={workspace.activeProject}
            executionTime={workspace.result?.execution_time_ms}
            generatedProject={workspace.generatedProject}
            buildResult={workspace.buildResult}
          />
        </div>
        <StatusBar socketState={workspace.socketState} error={workspace.error} />
      </main>
    </TooltipProvider>
  );
}
