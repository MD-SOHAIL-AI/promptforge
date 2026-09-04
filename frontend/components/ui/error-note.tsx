"use client";

import { AlertTriangle, RotateCw } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const toneStyles = {
  error: {
    icon: AlertTriangle,
    strip: "border-l-[var(--fx-error)]",
    surface: "bg-[var(--fx-error-soft)]",
    accentText: "text-[var(--fx-error)]",
  },
  warning: {
    icon: AlertTriangle,
    strip: "border-l-[var(--fx-warning)]",
    surface: "bg-[var(--fx-warning-soft)]",
    accentText: "text-[var(--fx-warning)]",
  },
} as const;

export interface ErrorNoteProps {
  tone?: "error" | "warning";
  message: ReactNode;
  onRetry?: () => void;
  action?: ReactNode;
  className?: string;
}

export function ErrorNote({ tone = "error", message, onRetry, action, className }: ErrorNoteProps) {
  const styles = toneStyles[tone];
  const Icon = styles.icon;
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn(
        "flex items-start gap-2.5 rounded-[var(--fx-radius-sm)] border border-[var(--fx-border-soft)] border-l-2 px-3 py-2",
        styles.strip,
        styles.surface,
        className,
      )}
    >
      <Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", styles.accentText)} />
      <p className="min-w-0 flex-1 text-xs leading-5 text-[var(--fx-text)]">{message}</p>
      {onRetry ? (
        <button
          type="button"
          onClick={onRetry}
          className={cn(
            "inline-flex h-6 shrink-0 items-center gap-1 rounded-[var(--fx-radius-sm)] px-1.5 text-[11px] font-medium outline-none transition-colors hover:bg-[color-mix(in_srgb,var(--fx-hover)_82%,transparent)] focus-visible:ring-2 focus-visible:ring-[var(--fx-accent)]",
            styles.accentText,
          )}
        >
          <RotateCw className="h-3 w-3" />
          Retry
        </button>
      ) : null}
      {action ? <div className="shrink-0">{action}</div> : null}
    </div>
  );
}
