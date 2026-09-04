"use client";

import { ArrowRight, Check, CircleSlash2, Copy, FileDiff, Info } from "lucide-react";
import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/lib/utils";

const EST_ROW_H = 24;
const MAX_HEIGHT = 520;
const VIRTUAL_THRESHOLD = 400;
const MAX_PARSE_LINES = 60000;

type LineKind = "add" | "del" | "ctx";

interface DiffLine {
  kind: LineKind;
  oldNum: number | null;
  newNum: number | null;
  text: string;
  noNl: boolean;
}

interface DiffHunk {
  header: string;
  lines: DiffLine[];
  raw: string[];
}

interface DiffFile {
  path: string;
  oldPath: string | null;
  binary: boolean;
  additions: number;
  deletions: number;
  hunks: DiffHunk[];
  rawLines?: string[];
}

interface ParsedDiff {
  files: DiffFile[];
  truncated: boolean;
  additions: number;
  deletions: number;
}

type Row =
  | { k: "file"; fi: number }
  | { k: "hunk"; fi: number; hi: number }
  | { k: "line"; fi: number; hi: number; li: number }
  | { k: "raw"; fi: number; li: number }
  | { k: "note"; text: string };

const HUNK_RE = /^@@+ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: (.*))?$/;

function cleanPath(p: string): string {
  let s = p.trim();
  if (s.startsWith('"') && s.endsWith('"')) {
    try {
      s = JSON.parse(s) as string;
    } catch {
      s = s.slice(1, -1);
    }
  }
  if (s.startsWith("b/") || s.startsWith("a/")) s = s.slice(2);
  return s || "(unknown)";
}

function parseUnifiedDiff(input: string): ParsedDiff {
  const files: DiffFile[] = [];
  let src = input.replace(/\r\n?/g, "\n").split("\n");
  if (src.length > 0 && src[src.length - 1] === "") src.pop();
  const truncated = src.length > MAX_PARSE_LINES;
  if (truncated) src = src.slice(0, MAX_PARSE_LINES);

  let additions = 0;
  let deletions = 0;
  let file: DiffFile | null = null;
  let hunk: DiffHunk | null = null;
  let sawPair = false;
  let o = 0;
  let n = 0;

  const mkFile = (path: string | null): DiffFile => {
    const f: DiffFile = {
      path: path ?? "(patch)",
      oldPath: null,
      binary: false,
      additions: 0,
      deletions: 0,
      hunks: [],
    };
    files.push(f);
    hunk = null;
    return f;
  };

  for (const line of src) {
    const hm = hunk === null ? HUNK_RE.exec(line) : null;
    const structural = hunk === null || line.startsWith("diff --git ") || hm !== null;

    if (structural) {
      if (hm) {
        if (!file) file = mkFile(null);
        hunk = { header: line, lines: [], raw: [line] };
        file.hunks.push(hunk);
        o = Number(hm[1]);
        n = Number(hm[3]);
        continue;
      }
      hunk = null;
      if (line.startsWith("diff --git ")) {
        const body = line.slice(11);
        const idx = body.indexOf(" b/");
        file = mkFile(idx >= 0 ? cleanPath(body.slice(idx + 1)) : null);
        sawPair = false;
        continue;
      }
      if (line.startsWith("+++ ")) {
        if (!file || sawPair) file = mkFile(null);
        const p = line.slice(4);
        if (p !== "/dev/null") file.path = cleanPath(p);
        sawPair = true;
        continue;
      }
      if (line.startsWith("--- ")) {
        if (!file || sawPair) file = mkFile(null);
        const p = line.slice(4);
        file.oldPath = p === "/dev/null" ? null : cleanPath(p);
        continue;
      }
      if (file) {
        if (line.startsWith("rename from ")) file.oldPath = cleanPath(line.slice(12));
        else if (line.startsWith("rename to ")) file.path = cleanPath(line.slice(10));
        else if (/^Binary files .* differ$/.test(line) || line === "GIT binary patch") file.binary = true;
      }
      continue;
    }

    const h = hunk;
    const f = file;
    if (!h || !f) continue;
    h.raw.push(line);
    const tag = line.charAt(0);
    if (tag === "+") {
      additions++;
      f.additions++;
      h.lines.push({ kind: "add", oldNum: null, newNum: n++, text: line.slice(1), noNl: false });
    } else if (tag === "-") {
      deletions++;
      f.deletions++;
      h.lines.push({ kind: "del", oldNum: o++, newNum: null, text: line.slice(1), noNl: false });
    } else if (tag === " " || line === "") {
      h.lines.push({ kind: "ctx", oldNum: o++, newNum: n++, text: tag === " " ? line.slice(1) : "", noNl: false });
    } else if (tag === "\\") {
      const last = h.lines[h.lines.length - 1];
      if (last) last.noNl = true;
      h.raw.pop();
    } else {
      // Unknown prefix inside a hunk: preserve fidelity as context.
      h.lines.push({ kind: "ctx", oldNum: o++, newNum: n++, text: line, noNl: false });
    }
  }

  if (files.length === 0 && src.length > 0) {
    files.push({
      path: "(input)",
      oldPath: null,
      binary: false,
      additions: 0,
      deletions: 0,
      hunks: [],
      rawLines: src,
    });
  }
  return { files, truncated, additions, deletions };
}

