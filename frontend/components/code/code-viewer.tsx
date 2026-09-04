"use client";

import Editor, { loader, useMonaco, type Monaco } from "@monaco-editor/react";
import { Braces, ChevronRight, Copy, Save } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { EditorTabs } from "@/components/code/editor-tabs";
import { useEditorPreferences, type EditorPreferences } from "@/hooks/use-editor-settings";
import { useForgeXThemeVersion } from "@/lib/theme-context";
import type { EditorTab } from "@/types";
import type * as monaco from "monaco-editor";

const FORGEX_EDITOR_THEME = "forgex-runtime";
const AUTOSAVE_DELAY_MS = 800;
const FORMAT_COMMIT_DELAY_MS = 60;
const SAVE_DEDUPE_MS = 120;

let monacoModule: typeof import("monaco-editor") | null = null;
let monacoSetupPromise: Promise<void> | null = null;

function ensureMonacoSetup(): Promise<void> {
  if (!monacoSetupPromise) {
    monacoSetupPromise = (async () => {
      const mod = await import("monaco-editor");
      const workerFor = (label: string): Worker => {
        switch (label) {
          case "json":
            return new Worker(new URL("monaco-editor/esm/vs/language/json/json.worker", import.meta.url));
          case "css":
          case "scss":
          case "less":
            return new Worker(new URL("monaco-editor/esm/vs/language/css/css.worker", import.meta.url));
          case "html":
          case "handlebars":
          case "razor":
            return new Worker(new URL("monaco-editor/esm/vs/language/html/html.worker", import.meta.url));
          case "typescript":
          case "javascript":
            return new Worker(new URL("monaco-editor/esm/vs/language/typescript/ts.worker", import.meta.url));
          default:
            return new Worker(new URL("monaco-editor/esm/vs/editor/editor.worker", import.meta.url));
        }
      };
      (window as typeof window & { MonacoEnvironment?: monaco.Environment }).MonacoEnvironment = {
        getWorker: (_workerId: string, label: string) => workerFor(label),
      };
      loader.config({ monaco: mod });
      monacoModule = mod;
    })();
  }
  return monacoSetupPromise;
}

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

