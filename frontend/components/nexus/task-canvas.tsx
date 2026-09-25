"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, Check, Code2, FileDiff, Hammer, Pencil, X } from "lucide-react";
import {
  ConversationComposer,
  CopyButton,
  MessageBody,
  ModelPicker,
} from "@/components/nexus/conversation";
import { ActivityStream } from "@/components/nexus/activity-stream";
import type { NexusCanvas } from "@/components/nexus/work-canvases";
import type { useForgeAgentSession } from "@/hooks/use-forge-agent-session";
import type { ComposerModelCatalog } from "@/lib/composer-model-options";
import type {
  DetectedBoard,
  ModelProviderResponse,
  ModelRouteResponse,
} from "@/types";
import { pendingWorkspaceChangeCount } from "@/lib/nexus-agent-view";
import { streamedAgentText } from "@/lib/activity-stream";
import { modelPauseMessage } from "@/lib/agent-recovery";
import { AGENT_SLASH_COMMANDS, parseAgentSlashCommand } from "@/lib/agent-slash-commands";

type Agent = ReturnType<typeof useForgeAgentSession>;

function QueueDock({ agent }: { agent: Agent }) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  if (!agent.guidanceQueue.length) return null;
  return (
    <div className="fx-guidance-queue" aria-label="Queued agent guidance">
      <div className="fx-guidance-queue-head">
        <span>Queued guidance</span>
        <small>{agent.guidanceQueue.length}</small>
      </div>
      <div className="fx-guidance-queue-list">
        {agent.guidanceQueue.map((item) => {
          const editing = editingId === item.item_id;
          return (
            <div className="fx-guidance-item" key={item.item_id}>
              {editing ? (
                <input
                  autoFocus
                  value={draft}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setEditingId(null);
                    if (event.key === "Enter" && draft.trim()) {
                      event.preventDefault();
                      void agent.editGuidance(item.item_id, draft.trim()).then(() =>
                        setEditingId(null),
                      );
                    }
                  }}
                  aria-label="Edit queued guidance"
                />
              ) : (
                <span title={item.content}>{item.content}</span>
              )}
              <div className="fx-guidance-actions">
                {editing ? (
                  <button
                    type="button"
                    aria-label="Save queued guidance"
                    onClick={() => {
                      if (!draft.trim()) return;
                      void agent.editGuidance(item.item_id, draft.trim()).then(() =>
                        setEditingId(null),
                      );
                    }}
                  >
                    <Check size={13} />
                  </button>
                ) : (
                  <button
                    type="button"
                    aria-label="Edit queued guidance"
                    onClick={() => {
                      setEditingId(item.item_id);
                      setDraft(item.content);
                    }}
                  >
                    <Pencil size={12} />
                  </button>
                )}
                <button
                  type="button"
                  aria-label="Remove queued guidance"
                  onClick={() => void agent.removeGuidance(item.item_id)}
                >
                  <X size={13} />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function TaskCanvas({
  agent,
  projectName,
  selectedBoard,
  selectedBoardId,
  activeEnvironment,
  modelProviders,
  modelRoutes,
  modelCatalog,
  modelSelecting,
  onProviderModelsRequest,
  onModelSelectionChange,
  onOpenCanvas,
  onViewFile,
  onWorkspaceChanged,
  initialDraft = "",
  onDraftConsumed,
}: {
  agent: Agent;
  projectName: string | null;
  selectedBoard: DetectedBoard | null;
  selectedBoardId: string | null;
  activeEnvironment: string | null;
  modelProviders: ModelProviderResponse[];
  modelRoutes: ModelRouteResponse[];
  modelCatalog: ComposerModelCatalog;
  modelSelecting: boolean;
  onProviderModelsRequest: (id: string) => void | Promise<void>;
  onModelSelectionChange: (
    provider: string,
    model: string,
  ) => void | Promise<void>;
  onOpenCanvas: (canvas: NexusCanvas) => void;
  onViewFile: (path: string) => void;
  onWorkspaceChanged: () => void;
  onOpenSessions?: () => void;
  focused?: boolean;
  onFocusToggle?: () => void;
  initialDraft?: string;
  onDraftConsumed?: () => void;
}) {
  const [mode, setMode] = useState<"auto" | "plan_only">("auto");
  const [latest, setLatest] = useState(false);
  const [limit, setLimit] = useState(50);
  const [commandNotice, setCommandNotice] = useState<string | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const running = ["queued", "running", "cancelling"].includes(
    agent.run?.status ?? "",
  );
  const messages = agent.messages.filter(
    (m) => m.role === "user" || m.role === "assistant",
  );
  const route = modelRoutes.find((r) => r.task_type === "code_generation");
  const selectedProviderId = agent.activeSession?.provider_id ?? route?.provider_id;
  const selectedModelId = agent.activeSession?.model_id ?? route?.model_id;
  const pauseMessage = agent.run ? modelPauseMessage(agent.run) : null;
  const changed = pendingWorkspaceChangeCount(agent.run);
  const liveResponse = useMemo(
    () => streamedAgentText(agent.events, running ? agent.run?.run_id : null),
    [agent.events, agent.run?.run_id, running],
  );
  useEffect(() => {
    follow.current = true;
    setLimit(50);
  }, [agent.activeSession?.session_id]);
  // Follow before paint. A normal effect leaves one frame where the growing
  // request can trigger onScroll and be mistaken for a deliberate scroll-up.
  useLayoutEffect(() => {
    if (follow.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
    else setLatest(true);
  }, [agent.messages, agent.events]);
  const send = async (
    content: string,
    approved?: {
      approvedPlanId: string;
      approvedPlanRevision: number;
      approvedPlanHash: string;
    },
  ) => {
    if (!approved && content.trimStart().startsWith("/")) {
      let command;
      try {
        command = parseAgentSlashCommand(content);
      } catch (error) {
        setCommandNotice(error instanceof Error ? error.message : "Slash command is invalid.");
        return false;
      }
      if (command?.definition.scope === "local") {
        const name = command.definition.name;
        if (name === "help") {
          setCommandNotice(AGENT_SLASH_COMMANDS.map((item) => `- \`${item.usage}\` — ${item.description}`).join("\n"));
          return true;
        }
        if (name === "status") {
          setCommandNotice(
            `Run: ${agent.run?.status ?? "idle"}\nProvider: ${selectedProviderId ?? "not selected"}\nModel: ${selectedModelId ?? "not selected"}`,
          );
          return true;
        }
        if (name === "retry") {
          if (!agent.run?.recovery_actions?.includes("retry")) {
            setCommandNotice("Retry is unavailable for this run.");
            return false;
          }
          setCommandNotice(null);
          return Boolean(await agent.retry());
        }
        if (name === "cancel") {
          if (!running) {
            setCommandNotice("There is no active run to cancel.");
            return false;
          }
          await agent.cancel();
          setCommandNotice(null);
          return true;
        }
        if (name === "model") {
          document.querySelector<HTMLButtonElement>('button[aria-label="Choose model"]')?.click();
          setCommandNotice("Choose a provider and model from the picker.");
          return true;
        }
      }
    }
    setCommandNotice(null);
    if (initialDraft && agent.messages.length) await agent.createSession();
    const result = await agent.sendMessage(content, undefined, {
      autonomy: approved ? "auto" : mode,
      boardPort: selectedBoard?.port,
      boardType: selectedBoardId,
      environment: activeEnvironment,
      ...approved,
    });
    if (result) onDraftConsumed?.();
    if (result?.run?.change_set_id) onWorkspaceChanged();
    return Boolean(result);
  };
  const hasResponse = messages.some(
    (m) => m.role === "assistant" && m.run_id === agent.run?.run_id,
  );
  return (
    <div className="fx-conversation-layout">
      <div
        ref={scroll}
        className="fx-conversation-scroll"
        onScroll={() => {
          const el = scroll.current;
          if (el) {
            follow.current =
              el.scrollHeight - el.scrollTop - el.clientHeight < 80;
            setLatest(!follow.current);
          }
        }}
      >
        <div className="fx-conversation-column">
          {!messages.length ? (
            <div className="fx-conversation-empty">
              <div className="fx-eyebrow">FORGEX AGENT</div>
              <h1>
                From an idea
                <br />
                to working firmware.
              </h1>
              <p>
                {projectName
                  ? `Working in ${projectName}. Describe a change, investigate a problem, or start with a plan.`
                  : "Open a project from Projects to start an engineering task."}
              </p>
              {projectName ? (
                <div className="flex flex-wrap justify-center gap-2 pt-5">
                  <button
                    className="fx-suggestion"
                    onClick={() =>
                      void send(
                        "Inspect this project and explain its structure and build configuration.",
                      )
                    }
                  >
                    Understand this project
                  </button>
                  <button
                    className="fx-suggestion"
                    onClick={() => {
                      setMode("plan_only");
                    }}
                  >
                    Plan a change
                  </button>
                </div>
              ) : null}
            </div>
          ) : null}
          {messages.length > limit ? (
            <button
              className="fx-text-button"
              onClick={() => setLimit((n) => n + 50)}
            >
              Show earlier messages
            </button>
          ) : null}
          {messages.slice(-limit).map((message) => (
            <article
              key={message.message_id}
              className={`fx-message ${message.role === "user" ? "is-user" : "is-assistant"}`}
            >
              <div className="fx-message-label">
                {message.role === "user" ? "You" : "ForgeX"}
              </div>
              {message.role === "assistant" &&
              message.run_id &&
              !(running && message.run_id === agent.run?.run_id) &&
              messages.findLast(
                (m) => m.role === "assistant" && m.run_id === message.run_id,
              )?.message_id === message.message_id ? (
                <ActivityStream
                  events={agent.events.filter(
                    (e) => e.run_id === message.run_id,
                  )}
                  run={agent.run?.run_id === message.run_id ? agent.run : null}
                  onViewFile={onViewFile}
                />
              ) : null}
              <MessageBody content={message.content} />
              {message.role === "assistant" ? (
                <div className="fx-message-actions">
                  <CopyButton text={message.content} />
                </div>
              ) : null}
            </article>
          ))}
          {agent.run && (running || !hasResponse) ? (
            <div className="fx-message is-assistant">
              <ActivityStream
                events={agent.events.filter(
                  (e) => e.run_id === agent.run?.run_id,
                )}
                run={agent.run}
                onViewFile={onViewFile}
                onReview={() => onOpenCanvas("review")}
                onCancel={() => void agent.cancel()}
                onApprove={() => void agent.confirmFlash({
                  port: agent.run?.flash_port ?? selectedBoard?.port,
                  boardType: agent.run?.flash_board_type ?? selectedBoardId,
                  environment: activeEnvironment,
                })}
                collapseLiveThinking={Boolean(liveResponse)}
              />
              {liveResponse ? (
                <div className="fx-agent-live-response" role="status" aria-live="polite" aria-label="ForgeX streaming response">
                  <div className="fx-agent-live-label">
                    <span className="fx-working-dot" />
                    <span>ForgeX</span>
                    <small>Streaming</small>
                  </div>
                  <MessageBody content={liveResponse} />
                  <span className="fx-stream-caret" aria-hidden="true" />
                </div>
              ) : null}
            </div>
          ) : null}
          {agent.submitting && !running ? (
            <div role="status" className="fx-waiting">
              <span className="fx-working-dot" />
              Preparing your task
            </div>
          ) : null}
          {agent.run?.autonomy === "plan_only" &&
          agent.run.status === "completed" &&
          agent.activeSession?.current_plan ? (
            <div className="fx-approval">
              <div>
                <strong>Plan ready</strong>
                <p>Review the plan above, then start implementation.</p>
              </div>
              <button
                className="fx-primary-action"
                onClick={() => {
                  const plan = agent.activeSession?.current_plan;
                  if (plan) {
                    setMode("auto");
                    void send(
                      "Execute the approved plan and verify the result.",
                      {
                        approvedPlanId: plan.plan_id,
                        approvedPlanRevision: plan.revision,
                        approvedPlanHash: plan.plan_hash,
                      },
                    );
                  }
                }}
              >
                Execute plan
              </button>
            </div>
          ) : null}
          {changed || agent.run?.build_result ? (
            <div className="flex flex-wrap gap-2 py-3">
              {changed ? (
                <button
                  className="fx-suggestion"
                  onClick={() => onOpenCanvas("review")}
                >
                  <FileDiff size={14} /> {changed} changed files
                </button>
              ) : null}
              {agent.run?.build_result ? (
                <button
                  className="fx-suggestion"
                  onClick={() => onOpenCanvas("build")}
                >
                  <Hammer size={14} /> Build results
                </button>
              ) : null}
              <button
                className="fx-suggestion"
                onClick={() => onOpenCanvas("code")}
              >
                <Code2 size={14} /> Files
              </button>
            </div>
          ) : null}
          {agent.run && ["failed", "blocked", "timed_out", "paused_model_unavailable"].includes(agent.run.status) && agent.run.change_set_id && agent.run.recovery_actions?.includes("retry") ? (
            <div className="fx-approval" role="status">
              <div>
                <strong>Generated files saved as a recovery draft</strong>
                <p>The draft is isolated and unverified. Retry to repair and validate it before applying changes.</p>
              </div>
              <div className="flex gap-2">
                <button className="fx-suggestion" onClick={() => onOpenCanvas("review")}>Review draft</button>
                <button className="fx-primary-action" onClick={() => void agent.retry()}>Retry with draft</button>
              </div>
            </div>
          ) : null}
          {agent.run?.status === "failed" && agent.run.provider_diagnostics?.last_failure_code ? (
            <p role="status" className="text-xs text-[var(--fx-text-muted)]">
              Agent response: {agent.run.provider_diagnostics.last_failure_code}
              {agent.run.provider_diagnostics.response_mode ? ` (${agent.run.provider_diagnostics.response_mode})` : ""}
              {agent.run.provider_diagnostics.last_turn ? ` on turn ${agent.run.provider_diagnostics.last_turn}` : ""}.
              {agent.run.provider_diagnostics.last_repair_outcome && agent.run.provider_diagnostics.last_repair_outcome !== "not_attempted"
                ? ` Repair ${agent.run.provider_diagnostics.last_repair_outcome}.` : ""}
              {agent.run.provider_diagnostics.http_status ? ` Provider HTTP ${agent.run.provider_diagnostics.http_status}.` : ""}
              {agent.run.provider_diagnostics.last_response_shape ? ` Response shape: ${agent.run.provider_diagnostics.last_response_shape}.` : ""}
              {agent.run.provider_diagnostics.last_parse_reason ? ` Parser: ${agent.run.provider_diagnostics.last_parse_reason}.` : ""}
              {agent.run.provider_diagnostics.last_failure_origin ? ` Source: ${agent.run.provider_diagnostics.last_failure_origin}.` : ""}
            </p>
          ) : null}
          {pauseMessage ? (
            <div className="fx-approval" role="status">
              <div>
                <strong>Model temporarily unavailable</strong>
                <p>{pauseMessage}</p>
              </div>
              <div className="flex gap-2">
                <button className="fx-primary-action" onClick={() => void agent.retry()}>Retry same model</button>
                <button className="fx-suggestion" onClick={() => document.querySelector<HTMLButtonElement>('button[aria-label="Choose model"]')?.click()}>Change model</button>
              </div>
            </div>
          ) : null}
          {agent.error ? (
            <p role="alert" className="fx-inline-error">
              {agent.error}
            </p>
          ) : null}
          {commandNotice ? (
            <div className="fx-command-notice" role="status">
              <MessageBody content={commandNotice} />
            </div>
          ) : null}
        </div>
      </div>
      <div className="fx-composer-dock">
        <div className="fx-conversation-column">
          {latest ? (
            <button
              className="fx-jump-latest"
              onClick={() => {
                follow.current = true;
                if (scroll.current)
                  scroll.current.scrollTop = scroll.current.scrollHeight;
                setLatest(false);
              }}
            >
              <ArrowDown size={14} /> Live
            </button>
          ) : null}
          {agent.connectionState === "polling" && running ? (
            <p
              role="status"
              className="mb-2 text-xs text-[var(--fx-text-muted)]"
            >
              Reconnecting to live activity…
            </p>
          ) : null}
          <QueueDock agent={agent} />
          <ConversationComposer
            draftKey={`forgex-agent-draft:${agent.activeSession?.session_id ?? projectName ?? "new"}`}
            initialValue={initialDraft}
            placeholder={
              running
                ? "Guide the current task…"
                : "Describe what you want to build or change…"
            }
            disabled={!projectName || agent.submitting}
            running={running}
            onSend={send}
            onStop={() => void agent.cancel()}
            controls={
              <>
                <select
                  className="fx-mode-select"
                  aria-label="Agent mode"
                  value={mode}
                  onChange={(e) =>
                    setMode(e.target.value as "auto" | "plan_only")
                  }
                >
                  <option value="auto">Execute</option>
                  <option value="plan_only">Plan</option>
                </select>
                <ModelPicker
                  providers={modelProviders}
                  routes={modelRoutes}
                  catalog={modelCatalog}
                  providerId={selectedProviderId}
                  modelId={selectedModelId}
                  onLoad={onProviderModelsRequest}
                  onSelect={(p, m) => void onModelSelectionChange(p, m)}
                  disabled={modelSelecting || running}
                />
              </>
            }
          />
        </div>
      </div>
    </div>
  );
}
