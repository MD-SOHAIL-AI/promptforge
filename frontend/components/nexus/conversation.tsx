"use client";

import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import hljs from "highlight.js/lib/common";
import { ArrowUp, Check, ChevronDown, Copy, Square, X } from "lucide-react";
import {
  COMPOSER_MODEL_GROUPS,
  composerProviderOptions,
  composerModelOptions,
  filterComposerModelOptions,
  type ComposerModelCatalog,
} from "@/lib/composer-model-options";
import type { ModelProviderResponse, ModelRouteResponse } from "@/types";
import { slashCommandSuggestions } from "@/lib/agent-slash-commands";

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const id = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(id);
  }, [copied]);
  return (
    <button
      className="fx-quiet-button"
      aria-label={copied ? "Copied" : "Copy"}
      onClick={() =>
        void navigator.clipboard
          .writeText(text)
          .then(() => setCopied(true))
          .catch(() => setCopied(false))
      }
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
    </button>
  );
}
function CodeBlock({
  children,
  className,
}: {
  children?: ReactNode;
  className?: string;
}) {
  const code = String(children ?? "").replace(/\n$/, "");
  const language = /language-(\w+)/.exec(className ?? "")?.[1];
  const html = useMemo(
    () =>
      language && hljs.getLanguage(language) && code.length < 32000
        ? hljs.highlight(code, { language }).value
        : null,
    [code, language],
  );
  if (!language && !code.includes("\n")) return <code>{children}</code>;
  return (
    <span className="fx-code-block">
      <span className="fx-code-toolbar">
        <span>{language ?? "text"}</span>
        <CopyButton text={code} />
      </span>
      {html ? (
        <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
      ) : (
        <code>{code}</code>
      )}
    </span>
  );
}
export const MessageBody = memo(function MessageBody({
  content,
}: {
  content: string;
}) {
  return (
    <div className="fx-prose">
      <Markdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          code: CodeBlock,
          a: ({ children, href }) => (
            <a href={href} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
          img: ({ alt }) => <span>{alt || "Image"}</span>,
        }}
      >
        {content}
      </Markdown>
    </div>
  );
});