function defineForgeXEditorTheme(monacoInstance: Monaco) {
  const root = document.documentElement;
  const isLight = new Set(["porcelain", "paper", "dawn", "solarized-light", "high-contrast-light", "light"]).has(root.dataset.theme ?? "");
  const background = cssColor("--fx-bg", isLight ? "#f4f6f8" : "#0b1117");
  const surfaceOpacity = Math.min(100, Math.max(65, Number.parseFloat(getComputedStyle(root).getPropertyValue("--fx-surface-opacity")) || 94));
  const editorBackground = root.dataset.backgroundAsset ? alpha(background, Math.round(surfaceOpacity * 2.55).toString(16).padStart(2, "0")) : background;
  const panel = cssColor("--fx-panel", isLight ? "#ffffff" : "#111821");
  const border = cssColor("--fx-border", isLight ? "#b8c5d4" : "#243040");
  const foreground = cssColor("--fx-code-text", isLight ? "#1f2a3d" : "#d7dee8");
  const muted = cssColor("--fx-text-muted", isLight ? "#536173" : "#8b949e");
  const accent = cssColor("--fx-accent", "#7c5cff");
  const info = cssColor("--fx-info", "#38bdf8");
  const success = cssColor("--fx-success", "#22c55e");
  const warning = cssColor("--fx-warning", "#f59e0b");
  const error = cssColor("--fx-error", "#ef4444");
  const constant = cssColor("--fx-code-constant", "#d19a66");
  const tagColor = cssColor("--fx-code-tag", "#e06c75");
  const syntax = (color: string) => color.slice(1);

  monacoInstance.editor.defineTheme(FORGEX_EDITOR_THEME, {
    base: isLight ? "vs" : "vs-dark",
    inherit: true,
    rules: [
      { token: "", foreground: syntax(foreground) },
      { token: "comment", foreground: syntax(muted), fontStyle: "italic" },
      { token: "comment.doc", foreground: syntax(muted), fontStyle: "italic" },
      { token: "keyword", foreground: syntax(accent) },
      { token: "keyword.flow", foreground: syntax(accent), fontStyle: "bold" },
      { token: "operator", foreground: syntax(info) },
      { token: "delimiter", foreground: syntax(muted) },
      { token: "string", foreground: syntax(success) },
      { token: "string.escape", foreground: syntax(warning) },
      { token: "string.regex", foreground: syntax(constant) },
      { token: "number", foreground: syntax(constant) },
      { token: "number.hex", foreground: syntax(constant) },
      { token: "constant", foreground: syntax(constant) },
      { token: "type", foreground: syntax(warning) },
      { token: "type.identifier", foreground: syntax(warning) },
      { token: "class", foreground: syntax(warning) },
      { token: "struct", foreground: syntax(warning) },
      { token: "interface", foreground: syntax(warning), fontStyle: "italic" },
      { token: "function", foreground: syntax(info) },
      { token: "entity.name.function", foreground: syntax(info) },
      { token: "method", foreground: syntax(info) },
      { token: "variable", foreground: syntax(foreground) },
      { token: "variable.predefined", foreground: syntax(tagColor) },
      { token: "identifier", foreground: syntax(foreground) },
      { token: "tag", foreground: syntax(tagColor) },
      { token: "attribute.name", foreground: syntax(warning) },
      { token: "attribute.value", foreground: syntax(success) },
      { token: "metatag", foreground: syntax(accent) },
      { token: "regex", foreground: syntax(constant) },
      { token: "invalid", foreground: syntax(error), fontStyle: "underline" },
    ],
    colors: {
      "editor.background": editorBackground,
      "editor.foreground": foreground,
      "editorGutter.background": editorBackground,
      "editorLineNumber.foreground": alpha(muted, "A6"),
      "editorLineNumber.activeForeground": foreground,
      "editorCursor.foreground": accent,
      "editor.selectionBackground": alpha(accent, "45"),
      "editor.inactiveSelectionBackground": alpha(accent, "25"),
      "editor.selectionHighlightBackground": alpha(info, "30"),
      "editor.wordHighlightBackground": alpha(info, "26"),
      "editor.wordHighlightStrongBackground": alpha(info, "3D"),
      "editor.findMatchBackground": alpha(accent, "70"),
      "editor.findMatchHighlightBackground": alpha(warning, "3D"),
      "editor.findRangeHighlightBackground": alpha(accent, "1F"),
      "editor.lineHighlightBackground": alpha(panel, isLight ? "80" : "B8"),
      "editor.lineHighlightBorder": "#00000000",
      "editorIndentGuide.background1": alpha(border, "90"),
      "editorIndentGuide.background2": alpha(border, "60"),
      "editorIndentGuide.activeBackground1": accent,
      "editorIndentGuide.activeBackground2": alpha(accent, "B3"),
      "editorBracketMatch.background": alpha(accent, "2E"),
      "editorBracketMatch.border": alpha(accent, "80"),
      "editorBracketHighlight.foreground1": accent,
      "editorBracketHighlight.foreground2": info,
      "editorBracketHighlight.foreground3": success,
      "editorBracketHighlight.foreground4": warning,
      "editorBracketHighlight.foreground5": tagColor,
      "editorBracketHighlight.foreground6": muted,
      "editorWhitespace.foreground": alpha(muted, "52"),
      "editorWidget.background": panel,
      "editorWidget.border": border,
      "editorSuggestWidget.background": panel,
      "editorSuggestWidget.border": border,
      "editorSuggestWidget.selectedBackground": alpha(accent, "2F"),
      "editorHoverWidget.background": panel,
      "editorHoverWidget.border": border,
      "editorStickyScroll.background": alpha(panel, "E5"),
      "editorStickyScroll.border": border,
      "editorOverviewRuler.border": "#00000000",
      "minimap.background": background,
      "scrollbarSlider.background": alpha(border, "80"),
      "scrollbarSlider.hoverBackground": alpha(muted, "80"),
      "scrollbarSlider.activeBackground": alpha(accent, "80"),
    },
  });
}

const EXTENSION_LANGUAGES: Record<string, string> = {
  py: "python",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  json: "json",
  jsonc: "json",
  yaml: "yaml",
  yml: "yaml",
  html: "html",
  htm: "html",
  css: "css",
  scss: "scss",
  less: "less",
  md: "markdown",
  markdown: "markdown",
  cpp: "cpp",
  cc: "cpp",
  cxx: "cpp",
  c: "cpp",
  h: "cpp",
  hpp: "cpp",
  hh: "cpp",
  ino: "cpp",
  ini: "ini",
  cfg: "ini",
  conf: "ini",
  toml: "ini",
  xml: "xml",
  svg: "xml",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
};

