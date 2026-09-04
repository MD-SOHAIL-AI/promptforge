"use client";

import { Copy, FileCode2, MoreHorizontal, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";

import type { EditorTab } from "@/types";

const OVERFLOW_RESERVE = 44;

interface MenuPosition {
  x: number;
  y: number;
  path: string;
}

interface EditorTabsProps {
  tabs: EditorTab[];
  activePath?: string | null;
  onSelectTab: (path: string) => void;
  onCloseTab: (path: string) => void;
  onReorder: (fromPath: string, toIndex: number) => void;
}

function filenameOf(path: string) {
  return path.split("/").at(-1) ?? path;
}

function MenuItem({
  label,
  icon,
  disabled,
  onClick,
}: {
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[var(--fx-text)] transition-colors hover:bg-[var(--fx-hover)] disabled:cursor-not-allowed disabled:opacity-40"
      onClick={onClick}
    >
      {icon}
      {label}
    </button>
  );
}

export function EditorTabs({ tabs, activePath, onSelectTab, onCloseTab, onReorder }: EditorTabsProps) {
  const stripRef = useRef<HTMLDivElement>(null);
  const dragPathRef = useRef<string | null>(null);
  const [dropIndex, setDropIndex] = useState<number | null>(null);
  const [visibleCount, setVisibleCount] = useState<number | null>(null);
  const [overflowing, setOverflowing] = useState(false);
  const [contextMenu, setContextMenu] = useState<MenuPosition | null>(null);
  const [listOpen, setListOpen] = useState(false);

  useEffect(() => {
    if (!contextMenu && !listOpen) return;
    const dismiss = () => {
      setContextMenu(null);
      setListOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
    };
    window.addEventListener("mousedown", dismiss);
    window.addEventListener("resize", dismiss);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("mousedown", dismiss);
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [contextMenu, listOpen]);

  useLayoutEffect(() => {
    const strip = stripRef.current;
    if (!strip || typeof ResizeObserver === "undefined") return;
    const evaluate = () => {
      const items = Array.from(strip.querySelectorAll<HTMLElement>("[data-tab-id]"));
      if (items.length === 0) {
        setOverflowing(false);
        setVisibleCount(null);
        return;
      }
      const limit = strip.clientWidth - OVERFLOW_RESERVE;
      let fit = items.length;
      for (let index = 0; index < items.length; index += 1) {
        const item = items[index];
        if (item.offsetLeft + item.offsetWidth > limit) {
          fit = index;
          break;
        }
      }
      const nextOverflowing = fit < items.length;
      setOverflowing(nextOverflowing);
      setVisibleCount(nextOverflowing ? Math.max(1, fit) : null);
    };
    evaluate();
    const observer = new ResizeObserver(evaluate);
    observer.observe(strip);
    return () => observer.disconnect();
  }, [tabs]);

  const openContextMenu = useCallback((event: ReactMouseEvent, path: string) => {
    event.preventDefault();
    setContextMenu({
      x: Math.min(event.clientX, window.innerWidth - 210),
      y: Math.min(event.clientY, window.innerHeight - 200),
      path,
    });
  }, []);

  const handleDrop = useCallback(
    (targetIndex: number) => {
      const fromPath = dragPathRef.current;
      dragPathRef.current = null;
      setDropIndex(null);
      if (fromPath) onReorder(fromPath, targetIndex);
    },
    [onReorder],
  );

  const hiddenFrom = visibleCount ?? tabs.length;
  const contextTabIndex = contextMenu ? tabs.findIndex((tab) => tab.path === contextMenu.path) : -1;

  return (
    <div className="relative flex min-w-0 flex-1 items-stretch">
      <div ref={stripRef} role="tablist" className="flex h-full min-w-0 flex-1 items-stretch overflow-x-hidden">
        {tabs.length > 0 ? (
          tabs.map((tab, index) => {
            const isActive = activePath === tab.path;
            const isHidden = index >= hiddenFrom;
            return (
              <div
                key={tab.path}
                data-tab-id={tab.path}
                role="tab"
                aria-selected={isActive}
                tabIndex={0}
                draggable
                onDragStart={(event) => {
                  dragPathRef.current = tab.path;
                  event.dataTransfer.setData("text/plain", tab.path);
                  event.dataTransfer.effectAllowed = "move";
                }}
                onDragEnd={() => {
                  dragPathRef.current = null;
                  setDropIndex(null);
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  const rect = event.currentTarget.getBoundingClientRect();
                  const insertBefore = event.clientX < rect.left + rect.width / 2;
                  setDropIndex(index + (insertBefore ? 0 : 1));
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  handleDrop(dropIndex ?? index);
                }}
                onMouseDown={(event) => {
                  if (event.button === 1) {
                    event.preventDefault();
                    onCloseTab(tab.path);
                  }
                }}
                onContextMenu={(event) => openContextMenu(event, tab.path)}
                onClick={() => onSelectTab(tab.path)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelectTab(tab.path);
                  }
                }}
                className={`group relative flex h-9 max-w-[220px] shrink-0 cursor-pointer select-none items-center gap-2 border-r border-[var(--fx-border-soft)] px-3 text-xs outline-none transition-colors focus-visible:bg-[var(--fx-hover)] ${
                  isHidden ? "invisible" : ""
                } ${
                  isActive
                    ? "bg-[var(--fx-bg)] text-[var(--fx-text)]"
                    : "bg-[var(--fx-panel)] text-[var(--fx-text-muted)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]"
                }`}
              >
                {dropIndex === index ? (
                  <span className="absolute inset-y-0 left-0 w-0.5 bg-[var(--fx-accent)]" />
                ) : null}
                {dropIndex === index + 1 && index === tabs.length - 1 ? (
                  <span className="absolute inset-y-0 right-0 w-0.5 bg-[var(--fx-accent)]" />
                ) : null}
                <FileCode2 className="h-4 w-4 shrink-0 text-[var(--fx-info)]" />
                <span className="min-w-0 truncate">{filenameOf(tab.path)}</span>
                <span className="relative flex h-4 w-4 shrink-0 items-center justify-center">
                  {tab.dirty ? (
                    <span className="h-2 w-2 rounded-full bg-[var(--fx-warning)] group-hover:hidden" />
                  ) : null}
                  <button
                    type="button"
                    tabIndex={-1}
                    aria-label={`Close ${filenameOf(tab.path)}`}
                    className={`rounded p-0.5 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] ${
                      tab.dirty ? "hidden group-hover:block" : "opacity-0 group-hover:opacity-100"
                    }`}
                    onMouseDown={(event) => event.stopPropagation()}
                    onClick={(event) => {
                      event.stopPropagation();
                      onCloseTab(tab.path);
                    }}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </span>
                {isActive ? <span className="absolute inset-x-0 top-0 h-0.5 bg-[var(--fx-accent)]" /> : null}
              </div>
            );
          })
        ) : (
          <div className="flex h-9 items-center gap-2 px-3 text-xs text-[var(--fx-text-muted)]">
            <FileCode2 className="h-4 w-4" />
            No file open
          </div>
        )}
      </div>

      {overflowing ? (
        <button
          type="button"
          aria-label="More tabs"
          className={`absolute right-0 top-0 flex h-9 w-9 items-center justify-center border-l border-[var(--fx-border-soft)] bg-[var(--fx-panel)] text-[var(--fx-text-muted)] hover:text-[var(--fx-text)] ${
            listOpen ? "text-[var(--fx-text)]" : ""
          }`}
          onMouseDown={(event) => event.stopPropagation()}
          onClick={() => setListOpen((open) => !open)}
        >
          <MoreHorizontal className="h-4 w-4" />
        </button>
      ) : null}

      {listOpen && overflowing ? (
        <div
          className="absolute right-2 top-full z-50 mt-1 max-h-72 min-w-[200px] overflow-y-auto rounded-md border border-[var(--fx-border)] bg-[var(--fx-panel)] py-1 shadow-xl"
          onMouseDown={(event) => event.stopPropagation()}
        >
          {tabs.slice(hiddenFrom).map((tab) => (
            <div key={tab.path} className="group flex items-center">
              <button
                type="button"
                className={`flex min-w-0 flex-1 items-center gap-2 px-3 py-1.5 text-left text-xs ${
                  activePath === tab.path ? "text-[var(--fx-text)]" : "text-[var(--fx-text-muted)] hover:text-[var(--fx-text)]"
                }`}
                onClick={() => {
                  onSelectTab(tab.path);
                  setListOpen(false);
                }}
              >
                <span className="min-w-0 truncate">{filenameOf(tab.path)}</span>
                {tab.dirty ? <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--fx-warning)]" /> : null}
              </button>
              <button
                type="button"
                aria-label={`Close ${filenameOf(tab.path)}`}
                className="mr-1 rounded p-1 text-[var(--fx-text-muted)] opacity-0 hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)] group-hover:opacity-100"
                onClick={(event) => {
                  event.stopPropagation();
                  onCloseTab(tab.path);
                }}
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          ))}
        </div>
      ) : null}

      {contextMenu && contextTabIndex >= 0 ? (
        <div
          role="menu"
          className="fixed z-50 min-w-[190px] overflow-hidden rounded-md border border-[var(--fx-border)] bg-[var(--fx-panel)] py-1 text-xs shadow-xl"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onMouseDown={(event) => event.stopPropagation()}
        >
          <MenuItem label="Close" onClick={() => { onCloseTab(contextMenu.path); setContextMenu(null); }} />
          <MenuItem
            label="Close Others"
            disabled={tabs.length <= 1}
            onClick={() => {
              for (const tab of tabs) {
                if (tab.path !== contextMenu.path) onCloseTab(tab.path);
              }
              setContextMenu(null);
            }}
          />
          <MenuItem
            label="Close Right"
            disabled={contextTabIndex >= tabs.length - 1}
            onClick={() => {
              for (const tab of tabs.slice(contextTabIndex + 1)) onCloseTab(tab.path);
              setContextMenu(null);
            }}
          />
          <div className="my-1 h-px bg-[var(--fx-border-soft)]" />
          <MenuItem
            label="Copy Path"
            icon={<Copy className="h-3.5 w-3.5 opacity-70" />}
            onClick={() => {
              navigator.clipboard.writeText(contextMenu.path).catch(() => undefined);
              setContextMenu(null);
            }}
          />
        </div>
      ) : null}
    </div>
  );
}