export interface ModelPickerProps {
  providers: ModelProviderResponse[];
  routes: ModelRouteResponse[];
  catalog: ComposerModelCatalog;
  providerId?: string | null;
  modelId?: string | null;
  onLoad?: (id: string) => void | Promise<void>;
  onSelect: (provider: string, model: string) => void;
  disabled?: boolean;
}
export function ModelPicker({
  providers,
  routes,
  catalog,
  providerId,
  modelId,
  onLoad,
  onSelect,
  disabled,
}: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const availableProviders = useMemo(
    () => composerProviderOptions(providers),
    [providers],
  );
  const initialProviderId = availableProviders.some(
    (provider) => provider.provider_id === providerId,
  )
    ? providerId!
    : availableProviders[0]?.provider_id ?? "";
  const [activeProviderId, setActiveProviderId] = useState(initialProviderId);
  const root = useRef<HTMLDivElement>(null);
  const options = useMemo(
    () => composerModelOptions(providers, routes, catalog, activeProviderId),
    [providers, routes, catalog, activeProviderId],
  );
  const filteredOptions = useMemo(
    () => filterComposerModelOptions(options, query),
    [options, query],
  );
  const selectedProvider = availableProviders.find(
    (provider) => provider.provider_id === providerId,
  );
  const activeProvider = availableProviders.find(
    (provider) => provider.provider_id === activeProviderId,
  );
  useEffect(() => {
    if (!open) return;
    if (activeProviderId) void onLoad?.(activeProviderId);
    const close = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    return () => document.removeEventListener("pointerdown", close);
  }, [activeProviderId, open, onLoad]);
  return (
    <div
      ref={root}
      className="relative min-w-0"
      onKeyDown={(e) => {
        if (e.key === "Escape") setOpen(false);
      }}
    >
      <button
        disabled={disabled}
        type="button"
        className="fx-model-trigger"
        aria-label="Choose model"
        aria-expanded={open}
        onClick={() => {
          if (!open) {
            setActiveProviderId(initialProviderId);
            setQuery("");
          }
          setOpen(!open);
        }}
      >
        <span className="truncate">
          {selectedProvider ? `${selectedProvider.display_name} · ` : ""}
          {modelId || "Choose model"}
        </span>
        <ChevronDown size={13} />
      </button>
      {open ? (
        <div className="fx-model-menu" role="dialog" aria-label="Select model">
          {availableProviders.length ? (
            <>
              <label className="fx-model-provider-field">
                <span>Provider</span>
                <select
                  aria-label="Model provider"
                  value={activeProviderId}
                  onChange={(event) => {
                    setActiveProviderId(event.target.value);
                    setQuery("");
                  }}
                >
                  {availableProviders.map((provider) => (
                    <option key={provider.provider_id} value={provider.provider_id}>
                      {provider.display_name}
                    </option>
                  ))}
                </select>
              </label>
              <div className="fx-model-search">
                <input
                  aria-label={`Search ${activeProvider?.display_name ?? "provider"} models`}
                  autoFocus
                  placeholder="Search by model name or ID…"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </div>
            </>
          ) : null}
          <div className="fx-model-results">
            {COMPOSER_MODEL_GROUPS.map((group) => {
              const groupOptions = filteredOptions.filter(
                (option) => option.group === group,
              );
              if (!groupOptions.length) return null;
              return (
                <section key={group} className="fx-model-group" aria-label={`${group} models`}>
                  <div className="fx-model-group-heading">
                    <span>{group === "Other" ? "Pricing unavailable" : `${group} models`}</span>
                    <span>{groupOptions.length}</span>
                  </div>
                  {groupOptions.map((option) => (
                    <button
                      key={option.key}
                      type="button"
                      disabled={option.agentCompatibility === "unsupported"}
                      onClick={() => {
                        onSelect(option.providerId, option.modelId);
                        setOpen(false);
                      }}
                    >
                      <span className="min-w-0">
                        <strong>{option.label}</strong>
                        {option.label !== option.modelId ? <small>{option.modelId}</small> : null}
                        <small>{option.agentCompatibility === "unsupported" ? "Text generation unavailable" : "Agent compatibility unverified"}</small>
                      </span>
                      {option.providerId === providerId && option.modelId === modelId ? (
                        <Check className="shrink-0" size={14} />
                      ) : null}
                    </button>
                  ))}
                </section>
              );
            })}
            {!availableProviders.length ? (
              <p className="fx-model-empty">Connect and enable a model provider in Settings.</p>
            ) : catalog[activeProviderId] === undefined && !filteredOptions.length ? (
              <p className="fx-model-empty">Loading {activeProvider?.display_name ?? "provider"} models…</p>
            ) : !filteredOptions.length ? (
              <p className="fx-model-empty">
                {query.trim() ? `No models match “${query.trim()}”.` : "No models are available from this provider."}
              </p>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function ConversationComposer({
  draftKey,
  initialValue = "",
  placeholder,
  running,
  canStop = running,
  disabled,
  onSend,
  onStop,
  stopping = false,
  stopLabel = "Stop response",
  controls,
  context,
}: {
  draftKey: string;
  initialValue?: string;
  placeholder: string;
  running?: boolean;
  canStop?: boolean;
  disabled?: boolean;
  onSend: (value: string) => void | boolean | Promise<boolean | void>;
  onStop?: () => void;
  stopping?: boolean;
  stopLabel?: string;
  controls?: ReactNode;
  context?: ReactNode;
}) {
  const [value, setValue] = useState("");
  const [sending, setSending] = useState(false);
  const [commandIndex, setCommandIndex] = useState(0);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const latestKey = useRef(draftKey);
  latestKey.current = draftKey;
  useEffect(() => {
    setValue(initialValue || localStorage.getItem(draftKey) || "");
  }, [draftKey, initialValue]);
  useEffect(() => {
    const el = textarea.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
    }
  }, [value]);
  const commandSuggestions = useMemo(() => slashCommandSuggestions(value), [value]);
  useEffect(() => setCommandIndex(0), [value]);
  const send = async () => {
    if (!value.trim() || disabled || sending) return;
    const submittedKey = draftKey;
    const submitted = value;
    setSending(true);
    try {
      const ok = await onSend(value.trim());
      if (ok !== false) {
        localStorage.removeItem(submittedKey);
        if (latestKey.current === submittedKey || submittedKey.endsWith(":new"))
          setValue((current) => (current === submitted ? "" : current));
      }
    } finally {
      setSending(false);
    }
  };
  return (
    <div className="fx-conversation-composer">
      {context}
      {commandSuggestions.length ? (
        <div className="fx-slash-menu" role="listbox" aria-label="Agent slash commands">
          {commandSuggestions.map((command, index) => (
            <button
              key={command.name}
              type="button"
              role="option"
              aria-selected={index === commandIndex}
              className={index === commandIndex ? "is-selected" : ""}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => {
                const next = command.usage.includes("<") || command.usage.includes("[")
                  ? `/${command.name} `
                  : `/${command.name}`;
                setValue(next);
                localStorage.setItem(draftKey, next);
                requestAnimationFrame(() => textarea.current?.focus());
              }}
            >
              <strong>{command.usage}</strong>
              <span>{command.description}</span>
            </button>
          ))}
        </div>
      ) : null}
      <textarea
        ref={textarea}
        rows={2}
        aria-label="Message"
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        onChange={(e) => {
          setValue(e.target.value);
          localStorage.setItem(draftKey, e.target.value);
        }}
        onKeyDown={(e) => {
          if (commandSuggestions.length && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            setCommandIndex((current) =>
              e.key === "ArrowDown"
                ? (current + 1) % commandSuggestions.length
                : (current - 1 + commandSuggestions.length) % commandSuggestions.length,
            );
            return;
          }
          if (commandSuggestions.length && (e.key === "Tab" || e.key === "Enter")) {
            const command = commandSuggestions[commandIndex] ?? commandSuggestions[0];
            const exactNoArgumentCommand =
              e.key === "Enter" &&
              value.trim().toLowerCase() === `/${command.name}` &&
              !command.usage.includes("<") &&
              !command.usage.includes("[");
            if (exactNoArgumentCommand) {
              e.preventDefault();
              void send();
              return;
            }
            e.preventDefault();
            const next = command.usage.includes("<") || command.usage.includes("[")
              ? `/${command.name} `
              : `/${command.name}`;
            setValue(next);
            localStorage.setItem(draftKey, next);
            return;
          }
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            void send();
          }
        }}
      />
      <div className="fx-composer-controls">
        <div className="flex min-w-0 items-center gap-2">{controls}</div>
        <div className="flex items-center gap-2">
          {canStop && onStop ? (
            <button
              type="button"
              className="fx-send-button is-stop fx-task-stop"
              aria-label={stopLabel}
              disabled={stopping}
              onClick={onStop}
            >
              <Square size={14} aria-hidden="true" />
              {stopping ? "Stopping…" : "Stop"}
            </button>
          ) : null}
          <button
            type="button"
            className="fx-send-button"
            aria-label={running ? "Send guidance" : "Send message"}
            disabled={!value.trim() || disabled || sending}
            onClick={() => void send()}
          >
            <ArrowUp size={17} />
          </button>
        </div>
      </div>
    </div>
  );
}

export function ContextChip({
  label,
  onRemove,
}: {
  label: string;
  onRemove: () => void;
}) {
  return (
    <span className="fx-context-chip">
      <span className="truncate">{label}</span>
      <button aria-label={`Remove ${label}`} onClick={onRemove}>
        <X size={12} />
      </button>
    </span>
  );
}
