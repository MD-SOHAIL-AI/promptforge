"use client";

import { Braces, Copy, FileCode2, Save, X } from "lucide-react";
import Editor from "@monaco-editor/react";

import type { EditorTab } from "@/types";

interface CodeViewerProps {
  tabs: EditorTab[];
  activePath?: string | null;
  onSelectTab: (path: string) => void;
  onCloseTab: (path: string) => void;
  onChange: (path: string, content: string) => void;
  onSave: (path: string) => void;
}

export function CodeViewer({
  tabs,
  activePath,
  onSelectTab,
  onCloseTab,
  onChange,
  onSave,
}: CodeViewerProps) {
  const active = tabs.find((tab) => tab.path === activePath) ?? tabs[0] ?? null;

  const copy = async () => {
    if (!active) return;
    await navigator.clipboard.writeText(active.content);
  };

  return (
    <section className="flex h-full min-h-[360px] flex-col bg-[#080c11]">
      <div className="flex min-h-11 items-center justify-between border-b border-white/10 bg-[#0c1117]">
        <div className="flex min-w-0 flex-1 overflow-x-auto">
          {tabs.length > 0 ? (
            tabs.map((tab) => (
              <button
                key={tab.path}
                className={`group flex h-11 max-w-[240px] items-center gap-2 border-r border-white/10 px-3 text-sm ${
                  active?.path === tab.path
                    ? "bg-[#080c11] text-white"
                    : "bg-[#111822] text-[#aab5c4] hover:bg-[#151e2a] hover:text-white"
                }`}
                onClick={() => onSelectTab(tab.path)}
              >
                <FileCode2 className="h-4 w-4 shrink-0 text-[#9cc7ff]" />
                <span className="min-w-0 truncate">{tab.path.split("/").at(-1)}</span>
                {tab.dirty ? <span className="h-2 w-2 shrink-0 rounded-full bg-[#f0b45b]" /> : null}
                <span
                  role="button"
                  tabIndex={0}
                  className="rounded p-0.5 opacity-60 hover:bg-white/10 hover:opacity-100"
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
              </button>
            ))
          ) : (
            <div className="flex h-11 items-center gap-2 px-3 text-sm text-[#7f8b99]">
              <Braces className="h-4 w-4" />
              No file open
            </div>
          )}
        </div>
        <div className="flex items-center gap-1 px-2 text-[#aab5c4]">
          <button
            className="rounded p-1.5 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40"
            title="Save"
            aria-label="Save"
            disabled={!active || !active.dirty}
            onClick={() => active && onSave(active.path)}
          >
            <Save className="h-4 w-4" />
          </button>
          <button
            className="rounded p-1.5 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40"
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
          <div className="flex h-9 items-center justify-between border-b border-white/10 px-4 text-xs text-[#7f8b99]">
            <span className="truncate">{active.path}</span>
            <span>{active.language}</span>
          </div>
          <div className="min-h-0 flex-1 overflow-hidden">
            <Editor
              key={active.path}
              path={active.path}
              value={active.content}
              language={active.language}
              theme="vs-dark"
              onChange={(value) => onChange(active.path, value ?? "")}
              options={{
                automaticLayout: true,
                fontFamily: "var(--font-geist-mono), Consolas, monospace",
                fontSize: 14,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                wordWrap: "off",
              }}
            />
          </div>
        </>
      ) : (
        <div className="flex flex-1 items-center justify-center text-sm text-[#7f8b99]">
          Open a file from the explorer.
        </div>
      )}
    </section>
  );
}