function flatten(parsed: ParsedDiff): Row[] {
  const out: Row[] = [];
  parsed.files.forEach((f, fi) => {
    out.push({ k: "file", fi });
    if (f.rawLines) {
      f.rawLines.forEach((_, li) => out.push({ k: "raw", fi, li }));
      return;
    }
    f.hunks.forEach((hu, hi) => {
      out.push({ k: "hunk", fi, hi });
      hu.lines.forEach((_, li) => out.push({ k: "line", fi, hi, li }));
    });
    if (f.binary && f.hunks.length === 0) out.push({ k: "note", text: "Binary file not shown" });
  });
  if (parsed.truncated) out.push({ k: "note", text: `Diff truncated at ${MAX_PARSE_LINES.toLocaleString()} lines` });
  return out;
}

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:-9999px;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

function Chip({ tone, children }: { tone: "success" | "error" | "muted"; children: ReactNode }) {
  const color =
    tone === "success" ? "var(--fx-success)" : tone === "error" ? "var(--fx-error)" : "var(--fx-text-muted)";
  return (
    <span
      className="shrink-0 rounded-full px-2 py-px font-mono text-[12px] leading-[16px]"
      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      {children}
    </span>
  );
}

function CopyButton({ active, label, onCopy, reveal }: { active: boolean; label: string; onCopy: () => void; reveal?: boolean }) {
  return (
    <button
      type="button"
      aria-label={active ? "Copied" : label}
      onClick={onCopy}
      className={cn(
        "inline-flex h-6 shrink-0 cursor-pointer items-center gap-1 rounded-[var(--fx-radius-sm)] border px-1.5 font-mono text-[12px] leading-none transition-colors",
        active
          ? "border-[color-mix(in_srgb,var(--fx-success)_45%,transparent)] bg-[color-mix(in_srgb,var(--fx-success)_14%,transparent)] text-[var(--fx-success)]"
          : "border-transparent text-[var(--fx-text-muted)] hover:border-[var(--fx-border)] hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]",
        reveal && !active && "opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
      )}
    >
      {active ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      <span>{active ? "Copied" : label}</span>
    </button>
  );
}

const FileHeaderRow = memo(function FileHeaderRow({ file }: { file: DiffFile }) {
  const renamed = file.oldPath !== null && file.oldPath !== file.path;
  return (
    <div className="sticky top-0 z-20 flex h-7 w-max min-w-full items-center gap-2 border-b border-[var(--fx-border)] bg-[color-mix(in_srgb,var(--fx-panel)_94%,transparent)] pr-3 pl-3 backdrop-blur-md">
      <FileDiff className="h-3.5 w-3.5 shrink-0 text-[var(--fx-accent)]" />
      <span className="shrink-0 font-mono text-[12px] font-semibold text-[var(--fx-text)]">{file.path}</span>
      {renamed ? (
        <span className="hidden shrink-0 items-center gap-1 font-mono text-[12px] text-[var(--fx-text-muted)] sm:flex">
          <ArrowRight className="h-3 w-3" />
          {file.oldPath}
        </span>
      ) : null}
      <span className="min-w-4 flex-1" />
      {file.binary ? <Chip tone="muted">binary</Chip> : null}
      {file.additions > 0 ? <Chip tone="success">+{file.additions}</Chip> : null}
      {file.deletions > 0 ? <Chip tone="error">−{file.deletions}</Chip> : null}
    </div>
  );
});

const HunkHeaderRow = memo(function HunkHeaderRow({
  header,
  copied,
  onCopy,
}: {
  header: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <div
      className="group flex h-6 w-max min-w-full items-center gap-2 pr-2 pl-3 font-mono text-[12px] leading-6"
      style={{
        background: "var(--fx-accent-faint)",
        color: "color-mix(in srgb, var(--fx-accent) 70%, var(--fx-text-muted))",
      }}
    >
      <span className="whitespace-pre select-all">{header}</span>
      <span className="min-w-4 flex-1" />
      <CopyButton active={copied} label="Copy hunk" onCopy={onCopy} reveal />
    </div>
  );
});

const LineRow = memo(function LineRow({ line }: { line: DiffLine }) {
  const tint = line.kind === "add" ? "var(--fx-success)" : line.kind === "del" ? "var(--fx-error)" : null;
  return (
    <div
      className="flex h-6 w-max min-w-full items-stretch font-mono text-[12px] leading-6"
      style={
        tint
          ? {
              background: `color-mix(in srgb, ${tint} 14%, transparent)`,
              boxShadow: `inset 2px 0 0 ${tint}`,
              color: `color-mix(in srgb, ${tint} 58%, var(--fx-text))`,
            }
          : undefined
      }
    >
      <span className="w-11 shrink-0 pr-2 text-right tabular-nums text-[var(--fx-text-muted)] opacity-80 select-none">
        {line.oldNum ?? ""}
      </span>
      <span className="w-11 shrink-0 pr-2 text-right tabular-nums text-[var(--fx-text-muted)] opacity-80 select-none">
        {line.newNum ?? ""}
      </span>
      <span
        className="w-4 shrink-0 text-center select-none"
        style={{ color: tint ?? "var(--fx-text-muted)", opacity: tint ? 0.9 : 0.35 }}
      >
        {line.kind === "add" ? "+" : line.kind === "del" ? "−" : " "}
      </span>
      <span className="whitespace-pre pr-6">
        {line.text}
        {line.noNl ? (
          <span className="ml-2 opacity-60 select-none" title="\ No newline at end of file">
            ⏎
          </span>
        ) : null}
      </span>
    </div>
  );
});

const RawRow = memo(function RawRow({ text }: { text: string }) {
  return (
    <div className="h-6 w-max min-w-full whitespace-pre pr-6 pl-3 font-mono text-[12px] leading-6 text-[var(--fx-text-muted)]">
      {text || " "}
    </div>
  );
});

function NoteRow({ text }: { text: string }) {
  return (
    <div
      className="flex h-7 w-max min-w-full items-center gap-2 pr-3 pl-3 font-mono text-[12px] text-[var(--fx-text-muted)]"
      style={{ background: "var(--fx-accent-faint)" }}
    >
      <Info className="h-3 w-3 shrink-0" />
      <span className="truncate">{text}</span>
    </div>
  );
}

export function DiffView({ diff, className }: { diff: string; className?: string }) {
  const isEmpty = !diff || diff.trim().length === 0;
  const parsed = useMemo(() => (isEmpty ? null : parseUnifiedDiff(diff)), [diff, isEmpty]);
  const rows = useMemo(() => (parsed ? flatten(parsed) : []), [parsed]);
  const virtual = rows.length > VIRTUAL_THRESHOLD;

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => EST_ROW_H,
    overscan: 14,
    enabled: virtual,
  });

  const [copied, setCopied] = useState<string | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => void (copyTimer.current && clearTimeout(copyTimer.current)), []);

  const copy = useCallback((key: string, text: string) => {
    void copyText(text).then(() => {
      setCopied(key);
      if (copyTimer.current) clearTimeout(copyTimer.current);
      copyTimer.current = setTimeout(() => setCopied(null), 1500);
    });
  }, []);

  const renderRow = useCallback(
    (row: Row): ReactNode => {
      if (!parsed) return null;
      switch (row.k) {
        case "file":
          return <FileHeaderRow file={parsed.files[row.fi]} />;
        case "hunk": {
          const hu = parsed.files[row.fi].hunks[row.hi];
          const key = `h${row.fi}.${row.hi}`;
          return <HunkHeaderRow header={hu.header} copied={copied === key} onCopy={() => copy(key, hu.raw.join("\n"))} />;
        }
        case "line":
          return <LineRow line={parsed.files[row.fi].hunks[row.hi].lines[row.li]} />;
        case "raw":
          return <RawRow text={parsed.files[row.fi].rawLines?.[row.li] ?? ""} />;
        case "note":
          return <NoteRow text={row.text} />;
        default:
          return null;
      }
    },
    [parsed, copied, copy]
  );

  if (isEmpty || !parsed) {
    return (
      <div
        className={cn(
          "rounded-[var(--fx-radius)] border border-[var(--fx-border)] bg-[color-mix(in_srgb,var(--fx-panel)_72%,transparent)]",
          className
        )}
      >
        <div className="flex items-center gap-2.5 px-4 py-7 text-[var(--fx-text-muted)]">
          <CircleSlash2 className="h-4 w-4 opacity-70" />
          <span className="font-mono text-[12px]">No changes</span>
        </div>
      </div>
    );
  }

  const rowKey = (row: Row, i: number): string => {
    switch (row.k) {
      case "file":
        return `file-${row.fi}`;
      case "hunk":
        return `hunk-${row.fi}.${row.hi}`;
      case "line":
        return `line-${row.fi}.${row.hi}.${row.li}`;
      case "raw":
        return `raw-${row.fi}.${row.li}`;
      default:
        return `note-${i}`;
    }
  };

  return (
    <div
      className={cn(
        "overflow-hidden rounded-[var(--fx-radius)] border border-[var(--fx-border)] bg-[color-mix(in_srgb,var(--fx-panel)_72%,transparent)]",
        className
      )}
    >
      <div className="flex items-center gap-2 border-b border-[var(--fx-border)] bg-[color-mix(in_srgb,var(--fx-panel-elevated)_88%,transparent)] px-3 py-1.5">
        <FileDiff className="h-3.5 w-3.5 shrink-0 text-[var(--fx-accent)]" />
        <span className="shrink-0 rounded-full border border-[var(--fx-border)] bg-[var(--fx-accent-faint)] px-2 py-px font-mono text-[12px] leading-[16px] text-[var(--fx-text)]">
          {parsed.files.length} changed
        </span>
        {parsed.additions > 0 ? <Chip tone="success">+{parsed.additions}</Chip> : null}
        {parsed.deletions > 0 ? <Chip tone="error">−{parsed.deletions}</Chip> : null}
        <span className="min-w-4 flex-1" />
        <CopyButton active={copied === "all"} label="Copy diff" onCopy={() => copy("all", diff)} />
      </div>

      <div ref={scrollRef} className="overscroll-contain overflow-auto" style={{ maxHeight: MAX_HEIGHT }}>
        {virtual ? (
          <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
            {virtualizer.getVirtualItems().map((vi) => (
              <div
                key={rowKey(rows[vi.index], vi.index)}
                data-index={vi.index}
                ref={virtualizer.measureElement}
                className="absolute top-0 left-0 w-full"
                style={{ transform: `translateY(${vi.start}px)` }}
              >
                {renderRow(rows[vi.index])}
              </div>
            ))}
          </div>
        ) : (
          <div className="w-max min-w-full">
            {rows.map((row, i) => (
              <div key={rowKey(row, i)}>{renderRow(row)}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
