"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowRight, Plus, RotateCcw } from "lucide-react";
import { ChatResponseActivity } from "@/components/agent-activity/chat-response-activity";
import {
  ConversationComposer,
  CopyButton,
  ContextChip,
  MessageBody,
  ModelPicker,
} from "@/components/nexus/conversation";
import { promptForgeApi } from "@/lib/api";
import type { useChatSession } from "@/hooks/use-chat-session";
import type { ComposerModelCatalog } from "@/lib/composer-model-options";
import type {
  ModelProviderResponse,
  ModelRouteResponse,
  ProjectResponse,
} from "@/types";

export function ChatCanvas({
  chat,
  projects,
  providers,
  routes,
  catalog,
  onLoadModels,
  onHandoff,
}: {
  chat: ReturnType<typeof useChatSession>;
  projects: ProjectResponse[];
  providers: ModelProviderResponse[];
  routes: ModelRouteResponse[];
  catalog: ComposerModelCatalog;
  onLoadModels: (id: string) => void | Promise<void>;
  onHandoff: (text: string, projectId: string | null) => void;
}) {
  const [selection, setSelection] = useState<{
    provider: string;
    model: string;
  } | null>(null);
  const [projectId, setProjectId] = useState("");
  const [paths, setPaths] = useState<string[]>([]);
  const [files, setFiles] = useState<string[]>([]);
  const [contextOpen, setContextOpen] = useState(false);
  const [contextError, setContextError] = useState<string | null>(null);
  const [latest, setLatest] = useState(false);
  const [limit, setLimit] = useState(50);
  const scroll = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const messages = useMemo(
    () => chat.session?.messages ?? [],
    [chat.session?.messages],
  );
  const route = routes.find((r) => r.task_type === "general_chat");
  const provider =
    selection?.provider ?? chat.session?.provider_id ?? route?.provider_id;
  const model = selection?.model ?? chat.session?.model_id ?? route?.model_id;
  useEffect(() => {
    setSelection(null);
    setProjectId("");
    setPaths([]);
    setLimit(50);
    follow.current = true;
  }, [chat.session?.session_id]);
  useEffect(() => {
    setFiles([]);
    setPaths([]);
    setContextError(null);
    if (!projectId) return;
    let disposed = false;
    void promptForgeApi
      .projectFiles(projectId)
      .then((result) => {
        if (!disposed)
          setFiles(
            result.entries.filter((e) => e.kind === "file").map((e) => e.path),
          );
      })
      .catch(() => {
        if (!disposed) setContextError("Project files could not be loaded.");
      });
    return () => {
      disposed = true;
    };
  }, [projectId]);
  useLayoutEffect(() => {
    if (follow.current && scroll.current)
      scroll.current.scrollTop = scroll.current.scrollHeight;
    else setLatest(true);
  }, [messages]);
  const options = {
    provider_id: provider || undefined,
    model_id: model || undefined,
    context_project_id: projectId || undefined,
    context_paths: paths,
  };
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
              <div className="fx-eyebrow">FORGEX CHAT</div>
              <h1>
                A little clarity.
                <br />A good place to start.
              </h1>
              <p>
                Ask a question, explore an idea, or bring your project into the
                conversation.
              </p>
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
          {messages.slice(-limit).map((message, index) => (
            <article
              className={`fx-message ${message.role === "user" ? "is-user" : "is-assistant"}`}
              key={message.message_id}
            >
              <div className="fx-message-label">
                {message.role === "user" ? "You" : "ForgeX"}
              </div>
              {message.content ? (
                <>
                  {message.metadata?.status === "streaming" ? <ChatResponseActivity startedAt={message.created_at} responding contextCount={paths.length} /> : null}
                  <MessageBody content={message.content} />
                </>
              ) : message.metadata?.status === "streaming" ? (
                <ChatResponseActivity startedAt={message.created_at} responding={false} contextCount={paths.length} />
              ) : null}
              {message.role === "assistant" ? (
                <div className="fx-message-actions">
                  <CopyButton text={message.content} />
                  {message.metadata?.status !== "streaming" ? (
                    <>
                      <button
                        className="fx-quiet-button"
                        title="Send to Agent"
                        onClick={() => {
                          const before = messages
                            .slice(0, messages.indexOf(message))
                            .findLast((m) => m.role === "user");
                          onHandoff(
                            `Task discussed in Chat:\n${before?.content ?? ""}\n\nReference answer:\n${message.content}`,
                            projectId || null,
                          );
                        }}
                      >
                        <ArrowRight size={14} /> <span>Send to Agent</span>
                      </button>
                      {index === Math.min(limit, messages.length) - 1 ? (
                        <button
                          className="fx-quiet-button"
                          disabled={chat.running}
                          title="Retry response"
                          onClick={() =>
                            void chat.send("Retry", { ...options, retry: true })
                          }
                        >
                          <RotateCcw size={14} />
                        </button>
                      ) : null}
                    </>
                  ) : null}
                  <span className="ml-auto text-xs text-[var(--fx-text-muted)]">
                    {message.metadata?.status === "streaming"
                      ? "Responding"
                      : message.metadata?.status === "completed"
                        ? String(message.metadata?.model_id ?? "")
                        : String(message.metadata?.status ?? "")}
                  </span>
                </div>
              ) : null}
            </article>
          ))}
          {chat.error ? (
            <p role="alert" className="fx-inline-error">
              {chat.error}
            </p>
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
          {chat.connection === "reconnecting" ? (
            <p
              role="status"
              className="mb-2 text-xs text-[var(--fx-text-muted)]"
            >
              Connection interrupted. Reconnecting…
            </p>
          ) : null}
          {contextOpen ? (
            <div className="fx-context-picker">
              <label>
                Project context
                <select
                  aria-label="Attach project"
                  value={projectId}
                  onChange={(e) => setProjectId(e.target.value)}
                >
                  <option value="">No project attached</option>
                  {projects.map((p) => (
                    <option key={p.project_id} value={p.project_id}>
                      {p.project_name}
                    </option>
                  ))}
                </select>
              </label>
              {contextError ? <p role="alert">{contextError}</p> : null}
              <div className="max-h-40 overflow-auto">
                {files.map((path) => (
                  <label key={path} className="flex gap-2 py-1">
                    <input
                      type="checkbox"
                      checked={paths.includes(path)}
                      disabled={!paths.includes(path) && paths.length >= 12}
                      onChange={(e) =>
                        setPaths((old) =>
                          e.target.checked
                            ? [...old, path]
                            : old.filter((p) => p !== path),
                        )
                      }
                    />
                    <span className="truncate">{path}</span>
                  </label>
                ))}
              </div>
              <button
                className="fx-text-button"
                onClick={() => setContextOpen(false)}
              >
                Done
              </button>
            </div>
          ) : null}
          <ConversationComposer
            draftKey={`forgex-chat-draft:${chat.session?.session_id ?? "new"}`}
            placeholder="Ask anything, or add project context…"
            running={chat.running}
            disabled={chat.busy || chat.running}
            onSend={(value) => chat.send(value, options)}
            onStop={() => void chat.cancel()}
            context={
              projectId ? (
                <div className="flex flex-wrap gap-2 pb-2">
                  <ContextChip
                    label={
                      projects.find((p) => p.project_id === projectId)
                        ?.project_name ?? "Project"
                    }
                    onRemove={() => setProjectId("")}
                  />
                  {paths.map((path) => (
                    <ContextChip
                      key={path}
                      label={path}
                      onRemove={() =>
                        setPaths((old) => old.filter((p) => p !== path))
                      }
                    />
                  ))}
                </div>
              ) : null
            }
            controls={
              <>
                <button
                  className="fx-quiet-button"
                  aria-label="Attach project context"
                  aria-expanded={contextOpen}
                  onClick={() => setContextOpen(!contextOpen)}
                >
                  <Plus size={17} />
                </button>
                <ModelPicker
                  providers={providers}
                  routes={routes}
                  catalog={catalog}
                  providerId={provider}
                  modelId={model}
                  onLoad={onLoadModels}
                  disabled={chat.running}
                  onSelect={(p, m) => {
                    setSelection({ provider: p, model: m });
                    if (chat.session)
                      void promptForgeApi
                        .configureChat(chat.session.session_id, {
                          provider_id: p,
                          model_id: m,
                        })
                        .catch(() =>
                          setContextError(
                            "Model selection could not be saved.",
                          ),
                        );
                  }}
                />
              </>
            }
          />
        </div>
      </div>
    </div>
  );
}
