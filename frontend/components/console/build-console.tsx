"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronUp, CircleSlash2, Search, Trash2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn, formatLogTimestamp } from "@/lib/utils";
import type { ConsoleEntry } from "@/types";

const tabs = ["Output", "PlatformIO", "Execution", "Serial Monitor"] as const;

export function BuildConsole({
  logs,
  collapsed,
  onToggle,
  onClear,
}: {
  logs: ConsoleEntry[];
  collapsed: boolean;
  onToggle: () => void;
  onClear: () => void;
}) {
  const [activeTab, setActiveTab] = useState<(typeof tabs)[number]>("Output");
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => {
    let entries = logs;
    if (activeTab === "PlatformIO") entries = entries.filter((item) => item.channel === "build");
    if (activeTab === "Execution") entries = entries.filter((item) => ["workflow", "error"].includes(item.channel));
    if (activeTab === "Serial Monitor") entries = entries.filter((item) => item.channel === "serial");
    if (query) entries = entries.filter((item) => item.message.toLowerCase().includes(query.toLowerCase()));
    return entries;
  }, [activeTab, logs, query]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [filtered]);

  return (
    <section className="flex h-full min-h-0 flex-col border-t border-border bg-[#111419]">
      <div className="flex h-8 shrink-0 items-center justify-between border-b border-border bg-[#181b20] px-1.5">
        <div className="flex h-full items-center">
          {tabs.map((tab) => (
            <button
              key={tab}
              onClick={() => { setActiveTab(tab); if (collapsed) onToggle(); }}
              className={cn(
                "relative h-full px-2.5 text-[10px] uppercase tracking-[0.08em] text-[#7d8590] hover:text-[#c9d1d9]",
                activeTab === tab && "text-[#e6edf3]",
              )}
            >
              {tab}
              {activeTab === tab && <span className="absolute inset-x-2 bottom-0 h-px bg-[#e86f3d]" />}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-0.5">
          {searchOpen && (
            <div className="mr-1 flex h-6 items-center rounded-[3px] border border-border bg-[#111419] px-1.5">
              <Search className="mr-1 h-3 w-3 text-[#6e7681]" />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter output" className="w-28 bg-transparent font-mono text-[10px] text-[#c9d1d9] outline-none" autoFocus />
              <button onClick={() => { setQuery(""); setSearchOpen(false); }}><X className="h-3 w-3 text-[#6e7681]" /></button>
            </div>
          )}
          <Button variant="ghost" size="icon" className="h-6 w-6 text-muted-foreground" onClick={() => setSearchOpen((value) => !value)} aria-label="Search logs"><Search className="h-3 w-3" /></Button>
          <Button variant="ghost" size="icon" className="h-6 w-6 text-muted-foreground" onClick={onClear} aria-label="Clear logs"><Trash2 className="h-3 w-3" /></Button>
          <Button variant="ghost" size="icon" className="h-6 w-6 text-muted-foreground" onClick={onToggle} aria-label={collapsed ? "Expand console" : "Collapse console"}>
            {collapsed ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
          </Button>
        </div>
      </div>
      {!collapsed && (
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto px-2.5 py-1.5 font-mono text-[10px] leading-[17px]">
          {filtered.length ? filtered.map((log) => (
            <div key={log.id} className="flex min-w-max gap-2 hover:bg-[#171b21]">
              <span className="select-none text-[#484f58]">{formatLogTimestamp(log.timestamp)}</span>
              <span className={cn(
                "w-[62px] select-none uppercase",
                log.channel === "error" && "text-[#ff7b72]",
                log.channel === "build" && "text-[#d29922]",
                log.channel === "workflow" && "text-[#58a6ff]",
                log.channel === "system" && "text-[#8b949e]",
                log.channel === "serial" && "text-[#3fb950]",
              )}>{log.channel}</span>
              <span className={log.channel === "error" ? "text-[#ffa198]" : "text-[#b8c0ca]"}>{log.message}</span>
            </div>
          )) : (
            <div className="flex h-full items-center justify-center gap-2 text-[#59616b]"><CircleSlash2 className="h-4 w-4" /> No output for this channel</div>
          )}
        </div>
      )}
    </section>
  );
}
