"use client";

import { Check, Copy, WrapText } from "lucide-react";
import { isValidElement, memo, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

const CARET_CSS = `.fx-caret{display:inline-block;width:.55em;height:1.05em;margin-left:3px;border-radius:2px;background:var(--fx-accent);vertical-align:text-bottom;animation:fx-caret-blink 1.05s steps(2,start) infinite}@keyframes fx-caret-blink{50%{opacity:0}}`;

const remarkPlugins = [remarkGfm];

function nodeText(value: ReactNode): string {
  if (value === null || value === undefined || typeof value === "boolean") return "";
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return value.map(nodeText).join("");
  if (isValidElement(value)) return nodeText((value.props as { children?: ReactNode }).children);
  return "";
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);
  const [wrap, setWrap] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch { /* clipboard unavailable */ }
  };
  const actionClass = "grid h-6 w-6 place-items-center rounded-md text-[var(--fx-text-muted)] transition-colors hover:bg-[var(--fx-hover)] hover:text-[var(--fx-text)]";
  return (
    <div className="my-2 overflow-hidden rounded-[12px] border border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-panel)_82%,transparent)]">
      <div className="flex items-center justify-between gap-2 border-b border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-panel-elevated)_72%,transparent)] px-3 py-1">
        <span className="font-mono text-[9px] uppercase tracking-[.14em] text-[var(--fx-text-muted)]">{language || "code"}</span>
        <span className="flex items-center gap-0.5">
          <button type="button" onClick={() => setWrap((value) => !value)} className={`${actionClass} ${wrap ? "text-[var(--fx-accent)]" : ""}`} title={wrap ? "Disable wrap" : "Enable wrap"} aria-pressed={wrap} aria-label="Toggle code wrap"><WrapText className="h-3 w-3" /></button>
          <button type="button" onClick={() => void copy()} className={actionClass} title="Copy code" aria-label="Copy code">{copied ? <Check className="h-3 w-3 text-[var(--fx-success)]" /> : <Copy className="h-3 w-3" />}</button>
        </span>
      </div>
      <pre className={`m-0 overflow-x-auto px-3 py-2.5 font-mono text-[11px] leading-[1.6] text-[var(--fx-text)] ${wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"}`}><code>{code}</code></pre>
    </div>
  );
}

const markdownComponents: Components = {
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children }) => {
    const text = nodeText(children);
    const match = /language-([\w+#-]+)/.exec(className ?? "");
    if (match || text.includes("\n")) return <CodeBlock code={text.replace(/\n$/, "")} language={match?.[1] ?? ""} />;
    return <code className="rounded-[5px] border border-[var(--fx-border-soft)] bg-[color-mix(in_srgb,var(--fx-accent)_9%,transparent)] px-[5px] py-[1px] font-mono text-[11px] text-[var(--fx-text)]">{children}</code>;
  },
  p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
  a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer" className="text-[var(--fx-accent)] underline decoration-[color-mix(in_srgb,var(--fx-accent)_45%,transparent)] underline-offset-2 transition-colors hover:decoration-[var(--fx-accent)]">{children}</a>,
  strong: ({ children }) => <strong className="font-semibold text-[var(--fx-text)]">{children}</strong>,
  ul: ({ children }) => <ul className="my-2 list-disc space-y-1 pl-5 marker:text-[var(--fx-accent)]">{children}</ul>,
  ol: ({ children }) => <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-[var(--fx-text-muted)]">{children}</ol>,
  li: ({ children }) => <li className="pl-1 leading-[1.6]">{children}</li>,
  blockquote: ({ children }) => <blockquote className="my-2 border-l-2 border-[color-mix(in_srgb,var(--fx-accent)_46%,var(--fx-border))] pl-3 italic text-[var(--fx-text-muted)]">{children}</blockquote>,
  table: ({ children }) => <div className="my-2 overflow-x-auto rounded-[10px] border border-[var(--fx-border-soft)]"><table className="w-full border-collapse text-[11px]">{children}</table></div>,
  th: ({ children }) => <th className="border-b border-[var(--fx-border)] bg-[color-mix(in_srgb,var(--fx-panel-elevated)_70%,transparent)] px-2.5 py-1.5 text-left text-[9px] font-semibold uppercase tracking-[.12em] text-[var(--fx-text-muted)]">{children}</th>,
  td: ({ children }) => <td className="border-b border-[var(--fx-border-soft)] px-2.5 py-1.5 align-top text-[var(--fx-text)]">{children}</td>,
  h1: ({ children }) => <h1 className="mb-1.5 mt-3 text-[15px] font-semibold tracking-[-.02em] text-[var(--fx-text)]">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-1 mt-3 text-[13px] font-semibold tracking-[-.01em] text-[var(--fx-text)]">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1 mt-2.5 text-[12px] font-semibold text-[var(--fx-text)]">{children}</h3>,
  hr: () => <hr className="my-3 border-[var(--fx-border-soft)]" />,
};

export const MessageMarkdown = memo(function MessageMarkdown({ content, streaming = false, className = "" }: { content: string; streaming?: boolean; className?: string }) {
  return (
    <div className={`min-w-0 text-[12px] leading-[1.65] text-[var(--fx-text)] ${className}`}>
      <style>{CARET_CSS}</style>
      <ReactMarkdown remarkPlugins={remarkPlugins} components={markdownComponents}>{content}</ReactMarkdown>
      {streaming ? <span className="fx-caret" aria-hidden="true" /> : null}
    </div>
  );
});
