"use client";

import { Braces, Copy, FileCode2, Save, X } from "lucide-react";
import Editor, { type Monaco, useMonaco } from "@monaco-editor/react";
import { useEffect, useState } from "react";

import type { EditorTab } from "@/types";

interface CodeViewerProps {
  tabs: EditorTab[];
  activePath?: string | null;
  onSelectTab: (path: string) => void;
  onCloseTab: (path: string) => void;
  onChange: (path: string, content: string) => void;
  onSave: (path: string) => void;
  editorSettings?: {
    fontSize?: number;
    tabSize?: number;
    wordWrap?: boolean;
    minimapEnabled?: boolean;
    showLineNumbers?: boolean;
  };
}

const FORGEX_EDITOR_THEME = "forgex-runtime";

function cssColor(name: string, fallback: string) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const candidate = value || fallback;
  if (/^#[0-9a-f]{6}$/i.test(candidate)) return candidate;
  if (/^#[0-9a-f]{3}$/i.test(candidate)) {
    return `#${candidate.slice(1).split("").map((part) => part + part).join("")}`;
  }
  return fallback;
}

function alpha(color: string, opacity: string) {
  return `${color}${opacity}`;
}

function defineForgeXEditorTheme(monaco: Monaco) {
  const root = document.documentElement;
  const isLight = new Set(["porcelain", "paper", "dawn", "solarized-light", "high-contrast-light", "light"]).has(root.dataset.theme ?? "");
  const background = cssColor("--fx-bg", isLight ? "#f4f6f8" : "#0b1117");
  const panel = cssColor("--fx-panel", isLight ? "#ffffff" : "#111821");
  const border = cssColor("--fx-border", isLight ? "#b8c5d4" : "#243040");
  const foreground = cssColor("--fx-code-text", isLight ? "#1f2a3d" : "#d7dee8");
  const muted = cssColor("--fx-text-muted", isLight ? "#536173" : "#8b949e");
  const accent = cssColor("--fx-accent", "#7c5cff");
  const info = cssColor("--fx-info", "#38bdf8");
  const success = cssColor("--fx-success", "#22c55e");
  const warning = cssColor("--fx-warning", "#f59e0b");
  const error = cssColor("--fx-error", "#ef4444");
  const syntax = (color: string) => color.slice(1);

  monaco.editor.defineTheme(FORGEX_EDITOR_THEME, {
    base: isLight ? "vs" : "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: syntax(muted), fontStyle: "italic" },
      { token: "keyword", foreground: syntax(accent) },
      { token: "string", foreground: syntax(success) },
      { token: "number", foreground: syntax(info) },
      { token: "type", foreground: syntax(warning) },
      { token: "type.identifier", foreground: syntax(warning) },
      { token: "identifier", foreground: syntax(foreground) },
      { token: "delimiter", foreground: syntax(muted) },
      { token: "invalid", foreground: syntax(error) },
    ],
    colors: {
      "editor.background": background,
      "editor.foreground": foreground,
      "editorGutter.background": background,
      "editorLineNumber.foreground": alpha(muted, "A6"),
      "editorLineNumber.activeForeground": foreground,
      "editorCursor.foreground": accent,
      "editor.selectionBackground": alpha(accent, "45"),
      "editor.inactiveSelectionBackground": alpha(accent, "25"),
      "editor.lineHighlightBackground": alpha(panel, isLight ? "80" : "B8"),
      "editor.lineHighlightBorder": "#00000000",
      "editorIndentGuide.background1": alpha(border, "90"),
      "editorIndentGuide.activeBackground1": accent,
      "editorWhitespace.foreground": alpha(muted, "52"),
      "editorWidget.background": panel,
      "editorWidget.border": border,
      "editorSuggestWidget.background": panel,
      "editorSuggestWidget.border": border,
      "editorSuggestWidget.selectedBackground": alpha(accent, "2F"),
      "editorHoverWidget.background": panel,
      "editorHoverWidget.border": border,
      "editorOverviewRuler.border": "#00000000",
      "minimap.background": background,
      "scrollbarSlider.background": alpha(border, "80"),
      "scrollbarSlider.hoverBackground": alpha(muted, "80"),
      "scrollbarSlider.activeBackground": alpha(accent, "80"),
    },
  });
}

