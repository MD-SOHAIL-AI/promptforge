"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { AtSign, Check, ChevronDown, CircuitBoard, CircleStop, Command, FileText, FolderOpen, Hammer, Paperclip, Radio as RadioIcon, Send, ShieldCheck, Sparkles, TextCursorInput, TriangleAlert, Waypoints, X } from "lucide-react";
import type { ChangeEvent, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ChatModelPicker } from "@/components/nexus/chat-model-picker";
import type { ModelProviderResponse, ModelRouteResponse, ProviderModelsResponse } from "@/types";

export type ForgeMode = "auto" | "ask" | "plan";
export interface ForgeComposerAttachment {
  name: string;
  size: number;
  type?: string;
  lastModified?: number;
  content?: string;
  truncated?: boolean;
  error?: string;
}

export interface ForgeComposerContext {
  references: string[];
  attachments: ForgeComposerAttachment[];
}

const modes: Array<{ id: ForgeMode; label: string; icon: typeof Sparkles; detail: string }> = [
  { id: "auto", label: "Auto", icon: Sparkles, detail: "Inspect, edit, build and verify autonomously." },
  { id: "ask", label: "Ask", icon: ShieldCheck, detail: "Stage changes and keep review in the loop." },
  { id: "plan", label: "Plan", icon: Waypoints, detail: "Read-only investigation and implementation plan." },
];

const commands = [
  { value: "/build", label: "Build firmware" },
  { value: "/flash", label: "Flash verified firmware" },
  { value: "/monitor", label: "Open serial monitor" },
  { value: "/review", label: "Review current changes" },
  { value: "/fix", label: "Fix the current failure" },
  { value: "/diagnose", label: "Diagnose current failure" },
  { value: "/verify", label: "Verify the current project" },
  { value: "/plan", label: "Switch to planning" },
];

const contextItems = [
  { id: "current-file", label: "@current-file", hint: "Active editor file", icon: FileText },
  { id: "selection", label: "@selection", hint: "Highlighted code", icon: TextCursorInput },
  { id: "latest-build", label: "@latest-build", hint: "Last build output", icon: Hammer },
  { id: "problems", label: "@problems", hint: "Diagnostics and errors", icon: TriangleAlert },
  { id: "serial", label: "@serial", hint: "Serial monitor stream", icon: RadioIcon },
  { id: "board", label: "@board", hint: "Detected board and port", icon: CircuitBoard },
  { id: "project", label: "@project", hint: "Project metadata", icon: FolderOpen },
];

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function DisabledReason({ reason, children }: { reason?: string; children: ReactNode }) {
  if (!reason) return <>{children}</>;
  return <span title={reason} className="inline-flex cursor-not-allowed">{children}</span>;
}

interface UniversalComposerProps {
  mode: ForgeMode;
  onModeChange: (mode: ForgeMode) => void;
  onSubmit: (value: string, context: ForgeComposerContext) => void | Promise<void>;
  onStop?: () => void;
  running?: boolean;
  disabled?: boolean;
  submitDisabled?: boolean;
  compact?: boolean;
  placeholder?: string;
  initialValue?: string;
  modelProviders?: ModelProviderResponse[];
  modelRoute?: ModelRouteResponse | null;
  loadProviderModels?: (providerId: string) => Promise<ProviderModelsResponse>;
  onModelChange?: (providerId: string, modelId: string) => Promise<void> | void;
}

const menuItemClass = "flex w-full cursor-pointer select-none items-center gap-2 rounded-lg px-3 py-2 text-left text-[11px] text-[var(--fx-text)] outline-none transition-colors data-[highlighted]:bg-[var(--fx-hover)] data-[disabled]:pointer-events-none data-[disabled]:opacity-50";
const menuContentClass = "z-[90] min-w-[240px] rounded-[14px] border border-[var(--fx-border)] bg-[color-mix(in_srgb,var(--fx-panel)_94%,transparent)] p-1 shadow-[0_22px_60px_rgba(0,0,0,.45)] backdrop-blur-xl";
const chipClass = "group flex max-w-full items-center gap-1 rounded-lg border border-[var(--fx-border-soft)] bg-[var(--fx-panel-elevated)] py-0.5 pl-2 pr-1 text-[10px] text-[var(--fx-text)]";

