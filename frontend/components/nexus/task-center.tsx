"use client";

import { Archive, Check, Clock3, History, LoaderCircle, MessageSquare, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ForgeCore } from "@/components/nexus/forge-core";
import { SectionEyebrow } from "@/components/nexus/nexus-primitives";
import { useForgeXDialogs } from "@/components/ide/dialogs/forgex-dialog-provider";
import { EmptyState } from "@/components/ui/empty-state";
import { FxOverlay } from "@/components/ui/fx-overlay";
import { Skeleton } from "@/components/ui/skeleton";
import { notify } from "@/lib/notify";
import type { ForgeAgentSession } from "@/types";
import type { useForgeAgentSession } from "@/hooks/use-forge-agent-session";

type Agent = ReturnType<typeof useForgeAgentSession>;

function hiddenKey(projectId: string) {
  return `forgex-task-center:hidden:${projectId}`;
}

function titlesKey(projectId: string) {
  return `forgex-task-center:titles:${projectId}`;
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    return;
  }
}

export function TaskCenter({ agent, open, onClose, onOpenTask }: { agent: Agent; open: boolean; onClose: () => void; onOpenTask: () => void }) {
  const dialogs = useForgeXDialogs();
  const [query, setQuery] = useState("");
  const [hidden, setHidden] = useState<string[]>([]);
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const editInput = useRef<HTMLInputElement>(null);

  const projectId = agent.activeSession?.project_id ?? agent.sessions[0]?.project_id ?? "global";

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setEditingId(null);
    setHidden(readJson<string[]>(hiddenKey(projectId), []));
    setTitles(readJson<Record<string, string>>(titlesKey(projectId), {}));
  }, [open, projectId]);

  useEffect(() => {
    if (editingId) window.setTimeout(() => editInput.current?.select(), 0);
  }, [editingId]);

  const titleOf = (session: ForgeAgentSession) => titles[session.session_id] ?? session.title;

  const persistHidden = (next: string[]) => {
    setHidden(next);
    writeJson(hiddenKey(projectId), next);
  };

  const persistTitle = (sessionId: string, title: string) => {
    const next = { ...titles, [sessionId]: title };
    setTitles(next);
    writeJson(titlesKey(projectId), next);
  };

  const unhide = (sessionId: string) => persistHidden(hidden.filter((id) => id !== sessionId));

  const archiveSession = (session: ForgeAgentSession) => {
    persistHidden([...hidden, session.session_id]);
    notify.success("Task archived", {
      description: titleOf(session),
      action: { label: "Undo", onClick: () => unhide(session.session_id) },
    });
  };

  const deleteSession = async (session: ForgeAgentSession) => {
    const confirmed = await dialogs.confirmAction({
      title: "Delete task?",
      description: `"${titleOf(session)}" will be removed from your task list.`,
      confirmText: "Delete",
      cancelText: "Cancel",
      variant: "danger",
    });
    if (!confirmed) return;
    persistHidden([...hidden, session.session_id]);
    notify.success("Task deleted", { description: titleOf(session) });
  };

  const commitRename = (session: ForgeAgentSession) => {
    const next = editValue.trim();
    setEditingId(null);
    if (!next || next === titleOf(session)) return;
    persistTitle(session.session_id, next);
  };

  const recentSessions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return agent.sessions
      .filter((session) => session.session_id !== agent.activeSession?.session_id)
      .filter((session) => !hidden.includes(session.session_id))
      .filter((session) => (needle ? (titles[session.session_id] ?? session.title).toLowerCase().includes(needle) : true))
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  }, [agent.sessions, agent.activeSession?.session_id, hidden, query, titles]);

  const hasAnySessions = agent.sessions.length > 0;
  const showCurrent = Boolean(agent.activeSession && !hidden.includes(agent.activeSession.session_id));

  const rowActions = (session: ForgeAgentSession) => (
    <span className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
      <button
        onClick={(event) => {
          event.stopPropagation();
          setEditingId(session.session_id);
          setEditValue(titleOf(session));
        }}
        aria-label={`Rename ${titleOf(session)}`}
        title="Rename"
        className="grid h-6 w-6 place-items-center rounded-lg text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
      >
        <Pencil className="h-3 w-3" />
      </button>
      <button
        onClick={(event) => {
          event.stopPropagation();
          archiveSession(session);
        }}
        aria-label={`Archive ${titleOf(session)}`}
        title="Archive"
        className="grid h-6 w-6 place-items-center rounded-lg text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
      >
        <Archive className="h-3 w-3" />
      </button>
      <button
        onClick={(event) => {
          event.stopPropagation();
          void deleteSession(session);
        }}
        aria-label={`Delete ${titleOf(session)}`}
        title="Delete"
        className="grid h-6 w-6 place-items-center rounded-lg text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-error)]"
      >
        <Trash2 className="h-3 w-3" />
      </button>
    </span>
  );

  return (
    <FxOverlay open={open} onOpenChange={(next) => { if (!next) onClose(); }} title="Forge tasks" size="drawer">
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex shrink-0 items-center gap-2 border-b border-[var(--fx-border-soft)] px-3 py-2">
          <label className="flex h-8 min-w-0 flex-1 items-center gap-2 rounded-lg border border-[var(--fx-border-soft)] bg-[var(--fx-panel-elevated)] px-2.5">
            <Search className="h-3.5 w-3.5 shrink-0 text-[var(--fx-text-muted)]" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Filter tasks…"
              className="min-w-0 flex-1 bg-transparent text-[11px] text-[var(--fx-text)] outline-none placeholder:text-[var(--fx-text-muted)]"
            />
            {query ? <button onClick={() => setQuery("")} aria-label="Clear filter" className="text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]"><X className="h-3 w-3" /></button> : null}
          </label>
          <button
            onClick={() => void agent.createSession().then(() => onOpenTask())}
            className="flex h-8 shrink-0 items-center gap-1.5 rounded-lg bg-[var(--fx-accent)] px-2.5 text-[10px] font-semibold text-white shadow-[0_0_18px_var(--fx-glow)]"
            title="Start a new task"
          >
            <Plus className="h-3.5 w-3.5" /> New task
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3">
          {agent.restoring ? (
            <div className="space-y-2" aria-hidden>
              {[0, 1, 2, 3].map((row) => <Skeleton key={row} className="h-[56px] w-full rounded-xl" />)}
            </div>
          ) : (
            <>
              {showCurrent && agent.activeSession ? (
                <div className="mb-4">
                  <SectionEyebrow>Current</SectionEyebrow>
                  <button onClick={() => { onOpenTask(); onClose(); }} className="mt-2 flex w-full items-center gap-3 rounded-2xl border border-[color-mix(in_srgb,var(--fx-accent)_34%,var(--fx-border))] bg-[var(--fx-accent-faint)] p-3 text-left">
                    <ForgeCore state={agent.run?.status === "running" ? "working" : agent.run?.status === "completed" ? "complete" : "idle"} size="sm" label={false} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[11px] font-semibold text-[var(--fx-text)]">{titleOf(agent.activeSession)}</span>
                      <span className="mt-0.5 block truncate text-[9px] text-[var(--fx-text-muted)]">{agent.currentActivity?.label ?? `${agent.activeSession.message_count} messages`}</span>
                    </span>
                    {agent.run?.status === "running" ? <LoaderCircle className="h-3.5 w-3.5 animate-spin text-[var(--fx-accent)]" /> : <Check className="h-3.5 w-3.5 text-[var(--fx-success)]" />}
                  </button>
                </div>
              ) : null}

              {!hasAnySessions ? (
                <EmptyState
                  icon={History}
                  title="No tasks yet"
                  hint="Start a Forge task and it will appear here with full history."
                  action={<button onClick={() => void agent.createSession().then(() => onOpenTask())} className="rounded-lg bg-[var(--fx-accent)] px-3 py-1.5 text-[10px] font-semibold text-white shadow-[0_0_18px_var(--fx-glow)]">Start a task</button>}
                />
              ) : recentSessions.length ? (
                <>
                  <SectionEyebrow>Recent</SectionEyebrow>
                  <div className="mt-2 space-y-1">
                    {recentSessions.map((session) => (
                      <div key={session.session_id} className="group flex w-full items-center gap-3 rounded-xl px-2.5 py-2.5 text-left hover:bg-[var(--fx-hover)]">
                        {editingId === session.session_id ? (
                          <>
                            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-[var(--fx-panel-elevated)] text-[var(--fx-text-muted)]"><Clock3 className="h-3.5 w-3.5" /></span>
                            <input
                              ref={editInput}
                              value={editValue}
                              onChange={(event) => setEditValue(event.target.value)}
                              onKeyDown={(event) => {
                                if (event.key === "Enter") {
                                  event.preventDefault();
                                  commitRename(session);
                                }
                                if (event.key === "Escape") {
                                  event.stopPropagation();
                                  setEditingId(null);
                                }
                              }}
                              onBlur={() => commitRename(session)}
                              aria-label="Task name"
                              className="min-w-0 flex-1 rounded-md border border-[color-mix(in_srgb,var(--fx-accent)_40%,var(--fx-border))] bg-[var(--fx-panel-elevated)] px-2 py-1 text-[10px] font-medium text-[var(--fx-text)] outline-none"
                            />
                            <button onMouseDown={(event) => event.preventDefault()} onClick={() => commitRename(session)} aria-label="Save name" className="grid h-6 w-6 place-items-center rounded-lg text-[var(--fx-success)] hover:bg-[var(--fx-hover)]"><Check className="h-3 w-3" /></button>
                          </>
                        ) : (
                          <>
                            <button onClick={() => void agent.resumeSession(session.session_id).then(() => { onOpenTask(); onClose(); })} className="flex min-w-0 flex-1 items-center gap-3 text-left">
                              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-xl bg-[var(--fx-panel-elevated)] text-[var(--fx-text-muted)]"><Clock3 className="h-3.5 w-3.5" /></span>
                              <span className="min-w-0 flex-1">
                                <span className="block truncate text-[10px] font-medium text-[var(--fx-text)]">{titleOf(session)}</span>
                                <span className="mt-0.5 flex items-center gap-1 text-[8px] text-[var(--fx-text-muted)]"><MessageSquare className="h-2.5 w-2.5" /> {session.message_count} · {new Date(session.updated_at).toLocaleDateString()}</span>
                              </span>
                            </button>
                            {rowActions(session)}
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <EmptyState
                  icon={Search}
                  title={query ? "No matching tasks" : "Nothing here"}
                  hint={query ? `No tasks match "${query.trim()}".` : "Archived tasks stay hidden until a new activity revives them."}
                />
              )}
            </>
          )}
        </div>

        <div className="shrink-0 border-t border-[var(--fx-border-soft)] px-4 py-2 text-[8px] uppercase tracking-[.13em] text-[var(--fx-text-muted)]">
          {hasAnySessions ? `${agent.sessions.length} session${agent.sessions.length === 1 ? "" : "s"} · ${hidden.length} archived locally` : "Persistent engineering sessions"}
        </div>
      </div>
    </FxOverlay>
  );
}