function extensionOf(path: string) {
  const filename = path.split("/").at(-1)?.toLowerCase() ?? "";
  const dot = filename.lastIndexOf(".");
  return dot > 0 ? filename.slice(dot + 1) : "";
}

function languageFor(path: string, fallback?: string | null) {
  const mapped = EXTENSION_LANGUAGES[extensionOf(path)];
  if (mapped) return mapped;
  if (fallback && fallback !== "text") return fallback;
  return "plaintext";
}

const SKELETON_WIDTHS = ["92%", "78%", "85%", "64%", "88%", "72%", "81%", "56%", "90%", "68%", "74%"];

function EditorSkeleton() {
  return (
    <div aria-busy="true" className="flex h-full flex-col gap-4 p-8">
      <div className="h-4 w-1/3 animate-pulse rounded bg-[var(--fx-hover)]" />
      <div className="mt-2 flex flex-col gap-3">
        {SKELETON_WIDTHS.map((width, index) => (
          <div key={index} className="h-3 animate-pulse rounded bg-[var(--fx-hover)]" style={{ width }} />
        ))}
      </div>
    </div>
  );
}

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

export const CodeViewer = memo(function CodeViewer({
  tabs,
  activePath,
  onSelectTab,
  onCloseTab,
  onChange,
  onSave,
  editorSettings,
}: CodeViewerProps) {
  const monacoInstance = useMonaco();
  const [themeRevision, setThemeRevision] = useState(0);
  const [monacoReady, setMonacoReady] = useState(false);
  const themeVersion = useForgeXThemeVersion();

  const preferencesOverride = useMemo(
    () => ({
      fontSize: editorSettings?.fontSize,
      tabSize: editorSettings?.tabSize,
      wordWrap: editorSettings?.wordWrap,
      minimapEnabled: editorSettings?.minimapEnabled,
      showLineNumbers: editorSettings?.showLineNumbers,
    }),
    [
      editorSettings?.fontSize,
      editorSettings?.tabSize,
      editorSettings?.wordWrap,
      editorSettings?.minimapEnabled,
      editorSettings?.showLineNumbers,
    ],
  );
  const preferences = useEditorPreferences(preferencesOverride);

  const editorRef = useRef<monaco.editor.IStandaloneCodeEditor | null>(null);
  const tabsRef = useRef<EditorTab[]>(tabs);
  const preferencesRef = useRef<EditorPreferences>(preferences);
  const onSaveRef = useRef(onSave);
  const activePathRef = useRef<string | null>(activePath);
  const autosaveTimersRef = useRef<Record<string, number>>({});
  const lastSaveTickRef = useRef<Record<string, number>>({});

  useEffect(() => {
    tabsRef.current = tabs;
  }, [tabs]);
  useEffect(() => {
    preferencesRef.current = preferences;
  }, [preferences]);
  useEffect(() => {
    onSaveRef.current = onSave;
  }, [onSave]);
  useEffect(() => {
    activePathRef.current = activePath;
  }, [activePath]);

  useEffect(() => {
    setThemeRevision((value) => value + 1);
  }, [themeVersion]);

  useEffect(() => {
    const update = () => setThemeRevision((value) => value + 1);
    window.addEventListener("forgex-theme-change", update);
    return () => window.removeEventListener("forgex-theme-change", update);
  }, []);

  useEffect(() => {
    if (!monacoInstance) return;
    defineForgeXEditorTheme(monacoInstance);
    monacoInstance.editor.setTheme(FORGEX_EDITOR_THEME);
  }, [monacoInstance, themeRevision]);

  useEffect(() => {
    const timers = autosaveTimersRef.current;
    return () => {
      for (const id of Object.values(timers)) window.clearTimeout(id);
      autosaveTimersRef.current = {};
    };
  }, []);

  useEffect(() => {
    if (preferences.autoSave) return;
    const timers = autosaveTimersRef.current;
    for (const id of Object.values(timers)) window.clearTimeout(id);
    autosaveTimersRef.current = {};
  }, [preferences.autoSave]);

  const clearAutosaveTimer = useCallback((path: string) => {
    const timers = autosaveTimersRef.current;
    if (timers[path] !== undefined) {
      window.clearTimeout(timers[path]);
      delete timers[path];
    }
  }, []);

  const performSave = useCallback(async (targetPath?: string | null) => {
    const path = targetPath ?? activePathRef.current;
    if (!path) return;
    const tab = tabsRef.current.find((item) => item.path === path);
    if (!tab || !tab.dirty) {
      clearAutosaveTimer(path);
      return;
    }
    const now = Date.now();
    if (now - (lastSaveTickRef.current[path] ?? 0) < SAVE_DEDUPE_MS) return;
    lastSaveTickRef.current[path] = now;
    clearAutosaveTimer(path);
    if (preferencesRef.current.formatOnSave && path === activePathRef.current && editorRef.current) {
      await editorRef.current.getAction("editor.action.formatDocument")?.run();
      await new Promise<void>((resolve) => setTimeout(resolve, FORMAT_COMMIT_DELAY_MS));
    }
    onSaveRef.current(path);
  }, [clearAutosaveTimer]);

  const saveActiveRef = useRef<(targetPath?: string | null) => void>(() => undefined);
  useEffect(() => {
    saveActiveRef.current = (targetPath?: string | null) => void performSave(targetPath);
  }, [performSave]);

  const handleEditorMount = useCallback((instance: monaco.editor.IStandaloneCodeEditor) => {
    editorRef.current = instance;
    if (monacoModule) {
      instance.addCommand(monacoModule.KeyMod.CtrlCmd | monacoModule.KeyCode.KeyS, () => saveActiveRef.current());
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    void ensureMonacoSetup()
      .then(() => {
        if (mounted) setMonacoReady(true);
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveActiveRef.current();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const handleChange = useCallback(
    (path: string, content: string) => {
      onChange(path, content);
      if (!preferencesRef.current.autoSave) return;
      const timers = autosaveTimersRef.current;
      if (timers[path] !== undefined) window.clearTimeout(timers[path]);
      timers[path] = window.setTimeout(() => {
        delete timers[path];
        void performSave(path);
      }, AUTOSAVE_DELAY_MS);
    },
    [onChange, performSave],
  );

  const handleCloseTab = useCallback(
    (path: string) => {
      clearAutosaveTimer(path);
      onCloseTab(path);
    },
    [clearAutosaveTimer, onCloseTab],
  );

  const [order, setOrder] = useState<string[]>(() => tabs.map((tab) => tab.path));
  useEffect(() => {
    setOrder((current) => {
      if (current.length !== tabs.length) return tabs.map((tab) => tab.path);
      const known = new Set(current);
      return tabs.every((tab) => known.has(tab.path)) ? current : tabs.map((tab) => tab.path);
    });
  }, [tabs]);

  const orderedTabs = useMemo(() => {
    if (order.length !== tabs.length) return tabs;
    const byPath = new Map(tabs.map((tab) => [tab.path, tab] as const));
    const ordered = order.flatMap((path) => {
      const tab = byPath.get(path);
      return tab ? [tab] : [];
    });
    return ordered.length === tabs.length ? ordered : tabs;
  }, [order, tabs]);

  const handleReorder = useCallback(
    (fromPath: string, toIndex: number) => {
      setOrder((current) => {
        const base = current.length === tabs.length ? [...current] : tabs.map((tab) => tab.path);
        const from = base.indexOf(fromPath);
        if (from < 0) return current;
        const clamped = Math.max(0, Math.min(toIndex > from ? toIndex - 1 : toIndex, base.length - 1));
        base.splice(from, 1);
        base.splice(clamped, 0, fromPath);
        return base;
      });
    },
    [tabs],
  );

  const active = orderedTabs.find((tab) => tab.path === activePath) ?? orderedTabs[0] ?? null;
  const fontSize = Number(preferences.fontSize);
  const tabSize = Number(preferences.tabSize);
  const editorOptions = useMemo(() => ({
    automaticLayout: true,
    fontFamily: "'Cascadia Code', 'Cascadia Mono', Consolas, monospace",
    fontSize: Number.isFinite(fontSize) ? fontSize : 14,
    lineNumbersMinChars: 3,
    minimap: { enabled: Boolean(preferences.minimapEnabled) },
    overviewRulerBorder: false,
    renderLineHighlight: "line" as const,
    scrollBeyondLastLine: false,
    tabSize: Number.isFinite(tabSize) ? tabSize : 2,
    wordWrap: preferences.wordWrap ? ("on" as const) : ("off" as const),
    lineNumbers: preferences.showLineNumbers === false ? ("off" as const) : ("on" as const),
    smoothScrolling: true,
    cursorSmoothCaretAnimation: "on" as const,
    padding: { top: 10 },
    bracketPairColorization: { enabled: true },
    guides: {
      indentation: true,
      bracketPairs: true,
      highlightActiveIndentation: true,
      highlightActiveBracketPair: true,
    },
    stickyScroll: { enabled: true },
  }), [fontSize, preferences.minimapEnabled, preferences.showLineNumbers, preferences.wordWrap, tabSize]);

  const copy = async () => {
    if (!active) return;
    await navigator.clipboard.writeText(active.content);
  };

  const breadcrumbSegments = active ? active.path.split("/").filter(Boolean) : [];

  return (
    <section className="flex h-full min-h-0 flex-col bg-transparent">
      <div className="fx-editor-tabs flex h-9 items-center justify-between border-b border-[var(--fx-border)]">
        <EditorTabs
          tabs={orderedTabs}
          activePath={activePath}
          onSelectTab={onSelectTab}
          onCloseTab={handleCloseTab}
          onReorder={handleReorder}
        />
        <div className="flex shrink-0 items-center gap-1 px-2 text-[var(--fx-text-muted)]">
          <button
            className="rounded p-1.5 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] disabled:cursor-not-allowed disabled:opacity-40"
            title="Save (Ctrl+S)"
            aria-label="Save"
            disabled={!active || !active.dirty}
            onClick={() => void performSave(active?.path)}
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

      {active && preferences.showBreadcrumbs ? (
        <nav
          aria-label="Breadcrumb"
          className="flex h-7 shrink-0 items-center gap-1 overflow-hidden border-b border-[var(--fx-border-soft)] px-3 text-[11px] text-[var(--fx-text-muted)]"
        >
          <Braces className="h-3.5 w-3.5 shrink-0 text-[var(--fx-info)]" />
          {breadcrumbSegments.map((segment, index) => (
            <span key={`${segment}-${index}`} className="flex min-w-0 items-center gap-1">
              {index > 0 ? <ChevronRight className="h-3 w-3 shrink-0 opacity-60" /> : null}
              <span
                className={`truncate ${
                  index === breadcrumbSegments.length - 1 ? "text-[var(--fx-code-text)]" : ""
                }`}
              >
                {segment}
              </span>
            </span>
          ))}
          <span className="ml-auto shrink-0 pl-2 uppercase opacity-70">{languageFor(active.path, active.language)}</span>
        </nav>
      ) : null}

      {active && monacoReady ? (
        <div className="min-h-0 flex-1 overflow-hidden">
          <Editor
            path={active.path}
            value={active.content}
            language={languageFor(active.path, active.language)}
            theme={FORGEX_EDITOR_THEME}
            beforeMount={defineForgeXEditorTheme}
            onMount={handleEditorMount}
            loading={<EditorSkeleton />}
            onChange={(value) => handleChange(active.path, value ?? "")}
            options={editorOptions}
          />
        </div>
      ) : active ? (
        <div className="min-h-0 flex-1 overflow-hidden">
          <EditorSkeleton />
        </div>
      ) : (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full border border-[var(--fx-border-soft)] bg-[var(--fx-panel)]">
            <Braces className="h-6 w-6 text-[var(--fx-info)]" />
          </div>
          <div>
            <p className="text-sm font-medium text-[var(--fx-text)]">No files open</p>
            <p className="mt-1 max-w-xs text-xs leading-relaxed text-[var(--fx-text-muted)]">
              Pick a file from the Explorer tree to start editing. Your tabs and unsaved changes stay put while you browse.
            </p>
          </div>
          <p className="text-[11px] text-[var(--fx-text-muted)]">
            Tip: <kbd className="rounded border border-[var(--fx-border-soft)] px-1">Ctrl</kbd>
            {" + "}
            <kbd className="rounded border border-[var(--fx-border-soft)] px-1">S</kbd> saves instantly.
            {preferences.autoSave ? " Auto-save is on." : ""}
          </p>
        </div>
      )}
    </section>
  );
});