export function CodeViewer({
  tabs,
  activePath,
  onSelectTab,
  onCloseTab,
  onChange,
  onSave,
  editorSettings,
}: CodeViewerProps) {
  const monaco = useMonaco();
  const [themeRevision, setThemeRevision] = useState(0);
  const active = tabs.find((tab) => tab.path === activePath) ?? tabs[0] ?? null;
  const fontSize = Number(editorSettings?.fontSize ?? 14);
  const tabSize = Number(editorSettings?.tabSize ?? 2);

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeRevision((value) => value + 1));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "style"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!monaco) return;
    defineForgeXEditorTheme(monaco);
    monaco.editor.setTheme(FORGEX_EDITOR_THEME);
  }, [monaco, themeRevision]);

  const copy = async () => {
    if (!active) return;
    await navigator.clipboard.writeText(active.content);
  };

  return (
    <section className="flex h-full min-h-0 flex-col bg-[var(--fx-bg)]">
      <div className="flex min-h-11 items-center justify-between border-b border-[var(--fx-border)] bg-[var(--fx-panel)]">
        <div className="flex min-w-0 flex-1 overflow-x-auto">
          {tabs.length > 0 ? (
            tabs.map((tab) => (
              <button
                key={tab.path}
                className={`group relative flex h-11 max-w-[240px] items-center gap-2 border-r border-[var(--fx-border)] px-3 text-sm ${
                  active?.path === tab.path
                    ? "bg-[var(--fx-bg)] text-[var(--fx-text)]"
                    : "bg-[var(--fx-panel)] text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                }`}
                onClick={() => onSelectTab(tab.path)}
              >
                <FileCode2 className="h-4 w-4 shrink-0 text-[var(--fx-info)]" />
                <span className="min-w-0 truncate">{tab.path.split("/").at(-1)}</span>
                {tab.dirty ? <span className="h-2 w-2 shrink-0 rounded-full bg-[var(--fx-warning)]" /> : null}
                <span
                  role="button"
                  tabIndex={0}
                  className="rounded p-0.5 opacity-60 hover:bg-[var(--fx-hover)] hover:opacity-100"
                  onClick={(event) => {
                    event.stopPropagation();
                    onCloseTab(tab.path);
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      event.stopPropagation();
                      onCloseTab(tab.path);
                    }
                  }}
                >
                  <X className="h-3.5 w-3.5" />
                </span>
                {active?.path === tab.path ? <span className="absolute inset-x-0 top-0 h-0.5 bg-[var(--fx-accent)]" /> : null}
              </button>
            ))
          ) : (
            <div className="flex h-11 items-center gap-2 px-3 text-sm text-[var(--fx-text-muted)]">
              <Braces className="h-4 w-4" />
              No file open
            </div>
          )}
        </div>
        <div className="flex items-center gap-1 px-2 text-[var(--fx-text-muted)]">
          <button
            className="rounded p-1.5 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-40"
            title="Save"
            aria-label="Save"
            disabled={!active || !active.dirty}
            onClick={() => active && onSave(active.path)}
          >
            <Save className="h-4 w-4" />
          </button>
          <button
            className="rounded p-1.5 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-40"
            title="Copy"
            aria-label="Copy"
            disabled={!active}
            onClick={copy}
          >
            <Copy className="h-4 w-4" />
          </button>
        </div>
      </div>

      {active ? (
        <>
          <div className="flex h-9 items-center justify-between border-b border-[var(--fx-border)] px-4 text-xs text-[var(--fx-text-muted)]">
            <span className="truncate">{active.path}</span>
            <span>{active.language}</span>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">
            <Editor
              key={active.path}
              path={active.path}
              value={active.content}
              language={active.language}
              theme={FORGEX_EDITOR_THEME}
              beforeMount={defineForgeXEditorTheme}
              loading={<div className="flex h-full items-center justify-center bg-[var(--fx-bg)] text-xs text-[var(--fx-text-muted)]">Loading editor...</div>}
              onChange={(value) => onChange(active.path, value ?? "")}
              options={{
                automaticLayout: true,
                fontFamily: "var(--font-geist-mono), Consolas, monospace",
                fontSize: Number.isFinite(fontSize) ? fontSize : 14,
                lineNumbersMinChars: 3,
                minimap: { enabled: Boolean(editorSettings?.minimapEnabled) },
                overviewRulerBorder: false,
                renderLineHighlight: "line",
                scrollBeyondLastLine: false,
                tabSize: Number.isFinite(tabSize) ? tabSize : 2,
                wordWrap: editorSettings?.wordWrap ? "on" : "off",
                lineNumbers: editorSettings?.showLineNumbers === false ? "off" : "on",
              }}
            />
          </div>
        </>
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-[var(--fx-text-muted)]">
          Open a file from the explorer.
        </div>
      )}
    </section>
  );
}
