"use client";

import { Clock3, FolderGit2, History, LayoutDashboard, RefreshCw, ScrollText, Trash2 } from "lucide-react";

import type { BuildHistoryItem, HealthResponse, ProjectResponse, WorkspaceLog } from "@/types";
import type { WorkspaceView } from "@/hooks/use-promptforge-workspace";

interface WorkspaceSidebarProps {
  health: HealthResponse | null;
  projects: ProjectResponse[];
  activeProject: ProjectResponse | null;
  activeView: WorkspaceView;
  buildHistory: BuildHistoryItem[];
  workspaceLogs: WorkspaceLog[];
  onViewChange: (view: WorkspaceView) => void;
  onSelectProject: (project: ProjectResponse) => void;
  onRefreshProjects: () => void;
  onDeleteProject: () => void;
  onRefreshBuildHistory: () => void;
  onRefreshLogs: () => void;
}

const navigation: Array<{ id: WorkspaceView; label: string; icon: typeof LayoutDashboard }> = [
  { id: "workspace", label: "Workspace", icon: LayoutDashboard },
  { id: "projects", label: "Projects", icon: FolderGit2 },
  { id: "builds", label: "Build History", icon: History },
  { id: "logs", label: "Logs", icon: ScrollText },
];

function formatDuration(value?: number | null) {
  if (value == null) return "n/a";
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(1)} s`;
}

export function WorkspaceSidebar({
  health,
  projects,
  activeProject,
  activeView,
  buildHistory,
  workspaceLogs,
  onViewChange,
  onSelectProject,
  onRefreshProjects,
  onDeleteProject,
  onRefreshBuildHistory,
  onRefreshLogs,
}: WorkspaceSidebarProps) {
  return (
    <aside className="flex h-full w-[280px] shrink-0 flex-col border-r border-white/10 bg-[#080c11]">
      <div className="border-b border-white/10 px-4 py-4">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded bg-[#f0b45b] text-sm font-bold text-[#16130d]">
            FX
          </div>
          <div className="min-w-0">
            <h1 className="truncate text-sm font-semibold text-white">ForgeX</h1>
            <p className="truncate text-xs text-[#8a95a3]">Embedded EDE</p>
          </div>
        </div>
        <div className="mt-4 flex items-center justify-between rounded border border-white/10 bg-white/[0.03] px-3 py-2">
          <span className="text-xs text-[#8a95a3]">API</span>
          <span className={`h-2 w-2 rounded-full ${health ? "bg-[#6be29a]" : "bg-[#ef6f6c]"}`} />
        </div>
      </div>

      <nav className="space-y-1 border-b border-white/10 p-2">
        {navigation.map((item) => {
          const Icon = item.icon;
          const active = activeView === item.id;
          return (
            <button
              key={item.id}
              className={`flex w-full items-center gap-3 rounded px-3 py-2 text-left text-sm transition ${
                active ? "bg-white/10 text-white" : "text-[#a8b2c1] hover:bg-white/5 hover:text-white"
              }`}
              onClick={() => onViewChange(item.id)}
            >
              <Icon className="h-4 w-4" />
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="min-h-0 flex-1 overflow-auto">
        {(activeView === "workspace" || activeView === "projects") && (
          <section className="p-3">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#7f8b99]">Projects</p>
              <div className="flex gap-1">
                <button className="rounded p-1.5 text-[#aab5c4] hover:bg-white/10" title="Refresh" aria-label="Refresh" onClick={onRefreshProjects}>
                  <RefreshCw className="h-4 w-4" />
                </button>
                <button
                  className="rounded p-1.5 text-[#aab5c4] hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40"
                  title="Delete project"
                  aria-label="Delete project"
                  disabled={!activeProject}
                  onClick={onDeleteProject}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
            <div className="space-y-1">
              {projects.map((project) => {
                const active = activeProject?.project_id === project.project_id;
                return (
                  <button
                    key={project.project_id}
                    className={`w-full rounded border px-3 py-2 text-left transition ${
                      active
                        ? "border-[#f0b45b]/50 bg-[#f0b45b]/10"
                        : "border-white/10 bg-white/[0.02] hover:bg-white/[0.05]"
                    }`}
                    onClick={() => onSelectProject(project)}
                  >
                    <span className="block truncate text-sm text-white">{project.project_name}</span>
                    <span className="mt-1 flex items-center gap-2 text-xs text-[#8a95a3]">
                      <Clock3 className="h-3.5 w-3.5" />
                      {project.file_count} files
                    </span>
                  </button>
                );
              })}
              {projects.length === 0 ? <p className="px-1 py-3 text-sm text-[#7f8b99]">No projects found.</p> : null}
            </div>
          </section>
        )}

        {activeView === "builds" && (
          <section className="p-3">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#7f8b99]">Builds</p>
              <button className="rounded p-1.5 text-[#aab5c4] hover:bg-white/10" title="Refresh" aria-label="Refresh" onClick={onRefreshBuildHistory}>
                <RefreshCw className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-2">
              {buildHistory.map((item) => (
                <div key={item.execution_id} className="rounded border border-white/10 bg-white/[0.03] px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm text-white">{item.execution_id}</span>
                    <span className="text-xs text-[#f0b45b]">{item.status}</span>
                  </div>
                  <div className="mt-2 grid grid-cols-2 gap-1 text-xs text-[#8a95a3]">
                    <span className="truncate">{item.board}</span>
                    <span className="text-right">{formatDuration(item.duration_ms)}</span>
                    <span className="col-span-2 truncate">{item.timestamp}</span>
                  </div>
                </div>
              ))}
              {buildHistory.length === 0 ? <p className="px-1 py-3 text-sm text-[#7f8b99]">No builds found.</p> : null}
            </div>
          </section>
        )}

        {activeView === "logs" && (
          <section className="p-3">
            <div className="mb-2 flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#7f8b99]">Logs</p>
              <button className="rounded p-1.5 text-[#aab5c4] hover:bg-white/10" title="Refresh" aria-label="Refresh" onClick={onRefreshLogs}>
                <RefreshCw className="h-4 w-4" />
              </button>
            </div>
            <div className="space-y-2">
              {workspaceLogs.map((item) => (
                <div key={`${item.execution_id}-${item.log_type}-${item.path}`} className="rounded border border-white/10 bg-white/[0.03] px-3 py-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm text-white">{item.execution_id}</span>
                    <span className="text-xs text-[#8fd2c8]">{item.log_type}</span>
                  </div>
                  <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-xs text-[#8a95a3]">
                    {item.content.trim() || "Empty log"}
                  </p>
                </div>
              ))}
              {workspaceLogs.length === 0 ? <p className="px-1 py-3 text-sm text-[#7f8b99]">No logs found.</p> : null}
            </div>
          </section>
        )}
      </div>
    </aside>
  );
}