export function UniversalComposer({
  mode,
  onModeChange,
  onSubmit,
  onStop,
  running = false,
  disabled = false,
  submitDisabled = false,
  compact = false,
  placeholder,
  initialValue = "",
  modelProviders = [],
  modelRoute = null,
  loadProviderModels,
  onModelChange,
}: UniversalComposerProps) {
  const [value, setValue] = useState(initialValue);
  const [attachments, setAttachments] = useState<File[]>([]);
  const [contexts, setContexts] = useState<string[]>([]);
  const [modeOpen, setModeOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const typedContext = useRef(false);
  const typedCommand = useRef(false);
  const currentMode = modes.find((item) => item.id === mode) ?? modes[0];
  const ModeIcon = currentMode.icon;
  const canSend = Boolean(value.trim()) && !disabled && !submitDisabled;
  const rows = compact ? 1 : 3;

  useEffect(() => {
    if (!initialValue) return;
    setValue(initialValue);
    window.setTimeout(() => textarea.current?.focus(), 0);
  }, [initialValue]);

  const hint = useMemo(() => running ? "Steer Forge while the current task is running" : placeholder ?? "Build, debug, improve or explore something...", [placeholder, running]);

  const buildContext = async (): Promise<ForgeComposerContext> => {
    const payloads: ForgeComposerAttachment[] = [];
    let remainingBytes = 96_000;
    for (const file of attachments.slice(0, 8)) {
      const base: ForgeComposerAttachment = {
        name: file.name,
        size: file.size,
        type: file.type || undefined,
        lastModified: file.lastModified,
      };
      if (remainingBytes <= 0) {
        payloads.push({ ...base, truncated: true, error: "attachment_context_budget_exhausted" });
        continue;
      }
      try {
        const text = await file.text();
        const encoded = new TextEncoder().encode(text);
        const slice = encoded.slice(0, Math.min(encoded.byteLength, remainingBytes));
        const content = new TextDecoder().decode(slice);
        remainingBytes -= slice.byteLength;
        payloads.push({ ...base, content, truncated: slice.byteLength < encoded.byteLength });
      } catch {
        payloads.push({ ...base, error: "attachment_text_read_failed" });
      }
    }
    return { references: [...contexts], attachments: payloads };
  };

  const submit = async () => {
    if (!value.trim() || disabled || submitDisabled) return;
    await onSubmit(value.trim(), await buildContext());
    setValue("");
    setAttachments([]);
    setContexts([]);
    setModeOpen(false);
    setContextOpen(false);
    setCommandOpen(false);
  };

  const addAttachmentFiles = (files: FileList | null) => {
    if (!files?.length) return;
    setAttachments((current) => {
      const seen = new Set(current.map((file) => `${file.name}:${file.size}`));
      const merged = [...current];
      for (const file of Array.from(files)) {
        const key = `${file.name}:${file.size}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(file);
        }
      }
      return merged;
    });
  };

  const addContext = (id: string) => {
    setContexts((current) => current.includes(id) ? current : [...current, id]);
    setValue((current) => current.replace(/@\s*$/, ""));
    setContextOpen(false);
    window.setTimeout(() => textarea.current?.focus(), 0);
  };

  const pickCommand = (command: string) => {
    setValue((current) => `${current.replace(/\/\s*$/, "")}${command} `);
    if (command === "/plan") onModeChange("plan");
    setCommandOpen(false);
    window.setTimeout(() => textarea.current?.focus(), 0);
  };

  const handleTextareaChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const next = event.target.value;
    setValue(next);
    if (next.endsWith("@")) {
      setCommandOpen(false);
      typedContext.current = true;
      setContextOpen(true);
    } else if (next.trim() === "/") {
      setContextOpen(false);
      typedCommand.current = true;
      setCommandOpen(true);
    } else {
      setContextOpen(false);
      setCommandOpen(false);
    }
  };

  const sendBlockedReason = disabled
    ? "Composer is unavailable right now"
    : submitDisabled
      ? "Open a project before starting a task"
      : value.trim()
        ? undefined
        : "Type a prompt first";

  return (
    <div className={`fx-composer relative ${compact ? "is-compact" : ""}`}>
      <input
        ref={fileInput}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => {
          addAttachmentFiles(event.target.files);
          event.target.value = "";
        }}
      />

      {attachments.length || contexts.length ? (
        <div className="mb-1.5 flex flex-wrap items-center gap-1">
          {contexts.map((id) => {
            const meta = contextItems.find((item) => item.id === id);
            const Icon = meta?.icon ?? AtSign;
            return (
              <span key={id} className={chipClass} title={meta?.hint ?? id}>
                <Icon className="h-3 w-3 shrink-0 text-[var(--fx-accent)]" />
                <span className="font-mono">@{id}</span>
                <button onClick={() => setContexts((current) => current.filter((item) => item !== id))} aria-label={`Remove @${id} context`} className="grid h-4 w-4 place-items-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-error)]"><X className="h-2.5 w-2.5" /></button>
              </span>
            );
          })}
          {attachments.map((file) => (
            <span key={`${file.name}:${file.size}`} className={chipClass} title={`${file.name} - ${formatBytes(file.size)} - sent to Forge as a named reference`}>
              <Paperclip className="h-3 w-3 shrink-0 text-[var(--fx-accent)]" />
              <span className="max-w-[180px] truncate">{file.name}</span>
              <button onClick={() => setAttachments((current) => current.filter((item) => `${item.name}:${item.size}` !== `${file.name}:${file.size}`))} aria-label={`Remove ${file.name}`} className="grid h-4 w-4 place-items-center rounded text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-error)]"><X className="h-2.5 w-2.5" /></button>
            </span>
          ))}
        </div>
      ) : null}

      <textarea
        ref={textarea}
        value={value}
        rows={rows}
        disabled={disabled}
        onChange={handleTextareaChange}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
          if (event.key === "Escape") {
            setModeOpen(false);
            setContextOpen(false);
            setCommandOpen(false);
          }
        }}
        placeholder={hint}
        className="w-full resize-none bg-transparent px-1 py-1 text-[13px] leading-6 text-[var(--fx-text)] outline-none placeholder:text-[color-mix(in_srgb,var(--fx-text-muted)_72%,transparent)] disabled:opacity-60"
      />

      <div className="mt-2 flex items-end justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-1">
          <button type="button" onClick={() => fileInput.current?.click()} disabled={disabled} aria-label="Attach files" title="Attach files" className="fx-composer-icon disabled:pointer-events-none disabled:opacity-40"><Paperclip className="h-3.5 w-3.5" /></button>

          <DropdownMenu.Root modal={false} open={contextOpen && !disabled} onOpenChange={setContextOpen}>
            <DropdownMenu.Trigger asChild>
              <button type="button" onClick={() => { typedContext.current = false; }} disabled={disabled} aria-label="Add context" title="Add context (@)" className={`fx-composer-icon disabled:pointer-events-none disabled:opacity-40 ${contextOpen ? "bg-[var(--fx-hover)] text-[var(--fx-text)]" : ""}`}><AtSign className="h-3.5 w-3.5" /></button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content side="top" align="start" sideOffset={8} className={menuContentClass}>
                <DropdownMenu.Label className="px-3 pb-1.5 pt-2 text-[9px] font-semibold uppercase tracking-[.16em] text-[var(--fx-text-muted)]">Context</DropdownMenu.Label>
                {contextItems.map((item) => {
                  const Icon = item.icon;
                  const active = contexts.includes(item.id);
                  return (
                    <DropdownMenu.Item key={item.id} onSelect={() => addContext(item.id)} className={menuItemClass}>
                      <Icon className="h-3.5 w-3.5 shrink-0 text-[var(--fx-text-muted)]" />
                      <span className="flex-1 font-mono">{item.label}</span>
                      <span className="text-[9px] text-[var(--fx-text-muted)]">{active ? "added" : item.hint}</span>
                    </DropdownMenu.Item>
                  );
                })}
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>

          <DropdownMenu.Root modal={false} open={commandOpen && !disabled} onOpenChange={setCommandOpen}>
            <DropdownMenu.Trigger asChild>
              <button type="button" onClick={() => { typedCommand.current = false; }} disabled={disabled} aria-label="Commands" title="Commands (/)" className={`fx-composer-icon disabled:pointer-events-none disabled:opacity-40 ${commandOpen ? "bg-[var(--fx-hover)] text-[var(--fx-text)]" : ""}`}><Command className="h-3.5 w-3.5" /></button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content side="top" align="start" sideOffset={8} className={menuContentClass}>
                <DropdownMenu.Label className="px-3 pb-1.5 pt-2 text-[9px] font-semibold uppercase tracking-[.16em] text-[var(--fx-text-muted)]">Commands</DropdownMenu.Label>
                {commands.map((item) => (
                  <DropdownMenu.Item key={item.value} onSelect={() => pickCommand(item.value)} className={menuItemClass}>
                    <span className="w-[74px] shrink-0 font-mono text-[11px] text-[var(--fx-accent)]">{item.value}</span>
                    <span className="text-[10px] text-[var(--fx-text-muted)]">{item.label}</span>
                  </DropdownMenu.Item>
                ))}
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>

          <DropdownMenu.Root modal={false} open={modeOpen && !disabled} onOpenChange={setModeOpen}>
            <DropdownMenu.Trigger asChild>
              <button type="button" disabled={disabled} aria-label="Autonomy mode" title="Autonomy mode" className={`ml-1 flex items-center gap-1.5 rounded-lg px-2 py-1 text-[10px] font-medium text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:pointer-events-none disabled:opacity-40 ${modeOpen ? "bg-[var(--fx-hover)] text-[var(--fx-text)]" : ""}`}>
                <ModeIcon className="h-3.5 w-3.5 text-[var(--fx-accent)]" />
                {currentMode.label}
                <ChevronDown className="h-3 w-3" />
              </button>
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content side="top" align="start" sideOffset={8} className={`${menuContentClass} w-[310px]`}>
                <DropdownMenu.Label className="px-3 pb-1.5 pt-2 text-[9px] font-semibold uppercase tracking-[.16em] text-[var(--fx-text-muted)]">Autonomy</DropdownMenu.Label>
                <DropdownMenu.RadioGroup value={mode} onValueChange={(next) => { onModeChange(next as ForgeMode); setModeOpen(false); }}>
                  {modes.map((item) => {
                    const Icon = item.icon;
                    return (
                      <DropdownMenu.RadioItem key={item.id} value={item.id} className={`${menuItemClass} items-start gap-3 py-2.5`}>
                        <span className="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-[var(--fx-panel-elevated)]"><Icon className="h-3.5 w-3.5 text-[var(--fx-accent)]" /></span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-[11px] font-semibold">{item.label}</span>
                          <span className="mt-0.5 block text-[10px] leading-4 text-[var(--fx-text-muted)]">{item.detail}</span>
                        </span>
                        <DropdownMenu.ItemIndicator><Check className="h-3.5 w-3.5 text-[var(--fx-success)]" /></DropdownMenu.ItemIndicator>
                      </DropdownMenu.RadioItem>
                    );
                  })}
                </DropdownMenu.RadioGroup>
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>

          {loadProviderModels && onModelChange ? (
            <ChatModelPicker
              providers={modelProviders}
              activeRoute={modelRoute}
              loadProviderModels={loadProviderModels}
              onSelect={onModelChange}
              compact={compact}
              disabled={disabled}
            />
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <span className="hidden pb-0.5 text-right font-mono text-[8px] tabular-nums text-[var(--fx-text-muted)] sm:block">
            {value.length > 0 ? <span className={value.length > 6000 ? "text-[var(--fx-warning)]" : undefined}>{`${value.length} chars - ~${Math.max(1, Math.ceil(value.length / 4))} tok`}</span> : "\u00A0"}
          </span>
          {running && onStop ? (
            <button onClick={onStop} className="grid h-8 w-8 place-items-center rounded-xl border border-[color-mix(in_srgb,var(--fx-error)_38%,var(--fx-border))] bg-[var(--fx-error-soft)] text-[var(--fx-error)]" title="Stop current task"><CircleStop className="h-4 w-4" /></button>
          ) : null}
          <DisabledReason reason={!canSend ? sendBlockedReason : undefined}>
            <button onClick={submit} disabled={!canSend} aria-label={running ? "Send steer to Forge" : "Send to Forge"} className="grid h-8 w-8 place-items-center rounded-xl bg-[var(--fx-accent)] text-white shadow-[0_0_24px_var(--fx-glow)] disabled:cursor-not-allowed disabled:opacity-30" title={running ? "Steer Forge" : "Send to Forge"}><Send className="h-4 w-4" /></button>
          </DisabledReason>
        </div>
      </div>
    </div>
  );
}
